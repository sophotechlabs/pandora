import datetime
import itertools

import pytest

from pandora.core import models as core_models
from pandora.issues import models as issue_models

BASE_TIME = datetime.datetime(2026, 8, 4, 12, 0, tzinfo=datetime.UTC)


@pytest.fixture
def read_token(project):
    return core_models.IngestToken.objects.create(
        project=project,
        name="spinoza panel",
        token="test-read-token",
        source=core_models.TokenSource.AM,
        scope=core_models.TokenScope.READ,
        environment="p-mk1",
    )


@pytest.fixture
def auth(read_token):
    return {"authorization": f"Bearer {read_token.token}"}


@pytest.fixture
def other_project(db):
    return core_models.Project.objects.create(slug="apps", name="Applications")


@pytest.fixture
def make_issue(project):
    counter = itertools.count(1)

    def build(**overrides):
        index = next(counter)
        fields = {
            "project": project,
            "fingerprint_hash": f"{index:064d}",
            "fingerprint": [f"alertname:Alert{index}"],
            "grouping_labels": {"alertname": f"Alert{index}"},
            "title": f"Alert{index} is firing",
            "culprit": f"alertname=Alert{index}",
            "level": issue_models.Level.WARNING,
            "environment": "p-mk1",
            "first_seen": BASE_TIME,
            "last_seen": BASE_TIME,
            "event_count": 1,
            "open_episode_count": 1,
            "source_state": issue_models.SourceState.FIRING,
            "triage_state": issue_models.TriageState.NEW,
        }
        fields.update(overrides)
        return issue_models.Issue.objects.create(**fields)

    return build


@pytest.fixture
def ladder(make_issue):
    return [
        make_issue(last_seen=BASE_TIME - datetime.timedelta(minutes=step))
        for step in range(5)
    ]
