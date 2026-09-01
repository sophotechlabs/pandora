import datetime

import pytest
from django.utils import timezone

from pandora.core import models as core_models
from pandora.ingest import models as ingest_models
from pandora.ingest import processor
from pandora.issues import models as issue_models
from tests.ingest import fakes, helpers

SENTRY_ID = "b" * 32


def event_payload(**overrides):
    payload = {
        "event_id": SENTRY_ID,
        "level": "error",
        "platform": "python",
        "exception": {
            "values": [
                {"type": "ValueError", "value": "bad input", "module": "app.views"}
            ]
        },
    }
    payload.update(overrides)
    return payload


def store_event(project, payload=None, received_at=None, environment=""):
    if payload is None:
        payload = event_payload()
    envelope = ingest_models.RawEnvelope(
        project=project,
        source=core_models.TokenSource.SDK,
        environment=environment,
        payload=payload,
    )
    if received_at is not None:
        envelope.received_at = received_at
    envelope.save()
    return envelope


def deliver(project, payload=None, store=None, received_at=None, environment=""):
    if store is None:
        store = fakes.RecordingEventStore()
    envelope = store_event(project, payload, received_at, environment)
    processor.process_envelope(envelope.pk, store=store)
    envelope.refresh_from_db()
    return envelope, store


# first event


@pytest.mark.django_db
def test_a_first_sdk_event_opens_an_issue(project):
    """Should group an SDK event into an issue like any other occurrence."""
    deliver(project)

    issue = issue_models.Issue.objects.get()
    result = (issue.title, issue.culprit, issue.level, issue.event_count)
    expected = ("ValueError", "", issue_models.Level.ERROR, 1)
    assert result == expected


@pytest.mark.django_db
def test_an_sdk_event_creates_no_episode(project):
    """Should keep episodes an Alertmanager concept, as the plan pins it."""
    deliver(project)

    result = issue_models.Episode.objects.count()
    expected = 0
    assert result == expected


@pytest.mark.django_db
def test_an_sdk_issue_has_no_source_state(project):
    """Should leave the firing/resolved column null — it is episode-derived."""
    deliver(project)

    issue = issue_models.Issue.objects.get()
    result = (issue.source_state, issue.open_episode_count)
    expected = (None, 0)
    assert result == expected


@pytest.mark.django_db
def test_the_envelope_finishes_done(project):
    """Should mark the inbox row consumed so prune can reclaim it."""
    envelope, _ = deliver(project)

    result = (envelope.state, envelope.error)
    expected = (ingest_models.EnvelopeState.DONE, "")
    assert result == expected


@pytest.mark.django_db
def test_the_event_blob_is_written_to_the_store(project):
    """Should keep the payload where the events endpoint reads it."""
    _, store = deliver(project)

    row = store.rows[0]
    result = (row.source, row.episode_id, row.extra["event_id"], row.message)
    expected = ("sdk", None, SENTRY_ID, "ValueError: bad input")
    assert result == expected


@pytest.mark.django_db
def test_the_event_is_linked_to_its_issue(project):
    """Should let the events endpoint find the blob from the issue."""
    _, store = deliver(project)

    issue = issue_models.Issue.objects.get()
    result = store.rows[0].issue_id
    expected = issue.pk
    assert result == expected


@pytest.mark.django_db
def test_the_creation_is_recorded(project):
    """Should show the issue's birth in the activity feed."""
    deliver(project)

    result = list(issue_models.IssueActivity.objects.values_list("kind", flat=True))
    expected = ["created"]
    assert result == expected


@pytest.mark.django_db
def test_the_aggregates_are_maintained_at_write_time(project):
    """Should fill the sparkline and tag bars as events land."""
    deliver(project, event_payload(tags={"release": "1.2.3"}))

    result = (
        issue_models.HourlyStat.objects.count(),
        list(issue_models.TagStat.objects.values_list("key", "value", "count")),
    )
    expected = (1, [("release", "1.2.3", 1)])
    assert result == expected


# dedup


@pytest.mark.django_db
def test_the_same_event_id_twice_counts_once(project):
    """Should make an SDK retry harmless — the id is the dedup key."""
    deliver(project)
    deliver(project)

    issue = issue_models.Issue.objects.get()
    result = (issue.event_count, ingest_models.ProcessedEvent.objects.count())
    expected = (1, 1)
    assert result == expected


