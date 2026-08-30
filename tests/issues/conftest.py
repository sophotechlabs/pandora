import datetime

import pytest
from django.db import connection
from django.utils import timezone

from pandora.issues import environments
from pandora.issues import models as issue_models

NOW = timezone.now()
FINGERPRINT = "f" * 64


UNIQUE_NAME = "issues_issue_fingerprint_uq"


@pytest.fixture
def unconstrained(db):
    """Recreate the pre-0007 world, where one fingerprint could hold several rows.

    The drop rides in the test's own transaction and disappears when pytest-django
    rolls it back, so there is nothing to put back afterwards — and putting it back
    is what Postgres refuses once rows in the table have pending trigger events.
    SQLite carries a plain unique constraint as part of the table definition, so
    dropping it rebuilds the table — which it will not do inside a transaction.
    The fold itself runs on both backends; only this staging step is Postgres.
    """
    if connection.vendor == "sqlite":
        pytest.skip(
            "staging pre-0007 duplicates needs a constraint drop mid-transaction"
        )
    table = issue_models.Issue._meta.db_table
    with connection.cursor() as cursor:
        cursor.execute(f"ALTER TABLE {table} DROP CONSTRAINT {UNIQUE_NAME}")


@pytest.fixture
def make_twin(unconstrained, project):
    def build(environment, **overrides):
        fields = {
            "project": project,
            "fingerprint_hash": FINGERPRINT,
            "fingerprint": ["alertname:TargetDown"],
            "grouping_labels": {"alertname": "TargetDown"},
            "title": "TargetDown",
            "culprit": "alertname=TargetDown",
            "level": issue_models.Level.WARNING,
            "environment": environment,
            "first_seen": NOW - datetime.timedelta(hours=4),
            "last_seen": NOW,
            "event_count": 3,
        }
        fields.update(overrides)
        built = issue_models.Issue.objects.create(**fields)
        environments.record(
            built, environment, built.last_seen, count=fields["event_count"]
        )
        return built

    return build
