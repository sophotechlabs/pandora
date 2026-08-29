import datetime

import pytest
from django.utils import timezone

from pandora.issues import models
from pandora.notify.models import Destination


@pytest.fixture
def make_issue(project):
    def build(**overrides):
        title = overrides.pop("title", "boom")
        fields = {
            "project": project,
            "fingerprint_hash": title,
            "fingerprint": [title],
            "title": title,
            "culprit": title,
            "level": models.Level.ERROR,
            "environment": "p-mk1",
            "first_seen": timezone.now() - datetime.timedelta(hours=1),
            "last_seen": timezone.now(),
            "event_count": 1,
        }
        fields.update(overrides)
        return models.Issue.objects.create(**fields)

    return build


@pytest.fixture
def make_destination():
    def build(**overrides):
        fields = {
            "name": "ops",
            "target": "https://hooks.example.test/pandora",
        }
        fields.update(overrides)
        return Destination.objects.create(**fields)

    return build
