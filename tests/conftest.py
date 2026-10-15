import datetime
import json
import pathlib

import pytest
from django import db
from django.utils import timezone

from pandora.core import models as core_models
from pandora.events import postgres_store
from pandora.issues import environments
from pandora.issues import models as issue_models

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures"
FIXTURE_MONTHS = ((datetime.date(2026, 8, 1), datetime.date(2026, 9, 1)),)


def needs_fixture_months(request):
    if "transactional_db" in request.fixturenames:
        return False
    if request.node.get_closest_marker("clock_partitions") is not None:
        return False
    if "db" in request.fixturenames:
        return True
    marker = request.node.get_closest_marker("django_db")
    if marker is None:
        return False
    return not marker.kwargs.get("transaction", False)


@pytest.fixture(autouse=True)
def fixture_month_partitions(request):
    if not needs_fixture_months(request):
        return
    request.getfixturevalue("db")
    if db.connection.vendor != "postgresql":
        return
    with db.connection.cursor() as cursor:
        for start, end in FIXTURE_MONTHS:
            cursor.execute(
                postgres_store.PARTITION.format(
                    suffix=f"{start.year}_{start.month:02d}",
                    start=start.isoformat(),
                    end=end.isoformat(),
                )
            )


@pytest.fixture
def am_fixture():
    def load(name):
        path = FIXTURE_DIR / "am" / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    return load


@pytest.fixture
def project(db):
    return core_models.Project.objects.create(
        slug="infrastructure",
        name="Infrastructure",
    )


@pytest.fixture
def token(project):
    return core_models.IngestToken.objects.create(
        project=project,
        name="p-mk1 alertmanager",
        token="test-ingest-token",
        source=core_models.TokenSource.AM,
        scope=core_models.TokenScope.INGEST,
        environment="p-mk1",
    )


@pytest.fixture
def issue(project):
    now = timezone.now()
    built = issue_models.Issue.objects.create(
        project=project,
        fingerprint_hash="a" * 64,
        fingerprint=["alertname:TargetDown", "namespace:monitoring"],
        grouping_labels={"alertname": "TargetDown", "namespace": "monitoring"},
        title="TargetDown: scrape target unreachable",
        culprit="alertname=TargetDown namespace=monitoring",
        level=issue_models.Level.WARNING,
        environment="p-mk1",
        first_seen=now - datetime.timedelta(hours=6),
        last_seen=now,
        event_count=3,
        open_episode_count=1,
        source_state=issue_models.SourceState.FIRING,
        triage_state=issue_models.TriageState.NEW,
    )
    environments.record(built, built.environment, built.last_seen)
    return built


@pytest.fixture
def episode(issue):
    now = timezone.now()
    return issue_models.Episode.objects.create(
        project=issue.project,
        issue=issue,
        am_fingerprint="3c1f6a2b9d4e5087",
        labels={"alertname": "TargetDown", "job": "node-exporter"},
        environment="p-mk1",
        starts_at=now - datetime.timedelta(hours=2),
        ends_at=None,
        delivery_count=2,
        last_delivery_at=now,
    )


@pytest.fixture(autouse=True)
def artifact_root(settings, tmp_path):
    """Keep uploaded bundles in the test's own directory, never in the checkout."""
    settings.MEDIA_ROOT = str(tmp_path / "artifacts")
