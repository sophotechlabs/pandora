import datetime
import hashlib
import itertools

import freezegun
import pytest
from django.contrib.auth import models as auth_models
from django.utils import timezone

from pandora.core import models as core_models
from pandora.issues import environments
from pandora.issues import models as issue_models

FROZEN = "2026-08-04 12:00:00"


@pytest.fixture
def other_project(db):
    return core_models.Project.objects.create(slug="apps", name="Applications")


@pytest.fixture(autouse=True)
def frozen():
    with freezegun.freeze_time(FROZEN):
        yield


@pytest.fixture
def make_issue(project):
    counter = itertools.count(1)

    def build(**overrides):
        index = next(counter)
        title = overrides.pop("title", f"Alert{index} is firing")
        now = timezone.now()
        fields = {
            "project": project,
            "fingerprint_hash": hashlib.sha256(title.encode()).hexdigest(),
            "fingerprint": [f"alertname:Alert{index}"],
            "grouping_labels": {"alertname": f"Alert{index}"},
            "title": title,
            "culprit": f"alertname=Alert{index}",
            "level": issue_models.Level.WARNING,
            "environment": "p-mk1",
            "first_seen": now - datetime.timedelta(hours=2),
            "last_seen": now,
            "event_count": 3,
            "open_episode_count": 1,
            "source_state": issue_models.SourceState.FIRING,
            "triage_state": issue_models.TriageState.NEW,
        }
        fields.update(overrides)
        built = issue_models.Issue.objects.create(**fields)
        environments.record(built, built.environment, built.last_seen)
        return built

    return build


OPERATOR_PERMISSIONS = ("issues.change_issue", "ingest.change_rawenvelope")


@pytest.fixture
def operator(db):
    user = auth_models.User.objects.create_user(
        username="operator",
        password="operator-pass",
        is_staff=True,
    )
    for label in OPERATOR_PERMISSIONS:
        app_label, codename = label.split(".")
        user.user_permissions.add(
            auth_models.Permission.objects.get(
                content_type__app_label=app_label,
                codename=codename,
            )
        )
    return user


@pytest.fixture
def operator_client(client, operator):
    client.force_login(operator)
    return client


@pytest.fixture
def reader(db):
    return auth_models.User.objects.create_user(
        username="reader",
        password="reader-pass",
        is_staff=False,
    )
