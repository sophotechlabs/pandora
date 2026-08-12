import datetime

import pytest
from django.utils import timezone

from pandora.issues import models
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
    expected = (("level", "error"),)

    assert result == expected


def test_terms_and_free_text_can_be_mixed():
    """Should let an operator narrow by filter and by words at once."""
    parsed = query.parse("is:unresolved payments ledger")

    result = (parsed.terms, parsed.text)
    expected = ((("is", "unresolved"),), "payments ledger")

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
    expected = (("level", "error"),)

    assert result == expected


def test_env_is_an_alias_for_environment():
    """Should accept the short form an operator will reach for."""
    parsed = query.parse("env:p-mk1")

    result = parsed.terms
    expected = (("environment", "p-mk1"),)

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