@pytest.mark.django_db
def test_a_replayed_event_writes_no_second_blob(project):
    """Should not duplicate the payload in the store on a retry."""
    store = fakes.RecordingEventStore()
    deliver(project, store=store)
    deliver(project, store=store)

    result = len(store.rows)
    expected = 1
    assert result == expected


@pytest.mark.django_db
def test_a_replayed_event_still_finishes_done(project):
    """Should not fail an envelope just because its event was seen already."""
    deliver(project)
    envelope, _ = deliver(project)

    result = envelope.state
    expected = ingest_models.EnvelopeState.DONE
    assert result == expected


@pytest.mark.django_db
def test_two_different_events_both_count(project):
    """Should count distinct ids separately in the same issue."""
    deliver(project)
    deliver(project, event_payload(event_id="c" * 32))

    issue = issue_models.Issue.objects.get()
    result = issue.event_count
    expected = 2
    assert result == expected


@pytest.mark.django_db
def test_the_same_id_in_two_projects_is_two_events(project):
    """Should scope dedup to the project, as the unique constraint does."""
    other = core_models.Project.objects.create(slug="other", name="Other")
    deliver(project)
    deliver(other)

    result = issue_models.Issue.objects.count()
    expected = 2
    assert result == expected


# grouping over time


@pytest.mark.django_db
def test_two_events_with_the_same_fingerprint_share_an_issue(project):
    """Should group by the derived fingerprint, not by the event id."""
    deliver(project)
    deliver(project, event_payload(event_id="c" * 32, level="warning"))

    result = issue_models.Issue.objects.count()
    expected = 1
    assert result == expected


@pytest.mark.django_db
def test_a_different_exception_opens_a_second_issue(project):
    """Should keep unrelated failures apart."""
    deliver(project)
    payload = event_payload(
        event_id="c" * 32,
        exception={"values": [{"type": "KeyError", "value": "x", "module": "app"}]},
    )
    deliver(project, payload)

    result = issue_models.Issue.objects.count()
    expected = 2
    assert result == expected


@pytest.mark.django_db
def test_last_seen_moves_forward_with_arrival(project):
    """Should track recency from when pandora saw the event."""
    first = timezone.now() - datetime.timedelta(hours=2)
    later = timezone.now()
    deliver(project, received_at=first)
    deliver(project, event_payload(event_id="c" * 32), received_at=later)

    issue = issue_models.Issue.objects.get()
    result = issue.last_seen
    expected = later
    assert result == expected


@pytest.mark.django_db
def test_an_event_that_arrives_late_does_not_rewind_last_seen(project):
    """Should not move recency backwards for a delayed delivery."""
    recent = timezone.now()
    stale = recent - datetime.timedelta(hours=3)
    deliver(project, received_at=recent)
    deliver(project, event_payload(event_id="c" * 32), received_at=stale)

    issue = issue_models.Issue.objects.get()
    result = issue.last_seen
    expected = recent
    assert result == expected


@pytest.mark.django_db
def test_an_older_event_pulls_first_seen_back(project):
    """Should widen the issue's window when an older event turns up."""
    now = timezone.now()
    older = now - datetime.timedelta(days=1)
    deliver(project, event_payload(timestamp=now.isoformat()))
    deliver(
        project,
        event_payload(event_id="c" * 32, timestamp=older.isoformat()),
    )

    issue = issue_models.Issue.objects.get()
    result = issue.first_seen.replace(microsecond=0)
    expected = older.replace(microsecond=0)
    assert result == expected


@pytest.mark.django_db
def test_an_out_of_range_event_timestamp_falls_back_to_arrival(project):
    received_at = timezone.now()

    deliver(project, event_payload(timestamp=1e100), received_at=received_at)

    issue = issue_models.Issue.objects.get()
    assert issue.first_seen == received_at


# the issue follows its latest event


@pytest.mark.django_db
def test_a_later_event_moves_the_level(project):
    """Should show what the issue is doing now, not what it did first."""
    deliver(project, event_payload(level="warning"))

    deliver(project, event_payload(event_id="c" * 32, level="fatal"))

    issue = issue_models.Issue.objects.get()
    result = issue.level
    expected = issue_models.Level.FATAL
    assert result == expected


@pytest.mark.django_db
def test_a_later_event_rewrites_the_title_and_culprit(project):
    """Should refresh the text a client-side fingerprint holds together."""
    deliver(project, event_payload(fingerprint=["checkout"]))
    payload = event_payload(
        event_id="c" * 32,
        fingerprint=["checkout"],
        exception={
            "values": [
                {
                    "type": "TimeoutError",
                    "value": "gateway did not answer",
                    "module": "app.pay",
                    "stacktrace": {
                        "frames": [
                            {"module": "app.pay", "function": "charge", "in_app": True}
                        ]
                    },
                }
            ]
        },
    )

    deliver(project, payload)

    issue = issue_models.Issue.objects.get()
    result = (issue.title, issue.culprit)
    expected = ("TimeoutError: app.pay in charge", "app.pay in charge")
    assert result == expected


