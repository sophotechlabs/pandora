import dataclasses
import datetime

import pytest

from pandora.events import types
from tests.events import support

pytestmark = pytest.mark.django_db(databases="__all__")


# store contract


def test_the_store_is_bound_to_the_events_table(event_store):
    """Should write to the one table the vendor-branched migration creates."""
    result = event_store.table
    expected = types.EVENTS_TABLE

    assert result == expected


# insert


def test_insert_round_trips_every_field(event_store, moment):
    """Should return an event identical to the one written, field for field."""
    event = support.make_event(0, moment)

    event_store.insert([event])

    result = event_store.fetch(event.project_id)
    expected = [event]
    assert result == expected


def test_insert_round_trips_an_unlinked_event(event_store, moment):
    """Should keep issue and episode null for an event with no links."""
    event = support.make_event(
        0,
        moment,
        issue_id=None,
        episode_id=None,
        fingerprint=[],
        tags={},
        extra={},
        source="sdk",
        environment="",
    )

    event_store.insert([event])

    result = event_store.fetch(event.project_id)
    expected = [event]
    assert result == expected


def test_insert_writes_a_whole_batch(event_store, moment):
    """Should write every event handed to it in one call."""
    events = support.make_events(5, moment)

    event_store.insert(events)

    result = support.ids(event_store.fetch(1))
    expected = list(reversed(support.ids(events)))
    assert result == expected


def test_insert_of_nothing_writes_nothing(event_store):
    """Should treat an empty batch as a no-op, not an error."""
    event_store.insert([])

    result = event_store.fetch(1)
    expected = []
    assert result == expected


def test_insert_is_idempotent_for_a_replayed_event(event_store, moment):
    """Should keep one row when the same event is written twice."""
    event = support.make_event(0, moment)

    event_store.insert([event])
    event_store.insert([event])

    result = event_store.fetch(event.project_id)
    expected = [event]
    assert result == expected


def test_insert_keeps_the_first_write_of_a_conflicting_event(event_store, moment):
    """Should ignore the later write rather than overwrite the stored payload."""
    first = support.make_event(0, moment)
    second = support.make_event(0, moment, message="Rewritten by a replay.")

    event_store.insert([first])
    event_store.insert([second])

    result = [event.message for event in event_store.fetch(first.project_id)]
    expected = ["Pod is crash looping."]
    assert result == expected


def test_insert_deduplicates_inside_one_batch(event_store, moment):
    """Should collapse a duplicate that arrives in the same batch."""
    event = support.make_event(0, moment)

    event_store.insert([event, event])

    result = len(event_store.fetch(event.project_id))
    expected = 1
    assert result == expected


# fetch


def test_fetch_returns_the_newest_event_first(event_store, moment):
    """Should order by id descending — ULIDs sort in creation order."""
    events = support.make_events(4, moment)
    event_store.insert(events)

    result = support.ids(event_store.fetch(1))
    expected = [support.event_id(index) for index in (3, 2, 1, 0)]
    assert result == expected


def test_fetch_is_scoped_to_one_project(event_store, moment):
    """Should never leak another project's events."""
    event_store.insert(
        [
            support.make_event(0, moment, project_id=1),
            support.make_event(1, moment, project_id=2),
        ]
    )

    result = support.ids(event_store.fetch(2))
    expected = [support.event_id(1)]
    assert result == expected


def test_fetch_filters_by_issue(event_store, moment):
    """Should return only the events grouped into the given issue."""
    event_store.insert(
        [
            support.make_event(0, moment, issue_id=10),
            support.make_event(1, moment, issue_id=11),
            support.make_event(2, moment, issue_id=10),
        ]
    )

    result = support.ids(event_store.fetch(1, issue_id=10))
    expected = [support.event_id(2), support.event_id(0)]
    assert result == expected


def test_fetch_filters_by_episode(event_store, moment):
    """Should return only the events stamped with the given episode."""
    event_store.insert(
        [
            support.make_event(0, moment, episode_id="100"),
            support.make_event(1, moment, episode_id="101"),
        ]
    )

    result = support.ids(event_store.fetch(1, episode_id="101"))
    expected = [support.event_id(1)]
    assert result == expected


