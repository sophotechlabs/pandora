import datetime

import pytest
from django.utils import timezone

from pandora.issues import environments, models
from pandora.issues.models import Issue
from pandora.ui import query

pytestmark = pytest.mark.django_db


def run(raw):
    found, rejected = query.filter_issues(
        Issue.objects.all(),
        query.parse(raw),
        timezone.now(),
    )
    return list(found), rejected


def titles(raw):
    found, _ = run(raw)
    return sorted(issue.title for issue in found)


# parsing


def test_a_bare_word_is_free_text():
    """Should search the title rather than guess at a filter."""
    parsed = query.parse("crashloop")

    result = (parsed.text, parsed.terms, parsed.unknown)
    expected = ("crashloop", (), ())

    assert result == expected


def test_a_known_key_becomes_a_term():
    """Should split key:value into the filter the stream applies."""
    parsed = query.parse("level:error")

    result = parsed.terms
    expected = (query.Term("level", "error"),)

    assert result == expected


def test_terms_and_free_text_can_be_mixed():
    """Should let an operator narrow by filter and by words at once."""
    parsed = query.parse("is:unresolved payments ledger")

    result = (parsed.terms, parsed.text)
    expected = ((query.Term("is", "unresolved"),), "payments ledger")

    assert result == expected


def test_a_quoted_phrase_survives_as_one_value():
    """Should keep a multi-word title search together."""
    parsed = query.parse('"scrape target unreachable"')

    result = parsed.text
    expected = "scrape target unreachable"

    assert result == expected


def test_an_unbalanced_quote_falls_back_to_plain_splitting():
    """Should search rather than fail when someone is mid-typing."""
    parsed = query.parse('level:error "half typed')

    result = parsed.terms
    expected = (query.Term("level", "error"),)

    assert result == expected


def test_env_is_an_alias_for_environment():
    """Should accept the short form an operator will reach for."""
    parsed = query.parse("env:p-mk1")

    result = parsed.terms
    expected = (query.Term("environment", "p-mk1"),)

    assert result == expected


def test_an_unknown_filter_is_reported_not_silently_searched():
    """Should tell the reader the term did nothing instead of returning zero rows."""
    parsed = query.parse("severity:page")

    result = (parsed.unknown, parsed.text, parsed.terms)
    expected = (("severity:page",), "", ())

    assert result == expected


def test_a_key_with_no_value_is_free_text():
    """Should not treat a half-typed filter as a filter."""
    parsed = query.parse("level:")

    result = (parsed.text, parsed.terms, parsed.unknown)
    expected = ("level:", (), ())

    assert result == expected


def test_a_colon_inside_a_word_stays_free_text():
    """Should let a URL or a stack path through to the title search."""
    parsed = query.parse("HTTPError:listopad")

    result = (parsed.text, parsed.unknown)
    expected = ("HTTPError:listopad", ())

    assert result == expected


# triage and source state


def test_is_unresolved_covers_new_and_acknowledged(make_issue):
    """Should default the stream to everything a human still owns."""
    make_issue(title="New one")
    make_issue(title="Owned", triage_state=models.TriageState.ACKNOWLEDGED)
    make_issue(title="Closed", triage_state=models.TriageState.RESOLVED)

    result = titles("is:unresolved")
    expected = ["New one", "Owned"]

    assert result == expected


def test_is_takes_an_exact_triage_state(make_issue):
    """Should let a reader ask for one state only."""
    make_issue(title="New one")
    make_issue(title="Closed", triage_state=models.TriageState.RESOLVED)

    result = titles("is:resolved")
    expected = ["Closed"]

    assert result == expected


def test_ack_is_accepted_as_the_stored_spelling(make_issue):
    """Should accept the value the database holds as well as the word."""
    make_issue(title="Owned", triage_state=models.TriageState.ACKNOWLEDGED)

    result = titles("is:ack")
    expected = ["Owned"]

    assert result == expected


