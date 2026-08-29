import datetime

import pytest
from django.utils import timezone

from pandora.issues import models


@pytest.fixture
def make_issue(project):
    def build(**overrides):
        title = overrides.pop("title", "An issue")
        fields = {
            "project": project,
            "fingerprint_hash": title,
            "fingerprint": [title],
            "grouping_labels": {"namespace": "payments"},
            "title": title,
            "culprit": title,
            "level": models.Level.ERROR,
            "environment": "p-mk1",
            "first_seen": timezone.now() - datetime.timedelta(hours=2),
            "last_seen": timezone.now(),
        }
        fields.update(overrides)
        return models.Issue.objects.create(**fields)

    return build