def test_fetch_combines_the_issue_and_episode_filters(event_store, moment):
    """Should require both filters to match, not either."""
    event_store.insert(
        [
            support.make_event(0, moment, issue_id=10, episode_id="100"),
            support.make_event(1, moment, issue_id=11, episode_id="100"),
        ]
    )

    result = support.ids(event_store.fetch(1, issue_id=10, episode_id="100"))
    expected = [support.event_id(0)]
    assert result == expected


def test_fetch_honours_the_limit(event_store, moment):
    """Should return at most `limit` events."""
    event_store.insert(support.make_events(5, moment))

    result = support.ids(event_store.fetch(1, limit=2))
    expected = [support.event_id(4), support.event_id(3)]
    assert result == expected


def test_fetch_before_excludes_the_cursor_row(event_store, moment):
    """Should start strictly below the cursor so a page never repeats a row."""
    event_store.insert(support.make_events(4, moment))

    result = support.ids(event_store.fetch(1, before=support.event_id(2)))
    expected = [support.event_id(1), support.event_id(0)]
    assert result == expected


def test_fetch_pages_through_every_event_once(event_store, moment):
    """Should walk the whole set with the cursor, no gaps and no repeats."""
    event_store.insert(support.make_events(5, moment))

    seen = []
    cursor = None
    for _ in range(5):
        page = event_store.fetch(1, before=cursor, limit=2)
        if not page:
            break
        seen.extend(support.ids(page))
        cursor = page[-1].id

    expected = [support.event_id(index) for index in (4, 3, 2, 1, 0)]
    assert seen == expected


def test_fetch_returns_nothing_for_an_unknown_project(event_store, moment):
    """Should return an empty list rather than raise for a project with no events."""
    event_store.insert(support.make_events(2, moment))

    result = event_store.fetch(999)
    expected = []
    assert result == expected


# reassign


def test_reassign_moves_an_episode_to_another_issue(event_store, moment):
    """Should follow the episodes when regroup rebuilds the grouping."""
    event_store.insert([support.make_event(0, moment, issue_id=10, episode_id="100")])

    event_store.reassign(1, ["100"], 11)

    result = support.ids(event_store.fetch(1, issue_id=11))
    expected = [support.event_id(0)]
    assert result == expected


def test_reassign_leaves_the_old_issue_empty(event_store, moment):
    """Should move the row, not copy it — the old grouping must go."""
    event_store.insert([support.make_event(0, moment, issue_id=10, episode_id="100")])

    event_store.reassign(1, ["100"], 11)

    result = event_store.fetch(1, issue_id=10)
    expected = []
    assert result == expected


def test_reassign_touches_only_the_episodes_it_was_given(event_store, moment):
    """Should leave every episode the rebuild did not move where it was."""
    event_store.insert(
        [
            support.make_event(0, moment, issue_id=10, episode_id="100"),
            support.make_event(1, moment, issue_id=10, episode_id="101"),
        ]
    )

    event_store.reassign(1, ["100"], 11)

    result = support.ids(event_store.fetch(1, issue_id=10))
    expected = [support.event_id(1)]
    assert result == expected


def test_reassign_moves_every_event_of_one_episode(event_store, moment):
    """Should relink the whole episode — an open and its close travel together."""
    event_store.insert(
        [
            support.make_event(0, moment, issue_id=10, episode_id="100"),
            support.make_event(1, moment, issue_id=10, episode_id="100"),
        ]
    )

    result = event_store.reassign(1, ["100"], 11)
    expected = 2

    assert result == expected


def test_reassign_reports_how_many_rows_it_relinked(event_store, moment):
    """Should report the row count so regroup can log what moved."""
    event_store.insert(
        [
            support.make_event(0, moment, issue_id=10, episode_id="100"),
            support.make_event(1, moment, issue_id=10, episode_id="101"),
        ]
    )

    result = event_store.reassign(1, ["100", "101"], 11)
    expected = 2

    assert result == expected


def test_reassign_is_scoped_to_one_project(event_store, moment):
    """Should never relink another project's events, whatever the episode id."""
    event_store.insert(
        [
            support.make_event(0, moment, project_id=1, episode_id="100"),
            support.make_event(1, moment, project_id=2, episode_id="100"),
        ]
    )

    event_store.reassign(1, ["100"], 11)

    result = [event.issue_id for event in event_store.fetch(2)]
    expected = [10]
    assert result == expected


