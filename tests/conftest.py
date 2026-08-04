import datetime
import json
import pathlib

import pytest
from django.utils import timezone

from pandora.core import models as core_models
from pandora.issues import models as issue_models

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures"


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
    return issue_models.Issue.objects.create(
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