def test_repeating_a_filter_widens_it(make_issue):
    """Should read two of the same key as either, the way the API does."""
    make_issue(title="New one")
    make_issue(title="Closed", triage_state=models.TriageState.RESOLVED)
    make_issue(title="Muted", triage_state=models.TriageState.IGNORED)

    result = titles("is:new is:resolved")
    expected = ["Closed", "New one"]

    assert result == expected


def test_an_unusable_triage_value_is_rejected_not_applied(make_issue):
    """Should keep showing rows and name the term it could not use."""
    make_issue(title="New one")

    found, rejected = run("is:sideways")

    result = ([issue.title for issue in found], rejected)
    expected = (["New one"], ["is:sideways"])

    assert result == expected


def test_state_filters_on_what_the_source_says(make_issue):
    """Should separate what is firing now from what pandora was told about."""
    make_issue(title="Live")
    make_issue(title="Settled", source_state=models.SourceState.RESOLVED)

    result = titles("state:resolved")
    expected = ["Settled"]

    assert result == expected


def test_an_unusable_source_state_is_rejected(make_issue):
    """Should not silently return nothing for a typo."""
    make_issue(title="Live")

    found, rejected = run("state:smouldering")

    result = rejected
    expected = ["state:smouldering"]

    assert result == expected
    assert len(found) == 1


# level, project, environment


def test_level_filters_on_severity(make_issue):
    """Should let an operator cut to the errors."""
    make_issue(title="Loud", level=models.Level.ERROR)
    make_issue(title="Quiet", level=models.Level.INFO)

    result = titles("level:error")
    expected = ["Loud"]

    assert result == expected


def test_an_unusable_level_is_rejected(make_issue):
    """Should name the bad value rather than empty the list."""
    make_issue(title="Loud", level=models.Level.ERROR)

    found, rejected = run("level:catastrophic")

    result = rejected
    expected = ["level:catastrophic"]

    assert result == expected
    assert len(found) == 1


def test_project_filters_on_the_slug(make_issue, other_project):
    """Should scope the stream to one project."""
    make_issue(title="Mine")
    make_issue(title="Theirs", project=other_project)

    result = titles("project:apps")
    expected = ["Theirs"]

    assert result == expected


def test_environment_filters_on_the_cluster(make_issue):
    """Should separate two clusters feeding one pandora."""
    make_issue(title="One", environment="p-mk1")
    make_issue(title="Two", environment="p-mk2")

    result = titles("environment:p-mk2")
    expected = ["Two"]

    assert result == expected


# time windows


def test_seen_narrows_to_a_recent_window(make_issue):
    """Should answer what has been noisy in the last hour."""
    now = timezone.now()
    make_issue(title="Fresh", last_seen=now - datetime.timedelta(minutes=10))
    make_issue(title="Stale", last_seen=now - datetime.timedelta(days=3))

    result = titles("seen:1h")
    expected = ["Fresh"]

    assert result == expected


def test_age_narrows_on_when_the_issue_first_appeared(make_issue):
    """Should answer what is genuinely new rather than what is loud."""
    now = timezone.now()
    make_issue(title="Young", first_seen=now - datetime.timedelta(hours=3))
    make_issue(title="Old", first_seen=now - datetime.timedelta(days=20))

    result = titles("age:1d")
    expected = ["Young"]

    assert result == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("30m", datetime.timedelta(minutes=30)),
        ("6h", datetime.timedelta(hours=6)),
        ("7d", datetime.timedelta(days=7)),
        ("2w", datetime.timedelta(weeks=2)),
    ],
)
def test_every_duration_unit_is_understood(raw, expected):
    """Should cover minutes through weeks so the grammar is worth learning."""
    result = query.parse_duration(raw)

    assert result == expected


def test_a_duration_without_a_unit_is_rejected(make_issue):
    """Should say the window was ignored rather than guess at hours."""
    make_issue(title="Fresh")

    found, rejected = run("seen:24")

    result = rejected
    expected = ["seen:24"]

    assert result == expected
    assert len(found) == 1


# labels and tags


def test_label_filters_on_a_kept_grouping_label(make_issue):
    """Should let an operator pivot on the labels the fingerprint kept."""
    make_issue(title="Payments", grouping_labels={"namespace": "payments"})
    make_issue(title="Traefik", grouping_labels={"namespace": "traefik"})

    result = titles("label:namespace=payments")
    expected = ["Payments"]

    assert result == expected