def test_reassign_of_no_episodes_touches_nothing(event_store, moment):
    """Should short-circuit an empty move rather than build an empty IN clause."""
    event_store.insert([support.make_event(0, moment, issue_id=10)])

    result = event_store.reassign(1, [], 11)
    expected = 0

    assert result == expected


def test_reassign_ignores_an_unknown_episode(event_store, moment):
    """Should report nothing moved rather than raise on a stale episode id."""
    event_store.insert([support.make_event(0, moment, issue_id=10)])

    result = event_store.reassign(1, ["nope"], 11)
    expected = 0

    assert result == expected


def test_reassign_accepts_more_episodes_than_one_statement_holds(event_store, moment):
    """Should chunk a wide rebuild instead of tripping the parameter limit."""
    event_store.insert(
        [
            support.make_event(index, moment, issue_id=10, episode_id=str(index))
            for index in range(3)
        ]
    )
    episode_ids = [str(index) for index in range(1200)]

    result = event_store.reassign(1, episode_ids, 11)
    expected = 3

    assert result == expected


def test_reassign_is_idempotent(event_store, moment):
    """Should be safe to run twice — a re-run of regroup must not drift."""
    event_store.insert([support.make_event(0, moment, issue_id=10, episode_id="100")])

    event_store.reassign(1, ["100"], 11)
    event_store.reassign(1, ["100"], 11)

    result = [event.issue_id for event in event_store.fetch(1)]
    expected = [11]
    assert result == expected


# reassign_events


def test_reassign_events_moves_one_event_to_another_issue(event_store, moment):
    """Should relink an SDK event, which carries no episode to move it by."""
    event_store.insert(
        [support.make_event(0, moment, issue_id=10, episode_id=None, source="sdk")]
    )

    event_store.reassign_events(1, [support.event_id(0)], 11)

    result = support.ids(event_store.fetch(1, issue_id=11))
    expected = [support.event_id(0)]
    assert result == expected


def test_reassign_events_touches_only_the_ids_it_was_given(event_store, moment):
    """Should leave every event the rebuild did not name where it was."""
    event_store.insert(support.make_events(2, moment, issue_id=10, episode_id=None))

    event_store.reassign_events(1, [support.event_id(0)], 11)

    result = support.ids(event_store.fetch(1, issue_id=10))
    expected = [support.event_id(1)]
    assert result == expected


def test_reassign_events_reports_how_many_rows_it_relinked(event_store, moment):
    """Should report the row count so the rebuild can log what moved."""
    event_store.insert(support.make_events(2, moment, issue_id=10, episode_id=None))

    result = event_store.reassign_events(
        1, [support.event_id(0), support.event_id(1)], 11
    )
    expected = 2

    assert result == expected


def test_reassign_events_is_scoped_to_one_project(event_store, moment):
    """Should never relink another project's event, whatever the id."""
    event_store.insert(
        [
            support.make_event(0, moment, project_id=1, episode_id=None),
            support.make_event(1, moment, project_id=2, episode_id=None),
        ]
    )

    event_store.reassign_events(1, [support.event_id(0), support.event_id(1)], 11)

    result = [event.issue_id for event in event_store.fetch(2)]
    expected = [10]
    assert result == expected


def test_reassign_events_of_nothing_touches_nothing(event_store, moment):
    """Should short-circuit an empty move rather than build an empty IN clause."""
    event_store.insert([support.make_event(0, moment, issue_id=10)])

    result = event_store.reassign_events(1, [], 11)
    expected = 0

    assert result == expected


def test_reassign_events_ignores_an_unknown_id(event_store, moment):
    """Should report nothing moved rather than raise on a pruned event."""
    event_store.insert([support.make_event(0, moment, issue_id=10)])

    result = event_store.reassign_events(1, ["01JNOPE"], 11)
    expected = 0

    assert result == expected


def test_reassign_events_accepts_more_ids_than_one_statement_holds(event_store, moment):
    """Should chunk a wide rebuild instead of tripping the parameter limit."""
    event_store.insert(support.make_events(3, moment, issue_id=10, episode_id=None))
    event_ids = [support.event_id(index) for index in range(1200)]

    result = event_store.reassign_events(1, event_ids, 11)
    expected = 3

    assert result == expected