@pytest.mark.django_db
def test_a_late_delivery_does_not_rewrite_the_title(project):
    """Should ignore a straggler — the newest event is what the issue shows."""
    now = timezone.now()
    deliver(project, event_payload(fingerprint=["checkout"]), received_at=now)
    payload = event_payload(
        event_id="c" * 32,
        fingerprint=["checkout"],
        level="fatal",
        exception={"values": [{"type": "TimeoutError", "value": "slow"}]},
    )

    deliver(project, payload, received_at=now - datetime.timedelta(hours=1))

    issue = issue_models.Issue.objects.get()
    result = (issue.title, issue.level)
    expected = ("ValueError", issue_models.Level.ERROR)
    assert result == expected


# regression


@pytest.mark.django_db
def test_a_new_event_on_a_resolved_issue_regresses_it(project):
    """Should reopen an issue somebody marked resolved when it happens again."""
    deliver(project)
    issue = issue_models.Issue.objects.get()
    issue.triage_state = issue_models.TriageState.RESOLVED
    issue.last_resolved_at = timezone.now() - datetime.timedelta(minutes=5)
    issue.save(update_fields=["triage_state", "last_resolved_at"])

    deliver(project, event_payload(event_id="c" * 32))

    issue.refresh_from_db()
    result = (issue.triage_state, issue_models.IssueActivity.objects.count())
    expected = (issue_models.TriageState.NEW, 2)
    assert result == expected


@pytest.mark.django_db
def test_a_regression_records_the_state_it_came_from(project):
    """Should say what the issue was before it came back."""
    deliver(project)
    issue = issue_models.Issue.objects.get()
    issue.triage_state = issue_models.TriageState.RESOLVED
    issue.save(update_fields=["triage_state"])

    deliver(project, event_payload(event_id="c" * 32))

    activity = issue_models.IssueActivity.objects.get(kind="regression")
    result = activity.data
    expected = {"previous_triage_state": "resolved"}
    assert result == expected


@pytest.mark.django_db
def test_an_event_delivered_before_the_resolution_does_not_regress(project):
    """Should ignore a straggler that reached pandora before the triage decision."""
    now = timezone.now()
    deliver(project, received_at=now - datetime.timedelta(hours=2))
    issue = issue_models.Issue.objects.get()
    issue.triage_state = issue_models.TriageState.RESOLVED
    issue.last_resolved_at = now
    issue.save(update_fields=["triage_state", "last_resolved_at"])

    deliver(
        project,
        event_payload(event_id="c" * 32),
        received_at=now - datetime.timedelta(hours=1),
    )

    issue.refresh_from_db()
    result = issue.triage_state
    expected = issue_models.TriageState.RESOLVED
    assert result == expected


@pytest.mark.django_db
def test_an_old_event_delivered_after_the_resolution_regresses(project):
    """Should reopen on a late delivery — the client's own clock is not the gate."""
    now = timezone.now()
    deliver(project, received_at=now - datetime.timedelta(hours=2))
    issue = issue_models.Issue.objects.get()
    issue.triage_state = issue_models.TriageState.RESOLVED
    issue.last_resolved_at = now
    issue.save(update_fields=["triage_state", "last_resolved_at"])

    payload = event_payload(
        event_id="c" * 32,
        timestamp=(now - datetime.timedelta(hours=3)).isoformat(),
    )
    deliver(project, payload, received_at=now + datetime.timedelta(minutes=5))

    issue.refresh_from_db()
    result = issue.triage_state
    expected = issue_models.TriageState.NEW
    assert result == expected


@pytest.mark.django_db
def test_an_acknowledged_issue_is_not_regressed(project):
    """Should leave triage alone unless the issue was actually resolved."""
    deliver(project)
    issue = issue_models.Issue.objects.get()
    issue.triage_state = issue_models.TriageState.ACKNOWLEDGED
    issue.save(update_fields=["triage_state"])

    deliver(project, event_payload(event_id="c" * 32))

    issue.refresh_from_db()
    result = issue.triage_state
    expected = issue_models.TriageState.ACKNOWLEDGED
    assert result == expected