def test_two_labels_must_both_match(make_issue):
    """Should read two label terms as and, not or."""
    make_issue(
        title="Both",
        grouping_labels={"namespace": "payments", "alertname": "KubePodCrashLooping"},
    )
    make_issue(title="One", grouping_labels={"namespace": "payments"})

    result = titles("label:namespace=payments label:alertname=KubePodCrashLooping")
    expected = ["Both"]

    assert result == expected


def test_a_label_term_without_a_value_is_rejected(make_issue):
    """Should not build a lookup out of half a term."""
    make_issue(title="Payments", grouping_labels={"namespace": "payments"})

    found, rejected = run("label:namespace")

    result = rejected
    expected = ["label:namespace"]

    assert result == expected
    assert len(found) == 1


def test_a_label_name_that_is_not_a_label_name_is_rejected(make_issue):
    """Should keep a hand-typed term from reaching the ORM as a lookup path."""
    make_issue(title="Payments", grouping_labels={"namespace": "payments"})

    found, rejected = run("label:name__contains=pay")

    result = rejected
    expected = ["label:name__contains=pay"]

    assert result == expected
    assert len(found) == 1


def test_tag_filters_on_the_recorded_tag_breakdown(make_issue):
    """Should find issues by a tag value that grouping did not keep."""
    wanted = make_issue(title="Wanted")
    other = make_issue(title="Other")
    models.TagStat.objects.create(issue=wanted, key="pod", value="ledger-1", count=4)
    models.TagStat.objects.create(issue=other, key="pod", value="web-1", count=2)

    result = titles("tag:pod=ledger-1")
    expected = ["Wanted"]

    assert result == expected


def test_a_tag_term_without_a_value_is_rejected(make_issue):
    """Should report the term rather than join on half of it."""
    make_issue(title="Wanted")

    found, rejected = run("tag:pod")

    result = rejected
    expected = ["tag:pod"]

    assert result == expected
    assert len(found) == 1


# free text


def test_free_text_matches_the_title(make_issue):
    """Should find an issue by the words a human remembers."""
    make_issue(title="KubePodCrashLooping: pod is restarting")
    make_issue(title="TargetDown: scrape target unreachable")

    result = titles("crashlooping")
    expected = ["KubePodCrashLooping: pod is restarting"]

    assert result == expected


def test_free_text_matches_the_culprit(make_issue):
    """Should find an SDK issue by the module that raised."""
    make_issue(title="HTTPError", culprit="listopad.core.transport in get_json")
    make_issue(title="Other", culprit="alertname=TargetDown")

    result = titles("transport")
    expected = ["HTTPError"]

    assert result == expected


def test_free_text_matches_a_fingerprint_prefix(make_issue):
    """Should let an operator paste a hash out of a log line."""
    issue = make_issue(title="Hashed")
    make_issue(title="Other")

    result = titles(issue.fingerprint_hash[:12])
    expected = ["Hashed"]

    assert result == expected


def test_an_issue_seen_in_two_places_matches_either(make_issue):
    """Should find the issue from whichever cluster you were looking at."""
    issue = make_issue(title="Both", environment="p-mk1")
    environments.record(issue, "p-mk2", issue.last_seen)

    result = (titles("environment:p-mk1"), titles("environment:p-mk2"))
    expected = (["Both"], ["Both"])

    assert result == expected


def test_an_issue_is_listed_once_however_many_places_it_fires(make_issue):
    """Should not multiply a row by its environments — a join is not a result."""
    issue = make_issue(title="Both", environment="p-mk1")
    environments.record(issue, "p-mk2", issue.last_seen)

    result = titles("environment:p-mk1 environment:p-mk2")
    expected = ["Both"]

    assert result == expected


# priority and review


def test_a_priority_filter_matches_one_rank(make_issue):
    """Should let a reader see only what the ranking put at the top."""
    make_issue(title="Loud", priority=models.Priority.HIGH)
    make_issue(title="Quiet", priority=models.Priority.LOW)

    result = titles("priority:high")
    expected = ["Loud"]

    assert result == expected


