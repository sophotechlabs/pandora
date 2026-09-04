import pytest
from django import db

from pandora.core import models

# field contract


def test_ingest_token_defaults_to_alertmanager_ingest(token):
    """Should default a new token to the Alertmanager ingest scope, active."""
    result = {
        "source": token.source,
        "scopes": token.scopes,
        "active": token.active,
    }
    expected = {"source": "am", "scopes": ("artifacts", "ingest"), "active": True}

    assert result == expected


def test_token_sources_name_every_front_door():
    """Should name each way an occurrence can arrive, so the ingest page can split them."""
    result = list(models.TokenSource.values)
    expected = ["am", "sdk", "log", "cron", "otlp", "ci"]

    assert result == expected


def test_token_scopes_are_independent_capabilities():
    """Should let one automation token carry only the capabilities it needs."""
    result = list(models.TokenScope.values)
    expected = ["ingest", "artifacts", "deploy", "read", "payload"]

    assert result == expected


@pytest.mark.django_db
def test_a_token_without_an_explicit_capability_keeps_legacy_access(project):
    token = models.IngestToken.objects.create(
        project=project,
        name="legacy default",
        token="legacy-default-token",
    )

    assert token.scopes == ("artifacts", "ingest")


@pytest.mark.django_db(databases="__all__")
def test_capability_writes_stay_on_the_selected_database():
    alias = next(
        name for name in db.connections if db.connections[name].vendor == "sqlite"
    )
    project = models.Project.objects.using(alias).create(
        slug="selected-database",
        name="Selected database",
    )

    token = models.IngestToken.objects.db_manager(alias).create(
        project=project,
        name="selected database token",
        token="selected-database-token",
        scopes=(models.TokenScope.READ, models.TokenScope.PAYLOAD),
    )

    assert token.scopes == ("payload", "read")


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