# failure


@pytest.mark.django_db
def test_a_store_failure_leaves_the_envelope_replayable(project):
    """Should keep the row failed rather than lose the event."""
    envelope, _ = deliver(project, store=fakes.FailingEventStore())

    result = (envelope.state, "RuntimeError" in envelope.error)
    expected = (ingest_models.EnvelopeState.FAILED, True)
    assert result == expected


@pytest.mark.django_db
def test_a_failed_envelope_leaves_no_half_written_issue(project):
    """Should roll the whole apply back, counters included."""
    deliver(project, store=fakes.FailingEventStore())

    result = (
        issue_models.Issue.objects.count(),
        ingest_models.ProcessedEvent.objects.count(),
    )
    expected = (0, 0)
    assert result == expected


@pytest.mark.django_db
def test_a_replay_after_a_failure_lands_exactly_once(project):
    """Should apply cleanly on the retry that follows a store outage."""
    store = fakes.FlakyEventStore(failures=1)
    envelope = store_event(project)
    processor.process_envelope(envelope.pk, store=store)
    processor.process_envelope(envelope.pk, store=store)

    envelope.refresh_from_db()
    issue = issue_models.Issue.objects.get()
    result = (envelope.state, issue.event_count, len(store.rows))
    expected = (ingest_models.EnvelopeState.DONE, 1, 1)
    assert result == expected


@pytest.mark.django_db
def test_a_malformed_event_payload_fails_the_envelope(project):
    """Should record why rather than crash the consumer."""
    envelope = ingest_models.RawEnvelope.objects.create(
        project=project,
        source=core_models.TokenSource.SDK,
        payload=[1, 2, 3],
    )
    processor.process_envelope(envelope.pk, store=fakes.RecordingEventStore())

    envelope.refresh_from_db()
    result = (envelope.state, "EnvelopeError" in envelope.error)
    expected = (ingest_models.EnvelopeState.FAILED, True)
    assert result == expected


# events the door used to swallow


@pytest.mark.django_db
def test_events_with_no_id_do_not_collapse_onto_one_claim(project):
    """Should keep id-less events apart — one bad client must not mute itself."""
    for message in ("first", "second", "third"):
        deliver(project, {"message": message})

    result = (
        issue_models.Issue.objects.count(),
        ingest_models.ProcessedEvent.objects.count(),
    )
    expected = (3, 3)
    assert result == expected


@pytest.mark.django_db
def test_events_with_a_null_id_do_not_collapse_either(project):
    """Should treat an explicit null id as absent, not as a shared key."""
    for message in ("first", "second"):
        deliver(project, {"event_id": None, "message": message})

    result = issue_models.Issue.objects.count()
    expected = 2
    assert result == expected


@pytest.mark.django_db
def test_a_replayed_id_less_event_still_counts_once(project):
    """Should keep the same envelope idempotent even without a client id."""
    envelope = store_event(project, {"message": "boom"})
    store = fakes.RecordingEventStore()
    processor.process_envelope(envelope.pk, store=store)
    envelope.state = ingest_models.EnvelopeState.PENDING
    envelope.save(update_fields=["state"])
    processor.process_envelope(envelope.pk, store=store)

    issue = issue_models.Issue.objects.get()
    result = (issue.event_count, len(store.rows))
    expected = (1, 1)
    assert result == expected


@pytest.mark.django_db
def test_an_overlong_title_is_capped_to_the_column(project):
    """Should not let a deep module path reject the row on Postgres."""
    payload = event_payload(
        exception={
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad",
                    "stacktrace": {
                        "frames": [
                            {"module": "m" * 400, "function": "f" * 400, "in_app": True}
                        ]
                    },
                }
            ]
        }
    )
    deliver(project, payload)

    issue = issue_models.Issue.objects.get()
    result = len(issue.title)
    expected = 500
    assert result == expected


@pytest.mark.django_db
def test_an_overlong_environment_is_capped_to_the_column(project):
    """Should not let a long environment reject the row on Postgres."""
    deliver(project, event_payload(environment="e" * 400))

    issue = issue_models.Issue.objects.get()
    result = len(issue.environment)
    expected = 100
    assert result == expected


@pytest.mark.django_db
def test_an_overlong_culprit_is_capped_to_the_column(project):
    """Should not let a deep module path reject the row on Postgres."""
    payload = event_payload(
        exception={
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad",
                    "stacktrace": {
                        "frames": [
                            {"module": "m" * 400, "function": "f" * 400, "in_app": True}
                        ]
                    },
                }
            ]
        }
    )
    deliver(project, payload)

    issue = issue_models.Issue.objects.get()
    result = len(issue.culprit)
    expected = 500
    assert result == expected