def test_a_priority_filter_can_ask_for_a_rank_and_above(make_issue):
    """Should answer 'anything that matters' in one term."""
    make_issue(title="Loud", priority=models.Priority.HIGH)
    make_issue(title="Middling", priority=models.Priority.MEDIUM)
    make_issue(title="Quiet", priority=models.Priority.LOW)

    result = titles("priority:>=medium")
    expected = ["Loud", "Middling"]

    assert result == expected


def test_an_unknown_priority_is_named_back(make_issue):
    """Should tell the reader the term was dropped rather than empty the list."""
    make_issue(title="Loud", priority=models.Priority.HIGH)

    _, rejected = run("priority:urgent")

    assert rejected == ["priority:urgent"]


def test_for_review_lists_what_nobody_has_looked_at(make_issue):
    """Should be the queue Sentry calls For Review."""
    make_issue(title="Fresh", needs_review=True)
    make_issue(title="Seen", needs_review=False)

    result = titles("is:for_review")
    expected = ["Fresh"]

    assert result == expected


def test_reviewed_lists_the_rest(make_issue):
    """Should let a reader ask the opposite question."""
    make_issue(title="Fresh", needs_review=True)
    make_issue(title="Seen", needs_review=False)

    result = titles("is:reviewed")
    expected = ["Seen"]

    assert result == expected


def test_assigned_is_another_word_for_owner(make_issue):
    """Should accept the word Sentry uses for the same filter."""
    assert query.parse("assigned:me").terms == (query.Term("owner", "me"),)


# negation, wildcards and comparisons


def test_a_negated_term_removes_what_it_matches(make_issue):
    """Should be the fastest way to hide one noisy value from the stream."""
    make_issue(title="Loud", level=models.Level.ERROR)
    make_issue(title="Quiet", level=models.Level.WARNING)

    result = titles("!level:error")
    expected = ["Quiet"]

    assert result == expected


def test_a_negated_term_is_parsed_as_one(make_issue):
    """Should keep the negation on the term rather than in the free text."""
    assert query.parse("!level:error").terms == (
        query.Term("level", "error", negated=True),
    )


def test_negated_free_text_removes_matching_titles(make_issue):
    """Should let a reader drop a whole family of titles in one word."""
    make_issue(title="Checkout failed")
    make_issue(title="Login failed")

    result = titles("!Checkout")
    expected = ["Login failed"]

    assert result == expected


def test_a_leading_wildcard_matches_the_end(make_issue):
    """Should match the way Sentry's glob does, not as a regular expression."""
    make_issue(title="payments-api down")
    make_issue(title="ledger-api up")

    result = titles("project:*")
    expected = ["ledger-api up", "payments-api down"]

    assert result == expected


def test_a_wildcard_matches_inside_a_tag_value(make_issue):
    """Should let one term cover every pod in a deployment."""
    issue = make_issue(title="Crash")
    models.TagStat.objects.create(issue=issue, key="pod", value="api-7d9f-abc", count=1)
    other = make_issue(title="Other")
    models.TagStat.objects.create(issue=other, key="pod", value="worker-1", count=1)

    result = titles("tag:pod=api-*")
    expected = ["Crash"]

    assert result == expected


def test_a_wildcard_in_the_middle_still_matches(make_issue):
    """Should handle a pattern with text on both sides of the star."""
    make_issue(title="payments checkout failure")
    make_issue(title="ledger import failure")

    result = titles("payments*failure")
    expected = ["payments checkout failure"]

    assert result == expected


def test_a_value_with_regex_characters_is_matched_literally(make_issue):
    """Should not read a title as a pattern just because it has punctuation."""
    make_issue(title="C++ compiler crashed")
    make_issue(title="C compiler crashed")

    result = titles("C++*")
    expected = ["C++ compiler crashed"]

    assert result == expected


