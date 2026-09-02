import datetime

import pytest
from django.utils import timezone

from pandora.ingest import client_reports
from pandora.ingest.models import ClientDiscard

pytestmark = pytest.mark.django_db

NOW = timezone.now()


def report(*entries):
    return {"timestamp": NOW.isoformat(), "discarded_events": list(entries)}


def entry(reason="sample_rate", category="error", quantity=1):
    return {"reason": reason, "category": category, "quantity": quantity}


def test_a_report_is_bucketed_by_project_category_reason_and_hour(project):
    accepted = client_reports.accept(project, report(entry(quantity=3)), NOW)

    row = ClientDiscard.objects.get()
    result = (accepted, row.category, row.reason, row.quantity, row.hour)
    expected = (
        3,
        "error",
        "sample_rate",
        3,
        NOW.replace(minute=0, second=0, microsecond=0),
    )

    assert result == expected


def test_repeated_reports_increment_the_existing_bucket(project):
    client_reports.accept(project, report(entry(quantity=2)), NOW)
    client_reports.accept(project, report(entry(quantity=3)), NOW)

    result = (ClientDiscard.objects.count(), ClientDiscard.objects.get().quantity)
    expected = (1, 5)

    assert result == expected


def test_duplicate_entries_in_one_report_are_aggregated(project):
    accepted = client_reports.accept(
        project,
        report(entry(quantity=2), entry(quantity=3)),
        NOW,
    )

    result = (accepted, ClientDiscard.objects.get().quantity)
    expected = (5, 5)

    assert result == expected


@pytest.mark.parametrize(
    "discard",
    [
        None,
        [],
        {"reason": "", "category": "error", "quantity": 1},
        {"reason": "sample_rate", "category": "", "quantity": 1},
        {"reason": "sample_rate", "category": "error", "quantity": True},
        {"reason": "sample_rate", "category": "error", "quantity": 1.5},
        {"reason": "sample_rate", "category": "error", "quantity": 0},
        {
            "reason": "sample_rate",
            "category": "error",
            "quantity": client_reports.MAX_QUANTITY + 1,
        },
    ],
)
def test_invalid_entries_are_ignored(project, discard):
    accepted = client_reports.accept(project, report(discard), NOW)

    assert accepted == 0
    assert ClientDiscard.objects.exists() is False


def test_a_report_cannot_create_more_than_the_entry_limit(project):
    entries = [entry(reason=f"reason-{number}") for number in range(150)]

    accepted = client_reports.accept(project, report(*entries), NOW)

    assert accepted == client_reports.MAX_ENTRIES
    assert ClientDiscard.objects.count() == client_reports.MAX_ENTRIES


def test_prune_removes_only_buckets_before_the_cutoff(project):
    client_reports.accept(project, report(entry()), NOW - datetime.timedelta(days=91))
    client_reports.accept(
        project,
        report(entry(reason="network_error")),
        NOW - datetime.timedelta(days=89),
    )

    removed = client_reports.prune(NOW - datetime.timedelta(days=90))

    assert removed == 1
    assert list(ClientDiscard.objects.values_list("reason", flat=True)) == [
        "network_error"
    ]