# the stored payload


@pytest.mark.django_db
def test_a_stored_event_carries_the_stack_frames(project):
    """Should keep what makes an error readable — grouping alone is not enough."""
    payload = event_payload(
        exception={
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad input",
                    "stacktrace": {
                        "frames": [
                            {
                                "module": "app.views",
                                "function": "handle",
                                "filename": "app/views.py",
                                "lineno": 42,
                                "in_app": True,
                                "context_line": "    raise ValueError(payload)",
                            }
                        ]
                    },
                }
            ]
        }
    )
    _, store = deliver(project, payload)

    result = store.rows[0].payload["exceptions"][0]["frames"][0]
    expected = {
        "module": "app.views",
        "function": "handle",
        "filename": "app/views.py",
        "lineno": 42,
        "in_app": True,
        "context_line": "    raise ValueError(payload)",
    }

    assert result == expected


@pytest.mark.django_db
def test_a_stored_event_carries_its_breadcrumbs_and_context(project):
    """Should keep the timeline and the who, which triage reads before the code."""
    payload = event_payload(
        breadcrumbs={"values": [{"category": "db", "message": "SELECT 1"}]},
        user={"id": "7", "username": "renata"},
        contexts={"runtime": {"name": "CPython", "version": "3.12.7"}},
    )
    _, store = deliver(project, payload)

    stored = store.rows[0].payload
    result = (
        stored["breadcrumbs"][0]["message"],
        stored["user"]["username"],
        stored["contexts"]["runtime"]["name"],
    )
    expected = ("SELECT 1", "renata", "CPython")

    assert result == expected


@pytest.mark.django_db
def test_an_alertmanager_occurrence_stores_no_interfaces(token, am_fixture):
    """Should leave the column empty for a source that has no stack trace."""
    store = fakes.RecordingEventStore()
    helpers.deliver(am_fixture("firing_group"), token, store)

    result = {tuple(row.payload) for row in store.rows}
    expected = {()}

    assert result == expected


@pytest.mark.django_db
def test_a_stack_grouped_issue_says_so(project):
    """Should tell a reader the fingerprint came from the exception, not a rule."""
    deliver(project)

    issue = issue_models.Issue.objects.get()
    result = (issue.grouping_source, issue.grouping_rule_id)
    expected = (issue_models.GroupingSource.STACK, None)

    assert result == expected


@pytest.mark.django_db
def test_a_client_declared_fingerprint_says_so(project):
    """Should make an SDK's own fingerprint visible rather than silently obeyed."""
    deliver(project, event_payload(fingerprint=["tenant-42"]))

    result = issue_models.Issue.objects.get().grouping_source
    expected = issue_models.GroupingSource.CLIENT

    assert result == expected


@pytest.mark.django_db
def test_a_message_only_event_groups_on_the_message(project):
    """Should still record how it grouped when there is no exception at all."""
    deliver(project, {"event_id": "d" * 32, "message": "disk filling"})

    result = issue_models.Issue.objects.get().grouping_source
    expected = issue_models.GroupingSource.MESSAGE

    assert result == expected


def message_payload(event_id, message):
    return {"event_id": event_id, "level": "error", "message": message}


@pytest.mark.django_db
def test_normalisation_collapses_two_ids_into_one_issue(project, settings):
    """Should be the whole point — one fault, not one issue per order id."""
    settings.PANDORA_GROUPING_NORMALISE = True
    deliver(project, message_payload("1" * 32, "order 8891 failed"))
    deliver(project, message_payload("2" * 32, "order 4417 failed"))

    result = issue_models.Issue.objects.count()
    expected = 1

    assert result == expected


@pytest.mark.django_db
def test_the_same_two_events_stay_apart_while_it_is_off(project, settings):
    """Should change nothing until an operator turns it on deliberately."""
    settings.PANDORA_GROUPING_NORMALISE = False
    deliver(project, message_payload("1" * 32, "order 8891 failed"))
    deliver(project, message_payload("2" * 32, "order 4417 failed"))

    result = issue_models.Issue.objects.count()
    expected = 2

    assert result == expected


