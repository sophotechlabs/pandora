import datetime

import pytest
from django.utils import timezone
from prometheus_client import REGISTRY

from pandora.core.models import Project
from pandora.releases import models, reporting

pytestmark = pytest.mark.django_db

NOW = timezone.now()


def record(project, *, days_ago=0, environment="production", state=None):
    release = models.Release.objects.create(
        project=project,
        version=f"1.2.{models.Release.objects.count()}",
    )
    deploy_state = state
    if deploy_state is None:
        deploy_state = models.DeployState.SUCCEEDED
    return models.Deploy.objects.create(
        release=release,
        environment=environment,
        state=deploy_state,
        started_at=NOW - datetime.timedelta(days=days_ago),
    )


def test_successful_deploys_are_counted_over_thirty_days(project):
    record(project, days_ago=1)
    record(project, days_ago=29)
    record(project, days_ago=31)

    result = reporting.counts(NOW)
    expected = {("infrastructure", "production"): 2}

    assert result == expected


def test_failed_and_unfinished_deploys_do_not_raise_the_frequency(project):
    record(project, state=models.DeployState.FAILED)
    record(project, state=models.DeployState.STARTED)

    result = reporting.counts(NOW)
    expected = {("infrastructure", "production"): 0}

    assert result == expected


def test_projects_and_environments_are_separate_series(project):
    application = Project.objects.create(slug="application", name="Application")
    record(project, environment="production")
    record(project, environment="staging")
    record(application, environment="production")

    result = reporting.counts(NOW)
    expected = {
        ("application", "production"): 1,
        ("infrastructure", "production"): 1,
        ("infrastructure", "staging"): 1,
    }

    assert result == expected


def test_future_deploys_are_not_counted(project):
    deploy = record(project)
    deploy.started_at = NOW + datetime.timedelta(days=1)
    deploy.save(update_fields=["started_at"])

    result = reporting.counts(NOW)
    expected = {("infrastructure", "production"): 0}

    assert result == expected


def test_refresh_publishes_the_rate_and_sample_size(project):
    record(project, days_ago=1)
    record(project, days_ago=2)
    record(project, days_ago=3)

    result = reporting.refresh(NOW)

    frequency = REGISTRY.get_sample_value(
        "pandora_deploys_per_day",
        {"project": "infrastructure", "environment": "production"},
    )
    total = REGISTRY.get_sample_value(
        "pandora_successful_deploys",
        {"project": "infrastructure", "environment": "production"},
    )
    assert result == {("infrastructure", "production"): 0.1}
    assert frequency == 0.1
    assert total == 3