def test_reassign_events_is_idempotent(event_store, moment):
    """Should be safe to run twice — a re-run of the rebuild must not drift."""
    event_store.insert([support.make_event(0, moment, issue_id=10, episode_id=None)])

    event_store.reassign_events(1, [support.event_id(0)], 11)
    event_store.reassign_events(1, [support.event_id(0)], 11)

    result = [event.issue_id for event in event_store.fetch(1)]
    expected = [11]
    assert result == expected


# search


def test_search_matches_a_single_tag(event_store, moment, window):
    """Should return the events whose tag map carries the given pair."""
    event_store.insert(
        [
            support.make_event(0, moment, tags={"namespace": "payments"}),
            support.make_event(1, moment, tags={"namespace": "storefront"}),
        ]
    )

    result = support.ids(event_store.search(1, {"namespace": "payments"}, *window))
    expected = [support.event_id(0)]
    assert result == expected


def test_search_requires_every_tag_to_match(event_store, moment, window):
    """Should AND the tag filters, not OR them."""
    event_store.insert(
        [
            support.make_event(
                0, moment, tags={"namespace": "payments", "severity": "critical"}
            ),
            support.make_event(
                1, moment, tags={"namespace": "payments", "severity": "warning"}
            ),
        ]
    )
    tags = {"namespace": "payments", "severity": "critical"}

    result = support.ids(event_store.search(1, tags, *window))
    expected = [support.event_id(0)]
    assert result == expected


def test_search_without_tags_returns_the_whole_window(event_store, moment, window):
    """Should treat an empty tag map as no tag filter at all."""
    event_store.insert(support.make_events(3, moment))

    result = support.ids(event_store.search(1, {}, *window))
    expected = [support.event_id(index) for index in (2, 1, 0)]
    assert result == expected


def test_search_ignores_an_unknown_tag_key(event_store, moment, window):
    """Should match nothing when a filtered key is absent from every event."""
    event_store.insert(support.make_events(3, moment))

    result = event_store.search(1, {"pod": "ledger-0"}, *window)
    expected = []
    assert result == expected


def test_search_includes_the_since_boundary(event_store, moment):
    """Should treat `since` as inclusive."""
    event_store.insert([support.make_event(0, moment)])

    result = support.ids(
        event_store.search(1, {}, moment, moment + datetime.timedelta(hours=1))
    )
    expected = [support.event_id(0)]
    assert result == expected


def test_search_excludes_the_until_boundary(event_store, moment):
    """Should treat `until` as exclusive so adjacent windows never overlap."""
    event_store.insert([support.make_event(0, moment)])

    result = event_store.search(1, {}, moment - datetime.timedelta(hours=1), moment)
    expected = []
    assert result == expected


def test_search_is_scoped_to_one_project(event_store, moment, window):
    """Should never return another project's events for a matching tag."""
    event_store.insert(
        [
            support.make_event(0, moment, project_id=1),
            support.make_event(1, moment, project_id=2),
        ]
    )

    result = support.ids(event_store.search(2, {"namespace": "payments"}, *window))
    expected = [support.event_id(1)]
    assert result == expected


def test_search_returns_the_newest_event_first(event_store, moment, window):
    """Should order the window by timestamp descending."""
    event_store.insert(support.make_events(3, moment))

    result = support.ids(event_store.search(1, {}, *window))
    expected = [support.event_id(index) for index in (2, 1, 0)]
    assert result == expected


def test_search_honours_the_limit(event_store, moment, window):
    """Should return at most `limit` events from the window."""
    event_store.insert(support.make_events(4, moment))

    result = support.ids(event_store.search(1, {}, *window, limit=2))
    expected = [support.event_id(3), support.event_id(2)]
    assert result == expected


# prune


def test_prune_removes_a_month_that_is_wholly_expired(event_store, moment):
    """Should delete events from a month that ended before the cutoff."""
    old = support.make_event(0, moment, timestamp=support.inside_previous_month(moment))
    current = support.make_event(1, moment)
    event_store.insert([old, current])

    event_store.prune(support.month_start(moment))

    result = support.ids(event_store.fetch(1))
    expected = [support.event_id(1)]
    assert result == expected