@pytest.mark.django_db
def test_the_event_keeps_the_id_the_fingerprint_dropped(project, settings):
    """Should never lose the value — it leaves the key, not the record."""
    settings.PANDORA_GROUPING_NORMALISE = True
    store = fakes.RecordingEventStore()
    envelope = store_event(project, message_payload(SENTRY_ID, "order 8891 failed"))
    processor.process_envelope(envelope.pk, store=store)

    result = store.rows[0].message

    assert "8891" in result


@pytest.mark.django_db
def test_the_normalised_fingerprint_is_what_the_issue_shows(project, settings):
    """Should show the key it grouped on, so a reader can see why."""
    settings.PANDORA_GROUPING_NORMALISE = True
    deliver(project, message_payload(SENTRY_ID, "order 8891 failed"))

    result = issue_models.Issue.objects.get().fingerprint
    expected = ["order <n> failed"]

    assert result == expected


@pytest.mark.django_db
def test_a_rule_can_split_one_issue_by_a_tag(project):
    """Should refine the built-in key rather than replacing it, which is the point."""
    issue_models.GroupingRule.objects.create(
        priority=10,
        conditions={"path": "exceptions.*.type", "value": "ValueError"},
        fingerprint=["{{ default }}", "{{ tags.tenant }}"],
    )
    deliver(project, event_payload(event_id="1" * 32, tags={"tenant": "acme"}))
    deliver(project, event_payload(event_id="2" * 32, tags={"tenant": "globex"}))

    result = issue_models.Issue.objects.count()
    expected = 2

    assert result == expected


@pytest.mark.django_db
def test_the_same_events_are_one_issue_without_the_rule(project):
    """Should show the split is the rule's doing, not the payload's."""
    deliver(project, event_payload(event_id="1" * 32, tags={"tenant": "acme"}))
    deliver(project, event_payload(event_id="2" * 32, tags={"tenant": "globex"}))

    result = issue_models.Issue.objects.count()
    expected = 1

    assert result == expected


@pytest.mark.django_db
def test_a_rule_can_set_the_title(project):
    """Should let an operator name an issue in the words their team uses."""
    issue_models.GroupingRule.objects.create(
        priority=10,
        conditions={"path": "exceptions.*.type", "value": "ValueError"},
        title_template="checkout broke for {{ tags.tenant }}",
    )
    deliver(project, event_payload(tags={"tenant": "acme"}))

    result = issue_models.Issue.objects.get().title
    expected = "checkout broke for acme"

    assert result == expected


@pytest.mark.django_db
def test_a_rule_that_regrouped_an_sdk_issue_is_recorded_on_it(project):
    """Should point at the rule to change when the grouping is wrong."""
    rule = issue_models.GroupingRule.objects.create(
        priority=10,
        conditions={"path": "exceptions.*.type", "value": "ValueError"},
        fingerprint=["checkout"],
    )
    deliver(project)

    issue = issue_models.Issue.objects.get()
    result = (issue.grouping_source, issue.grouping_rule_id)
    expected = (issue_models.GroupingSource.RULE, rule.pk)

    assert result == expected


@pytest.mark.django_db
def test_a_merged_fingerprint_lands_on_the_keeper(project):
    """Should hold the merge — otherwise what an operator merged comes straight back."""
    from pandora.issues import merge

    deliver(project, event_payload(event_id="1" * 32))
    deliver(
        project,
        event_payload(
            event_id="2" * 32,
            exception={"values": [{"type": "TimeoutError", "value": "slow"}]},
        ),
    )
    keeper, other = issue_models.Issue.objects.order_by("pk")
    merge.merge(keeper, [other])

    deliver(
        project,
        event_payload(
            event_id="3" * 32,
            exception={"values": [{"type": "TimeoutError", "value": "slow"}]},
        ),
    )

    result = issue_models.Issue.objects.count()
    expected = 1

    assert result == expected


@pytest.mark.django_db
def test_the_merged_occurrence_is_counted_on_the_keeper(project):
    """Should add to the issue it landed on, not vanish into the alias."""
    from pandora.issues import merge

    deliver(project, event_payload(event_id="1" * 32))
    deliver(
        project,
        event_payload(
            event_id="2" * 32,
            exception={"values": [{"type": "TimeoutError", "value": "slow"}]},
        ),
    )
    keeper, other = issue_models.Issue.objects.order_by("pk")
    merge.merge(keeper, [other])
    before = issue_models.Issue.objects.get().event_count

    deliver(
        project,
        event_payload(
            event_id="3" * 32,
            exception={"values": [{"type": "TimeoutError", "value": "slow"}]},
        ),
    )

    result = issue_models.Issue.objects.get().event_count
    expected = before + 1

    assert result == expected
