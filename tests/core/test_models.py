import pytest
from django import db

from pandora.core import models

# field contract


def test_ingest_token_defaults_to_alertmanager_ingest(token):
    """Should default a new token to the Alertmanager ingest scope, active."""
    result = {
        "source": token.source,
        "scope": token.scope,
        "active": token.active,
    }
    expected = {"source": "am", "scope": "ingest", "active": True}

    assert result == expected


def test_token_sources_are_the_two_front_doors():
    """Should offer exactly the Alertmanager and SDK sources."""
    result = list(models.TokenSource.values)
    expected = ["am", "sdk"]

    assert result == expected


def test_token_scopes_separate_ingest_from_read():
    """Should offer exactly the ingest and read scopes."""
    result = list(models.TokenScope.values)
    expected = ["ingest", "read"]

    assert result == expected


@pytest.mark.django_db
def test_dsn_key_starts_active(project):
    """Should mark a freshly minted DSN key as usable."""
    key = models.DsnKey.objects.create(project=project, public_key="0123456789abcdef")

    assert key.active is True


# uniqueness


@pytest.mark.django_db
def test_project_slug_is_unique():
    """Should refuse a second project with an existing slug."""
    models.Project.objects.create(slug="infrastructure", name="Infrastructure")

    with pytest.raises(db.IntegrityError, match="slug"):
        models.Project.objects.create(slug="infrastructure", name="Duplicate")


# display


def test_project_shows_its_name(project):
    """Should render a project as its human name."""
    result = str(project)
    expected = "Infrastructure"

    assert result == expected


def test_token_is_shown_project_first(token):
    """Should render a token as project slug over token name."""
    result = str(token)
    expected = "infrastructure/p-mk1 alertmanager"

    assert result == expected


@pytest.mark.django_db
def test_dsn_key_shows_a_truncated_public_key(project):
    """Should render a DSN key without exposing the whole public key."""
    key = models.DsnKey.objects.create(project=project, public_key="0123456789abcdef")

    result = str(key)
    expected = "infrastructure/01234567"

    assert result == expected


@pytest.mark.django_db
def test_a_service_link_is_named_by_its_label():
    """Should read as the button it becomes in the admin's own listings."""
    link = models.ServiceLink.objects.create(
        name="Grafana",
        url_template="https://grafana.test/?ns={namespace}",
    )

    result = str(link)
    expected = "Grafana"

    assert result == expected