def test_prune_reports_how_many_events_it_removed(event_store, moment):
    """Should return the number of event rows the call actually removed."""
    event_store.insert(
        [
            support.make_event(
                index, moment, timestamp=support.inside_previous_month(moment)
            )
            for index in range(3)
        ]
    )
    event_store.insert([support.make_event(9, moment)])

    result = event_store.prune(support.month_start(moment))
    expected = 3
    assert result == expected


def test_prune_keeps_everything_at_or_after_the_cutoff(event_store, moment):
    """Should leave the retained window untouched."""
    events = support.make_events(3, moment)
    event_store.insert(events)

    event_store.prune(support.month_start(moment))

    result = support.ids(event_store.fetch(1))
    expected = list(reversed(support.ids(events)))
    assert result == expected


def test_prune_removes_nothing_when_the_window_is_empty(event_store, moment):
    """Should return zero when no event predates the cutoff."""
    event_store.insert(support.make_events(2, moment))

    result = event_store.prune(support.month_start(moment))
    expected = 0
    assert result == expected


def test_prune_is_idempotent(event_store, moment):
    """Should remove nothing on a second run with the same cutoff."""
    old = support.make_event(0, moment, timestamp=support.inside_previous_month(moment))
    event_store.insert([old])
    event_store.prune(support.month_start(moment))

    result = event_store.prune(support.month_start(moment))
    expected = 0
    assert result == expected


# ensure_partitions


def test_ensure_partitions_returns_nothing(event_store):
    """Should be a command, not a query — the caller gets no value back."""
    result = event_store.ensure_partitions()

    assert result is None


def test_ensure_partitions_can_run_twice(event_store):
    """Should be safe to run on every prune, not only on a fresh database."""
    event_store.ensure_partitions()

    result = event_store.ensure_partitions(months_ahead=3)

    assert result is None


def test_events_still_land_after_ensuring_partitions(event_store, moment):
    """Should leave the table writable — partitions cover the current month."""
    event_store.ensure_partitions()
    event = support.make_event(0, moment)

    event_store.insert([event])

    result = event_store.fetch(event.project_id)
    expected = [event]
    assert result == expected


# rewriting and removing single rows


def test_rewrite_replaces_the_mutable_columns(event_store, moment):
    """Should let a retroactive redaction land without touching the identity of the row."""
    event = support.make_event(0, moment)
    event_store.insert([event])
    edited = dataclasses.replace(
        event,
        message="[redacted]",
        tags={"namespace": "payments"},
        extra={},
        payload={"user": {"id": "7"}},
    )

    written = event_store.rewrite(event.project_id, [edited])

    result = (written, event_store.fetch(event.project_id))
    expected = (1, [edited])

    assert result == expected


def test_rewrite_leaves_other_rows_alone(event_store, moment):
    """Should touch only what it was given."""
    first, second = support.make_events(2, moment)
    event_store.insert([first, second])

    event_store.rewrite(first.project_id, [dataclasses.replace(first, message="gone")])

    result = [event.message for event in event_store.fetch(first.project_id)]
    expected = [second.message, "gone"]

    assert result == expected


def test_rewrite_of_nothing_is_a_no_op(event_store):
    """Should not issue a statement for an empty batch."""
    result = event_store.rewrite(1, [])
    expected = 0

    assert result == expected


def test_delete_removes_one_occurrence(event_store, moment):
    """Should let an operator remove a single leaked payload, which Sentry cannot do."""
    first, second = support.make_events(2, moment)
    event_store.insert([first, second])

    removed = event_store.delete(first.project_id, [first])

    result = (removed, [event.id for event in event_store.fetch(first.project_id)])
    expected = (1, [second.id])

    assert result == expected


def test_delete_is_scoped_to_the_project(event_store, moment):
    """Should never reach across projects, the rule every other store method holds."""
    event = support.make_event(0, moment)
    event_store.insert([event])

    removed = event_store.delete(event.project_id + 1, [event])

    result = (removed, len(event_store.fetch(event.project_id)))
    expected = (0, 1)

    assert result == expected


def test_delete_of_nothing_is_a_no_op(event_store):
    """Should not issue a statement for an empty batch."""
    result = event_store.delete(1, [])
    expected = 0

    assert result == expected