def test_an_events_comparison_filters_on_the_count(make_issue):
    """Should answer 'show me what actually happens a lot'."""
    make_issue(title="Loud", event_count=500)
    make_issue(title="Quiet", event_count=2)

    result = titles("events:>100")
    expected = ["Loud"]

    assert result == expected


def test_a_users_comparison_filters_on_reach(make_issue):
    """Should be the filter an alert on people affected is built from."""
    make_issue(title="Everyone", user_count=400)
    make_issue(title="One", user_count=1)

    result = titles("users:>=100")
    expected = ["Everyone"]

    assert result == expected


def test_count_is_an_alias_for_events(make_issue):
    """Should accept the word Sentry's own syntax uses."""
    make_issue(title="Loud", event_count=500)
    make_issue(title="Quiet", event_count=2)

    result = titles("count:>100")
    expected = ["Loud"]

    assert result == expected


def test_a_bare_number_means_exactly_that_many(make_issue):
    """Should not silently turn an equality into a threshold."""
    make_issue(title="Three", event_count=3)
    make_issue(title="Four", event_count=4)

    result = titles("events:3")
    expected = ["Three"]

    assert result == expected


def test_an_unusable_comparison_is_named_back(make_issue):
    """Should tell the reader the term was dropped."""
    make_issue(title="Three", event_count=3)

    _, rejected = run("events:>lots")

    assert rejected == ["events:>lots"]


def test_an_array_value_matches_any_member(make_issue):
    """Should be one term where a reader would otherwise repeat the key."""
    make_issue(title="Error", level=models.Level.ERROR)
    make_issue(title="Fatal", level=models.Level.FATAL)
    make_issue(title="Info", level=models.Level.INFO)

    result = titles("level:[error,fatal]")
    expected = ["Error", "Fatal"]

    assert result == expected


def test_has_finds_issues_carrying_a_tag_key(make_issue):
    """Should answer 'which of these even record a release'."""
    issue = make_issue(title="Tagged")
    models.TagStat.objects.create(issue=issue, key="release", value="1.2.3", count=1)
    make_issue(title="Bare")

    result = titles("has:release")
    expected = ["Tagged"]

    assert result == expected


def test_negated_has_finds_the_rest(make_issue):
    """Should be how a reader finds the events that lost their release tag."""
    issue = make_issue(title="Tagged")
    models.TagStat.objects.create(issue=issue, key="release", value="1.2.3", count=1)
    make_issue(title="Bare")

    result = titles("!has:release")
    expected = ["Bare"]

    assert result == expected


def test_has_owner_finds_assigned_issues(make_issue, django_user_model):
    """Should not need a tag for the one relation people ask about most."""
    from pandora.people import models as people_models

    user = django_user_model.objects.create_user(username="dev")
    issue = make_issue(title="Owned")
    people_models.Assignment.objects.create(issue=issue, user=user)
    make_issue(title="Nobody's")

    result = titles("has:owner")
    expected = ["Owned"]

    assert result == expected


def test_a_tag_shortcut_reads_like_sentrys(make_issue):
    """Should let release:1.2.3 work without the tag: prefix."""
    issue = make_issue(title="Old build")
    models.TagStat.objects.create(issue=issue, key="release", value="1.2.3", count=1)
    make_issue(title="Untagged")

    result = titles("release:1.2.3")
    expected = ["Old build"]

    assert result == expected


def test_the_bracket_tag_syntax_is_accepted(make_issue):
    """Should accept the explicit form Sentry documents for ambiguous keys."""
    issue = make_issue(title="Crash")
    models.TagStat.objects.create(issue=issue, key="pod", value="api-1", count=1)
    make_issue(title="Other")

    result = titles("tags[pod]:api-1")
    expected = ["Crash"]

    assert result == expected


def test_an_issue_matching_two_tag_terms_is_listed_once(make_issue):
    """Should not multiply rows by the joins the filters needed."""
    issue = make_issue(title="Crash")
    models.TagStat.objects.create(issue=issue, key="pod", value="api-1", count=1)
    models.TagStat.objects.create(issue=issue, key="release", value="1.2.3", count=1)

    result = titles("tag:pod=api-1 release:1.2.3")
    expected = ["Crash"]

    assert result == expected
