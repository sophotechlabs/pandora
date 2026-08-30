import datetime

import pytest
from django.contrib.auth import models as auth_models
from django.utils import timezone

from pandora.issues import environments
from pandora.issues import models as issue_models
from pandora.people.models import Membership, Role, Team


@pytest.fixture
def make_user(db):
    def build(username, **overrides):
        fields = {
            "username": username,
            "password": f"{username}-pass",
            "is_staff": True,
        }
        fields.update(overrides)
        return auth_models.User.objects.create_user(**fields)

    return build


@pytest.fixture
def make_team(db):
    def build(name="platform", projects=()):
        team, _ = Team.objects.get_or_create(name=name)
        for project in projects:
            team.projects.add(project)
        return team

    return build


@pytest.fixture
def join():
    def build(user, team, role=Role.MEMBER):
        return Membership.objects.create(user=user, team=team, role=role)

    return build


@pytest.fixture
def make_issue(project):
    def build(**overrides):
        title = overrides.pop("title", "boom")
        fields = {
            "project": project,
            "fingerprint_hash": title,
            "fingerprint": [title],
            "grouping_labels": {"namespace": "payments"},
            "title": title,
            "culprit": "checkout.gateway in charge",
            "level": issue_models.Level.ERROR,
            "environment": "p-mk1",
            "first_seen": timezone.now() - datetime.timedelta(hours=1),
            "last_seen": timezone.now(),
            "event_count": 1,
        }
        fields.update(overrides)
        built = issue_models.Issue.objects.create(**fields)
        environments.record(built, built.environment, built.last_seen)
        return built

    return build
