import datetime

import pytest

from pandora.core import models as core_models
from pandora.issues import detail, models

pytestmark = pytest.mark.django_db

NOW = datetime.datetime(2026, 8, 29, 12, 0, tzinfo=datetime.UTC)


@pytest.fixture
def issue(project):
    return models.Issue.objects.create(
        project=project,
        fingerprint_hash="abc123",
        fingerprint=["alertname:KubePodCrashLooping"],
        grouping_labels={"alertname": "KubePodCrashLooping", "namespace": "payments"},
        title="KubePodCrashLooping",
        culprit="alertname=KubePodCrashLooping",
        level=models.Level.ERROR,
        environment="p-mk1",
        first_seen=NOW - datetime.timedelta(hours=2),
        last_seen=NOW,
    )


def link(**overrides):
    fields = {
        "name": "Grafana",
        "url_template": "https://grafana.test/d/x?var-ns={namespace}",
    }
    fields.update(overrides)
    return core_models.ServiceLink.objects.create(**fields)


def hrefs(issue):
    return {row.label: row.href for row in detail.build(issue).links}


# what a template can interpolate


def test_a_configured_link_is_rendered_from_the_grouping_labels(issue):
    """Should turn one issue row into an outbound link with the label already filled in."""
    link()

    result = hrefs(issue)["Grafana"]
    expected = "https://grafana.test/d/x?var-ns=payments"

    assert result == expected


def test_a_tag_value_is_available_even_when_grouping_dropped_it(issue):
    """Should reach the pod name, which grouping deliberately drops but a log query needs."""
    models.TagStat.objects.create(issue=issue, key="pod", value="ledger-1", count=9)
    link(url_template="https://loki.test/?q={pod}")

    result = hrefs(issue)["Grafana"]
    expected = "https://loki.test/?q=ledger-1"

    assert result == expected


def test_the_most_frequent_tag_value_wins(issue):
    """Should pick the value the issue is mostly about rather than whichever row came back first."""
    models.TagStat.objects.create(issue=issue, key="pod", value="rare", count=1)
    models.TagStat.objects.create(issue=issue, key="pod", value="common", count=40)
    link(url_template="https://loki.test/?q={pod}")

    result = hrefs(issue)["Grafana"]
    expected = "https://loki.test/?q=common"

    assert result == expected


def test_the_overflow_bucket_is_never_interpolated(issue):
    """Should not build a link to a literal placeholder — a key whose values never repeat collapses to one."""
    models.TagStat.objects.create(
        issue=issue, key="request_id", value=models.TAG_OVERFLOW_VALUE, count=99
    )
    link(url_template="https://loki.test/?q={request_id}")

    result = hrefs(issue)
    expected = {}

    assert result == expected


def test_a_grouping_label_beats_a_tag_of_the_same_name(issue):
    """Should prefer what the issue is defined by over what its occurrences happened to carry."""
    models.TagStat.objects.create(issue=issue, key="namespace", value="other", count=99)
    link()

    result = hrefs(issue)["Grafana"]
    expected = "https://grafana.test/d/x?var-ns=payments"

    assert result == expected


def test_the_issue_id_and_fingerprint_are_available(issue):
    """Should let a link point back at the issue, which is what a ticket or a chat message wants."""
    link(url_template="https://tickets.test/new?ref={issue}-{fingerprint}")

    result = hrefs(issue)["Grafana"]
    expected = f"https://tickets.test/new?ref={issue.pk}-abc123"

    assert result == expected


def test_the_padded_window_is_wider_than_the_exact_one(issue):
    """Should give a dashboard room either side — a query that starts exactly at the first occurrence shows no run-up."""
    link(url_template="{padded_from_ms}:{padded_to_ms}")
    exact = link(name="Exact", url_template="{from_ms}:{to_ms}")

    rendered = hrefs(issue)
    padded_from, padded_to = (int(part) for part in rendered["Grafana"].split(":"))
    exact_from, exact_to = (int(part) for part in rendered[exact.name].split(":"))

    result = (padded_from < exact_from, padded_to > exact_to)
    expected = (True, True)

    assert result == expected


# which links appear


def test_a_template_referencing_a_missing_key_renders_no_link(issue):
    """Should leave the button off rather than linking somewhere broken."""
    link(url_template="https://grafana.test/?node={node}")

    result = hrefs(issue)
    expected = {}

    assert result == expected


def test_an_inactive_link_is_not_rendered(issue):
    """Should let a link be turned off without deleting the template."""
    link(active=False)

    result = hrefs(issue)
    expected = {}

    assert result == expected


def test_a_link_scoped_to_another_project_is_not_rendered(issue):
    """Should keep one team's dashboards off another team's issues."""
    other = core_models.Project.objects.create(slug="apps", name="Applications")
    link(project=other)

    result = hrefs(issue)
    expected = {}

    assert result == expected


def test_a_link_scoped_to_this_project_is_rendered(issue, project):
    """Should let a project carry links the others do not."""
    link(project=project)

    result = hrefs(issue)["Grafana"]
    expected = "https://grafana.test/d/x?var-ns=payments"

    assert result == expected


def test_an_unscoped_link_is_rendered_for_every_project(issue):
    """Should let one template cover the whole install, which is the common case."""
    link(project=None)

    result = list(hrefs(issue))
    expected = ["Grafana"]

    assert result == expected


def test_links_come_back_in_the_configured_order(issue):
    """Should let an operator put the one they click most first."""
    link(name="Second", ordering=200)
    link(name="First", ordering=10)

    result = [row.label for row in detail.build(issue).links]
    expected = ["First", "Second"]

    assert result == expected


def test_the_settings_templates_still_work(issue, settings):
    """Should keep the two environment-driven links working for an install that predates the table."""
    settings.PANDORA_GRAFANA_URL = "https://grafana.test/?ns={namespace}"
    settings.PANDORA_LOKI_QUERY_URL = "https://loki.test/?ns={namespace}"

    result = sorted(hrefs(issue))
    expected = ["Grafana", "Loki"]

    assert result == expected
