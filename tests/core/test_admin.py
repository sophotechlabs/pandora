import http

import pytest
from django.contrib import admin as django_admin
from django.contrib import messages as django_messages
from django.urls import reverse

from pandora.core import admin, models
from pandora.issues import models as issue_models

pytestmark = pytest.mark.django_db

TOKEN_CHANGELIST = "/admin/core/ingesttoken/"
DSN_CHANGELIST = "/admin/core/dsnkey/"


def notes(response):
    return [
        str(message) for message in django_messages.get_messages(response.wsgi_request)
    ]


# generated secrets


def test_a_generated_token_is_long_enough_to_be_a_bearer_secret():
    """Should hand out 32 bytes of entropy, not a memorable string."""
    result = len(admin._new_token())

    assert result >= 40
    assert admin._new_token() != admin._new_token()


def test_a_generated_dsn_key_fits_the_public_key_column():
    """Should stay inside the frozen 64-character column."""
    result = len(admin._new_public_key())
    expected = 32

    assert result == expected


# project admin


def test_the_project_list_counts_its_issues(admin_client, project, issue):
    """Should show how much each project is carrying without opening it."""
    response = admin_client.get("/admin/core/project/")
    row = response.context["cl"].result_list[0]

    result = (row.issue_total, row.firing_total)
    expected = (1, 1)

    assert result == expected


def test_the_project_list_counts_only_firing_issues(admin_client, project, issue):
    """Should not count a settled issue as firing."""
    issue.source_state = issue_models.SourceState.RESOLVED
    issue.save(update_fields=["source_state"])

    response = admin_client.get("/admin/core/project/")
    row = response.context["cl"].result_list[0]

    result = (row.issue_total, row.firing_total)
    expected = (1, 0)

    assert result == expected


def test_an_empty_project_counts_zero(admin_client, project):
    """Should print a zero rather than a blank cell."""
    response = admin_client.get("/admin/core/project/")
    row = response.context["cl"].result_list[0]

    result = (row.issue_total, row.firing_total)
    expected = (0, 0)

    assert result == expected


# ingest token admin


def test_the_token_form_never_asks_for_the_secret():
    """Should generate the value rather than let a human choose it."""
    result = admin.IngestTokenAdmin.fields

    assert "token" not in result
    assert "token_preview" in result


def test_adding_a_token_generates_and_stores_one(admin_client, project):
    """Should mint the secret on save so the operator never types one."""
    payload = {
        "project": str(project.pk),
        "name": "p-mk1 alertmanager",
        "source": models.TokenSource.AM,
        "scope": models.TokenScope.INGEST,
        "environment": "p-mk1",
        "active": "on",
    }

    response = admin_client.post("/admin/core/ingesttoken/add/", payload)

    token = models.IngestToken.objects.get(name="p-mk1 alertmanager")

    assert response.status_code == http.HTTPStatus.FOUND
    assert len(token.token) >= 40


def test_adding_a_token_shows_the_secret_once(admin_client, project):
    """Should put the value in front of the operator exactly once."""
    payload = {
        "project": str(project.pk),
        "name": "p-mk1 alertmanager",
        "source": models.TokenSource.AM,
        "scope": models.TokenScope.INGEST,
        "environment": "p-mk1",
        "active": "on",
    }

    response = admin_client.post("/admin/core/ingesttoken/add/", payload)
    token = models.IngestToken.objects.get(name="p-mk1 alertmanager")

    result = notes(response)[0]
    expected = (
        f"p-mk1 alertmanager token: {token.token} — copy it now, it is not shown again"
    )

    assert result == expected


def test_the_changelist_shows_only_a_token_prefix(admin_client, token):
    """Should keep the secret out of a page anyone can leave open."""
    body = admin_client.get(TOKEN_CHANGELIST).content.decode()

    assert token.token not in body
    assert f"{token.token[:6]}…" in body


def test_a_token_that_was_never_issued_previews_as_a_dash(project):
    """Should not print an empty box for a row without a value."""
    view = admin.IngestTokenAdmin(models.IngestToken, django_admin.site)
    blank = models.IngestToken(project=project, name="blank", token="")

    result = view.token_preview(blank)
    expected = "—"

    assert result == expected


def test_regenerating_replaces_the_stored_token(admin_client, token):
    """Should rotate the secret in place, keeping the project wiring."""
    original = token.token

    admin_client.post(
        TOKEN_CHANGELIST,
        {"action": "regenerate", "_selected_action": [str(token.pk)], "index": "0"},
    )

    result = models.IngestToken.objects.get(pk=token.pk).token

    assert result != original
    assert len(result) >= 40


def test_regenerating_shows_the_new_token_once(admin_client, token):
    """Should let the operator copy the rotated value straight away."""
    response = admin_client.post(
        TOKEN_CHANGELIST,
        {"action": "regenerate", "_selected_action": [str(token.pk)], "index": "0"},
    )

    rotated = models.IngestToken.objects.get(pk=token.pk)

    result = notes(response)
    expected = [
        f"{rotated.name} token: {rotated.token} — copy it now, it is not shown again"
    ]

    assert result == expected


def test_editing_a_token_keeps_the_secret(admin_client, token):
    """Should not silently rotate a live credential on an unrelated edit."""
    url = reverse("admin:core_ingesttoken_change", args=[token.pk])
    payload = {
        "project": str(token.project_id),
        "name": "renamed",
        "source": token.source,
        "scope": token.scope,
        "environment": token.environment,
        "active": "on",
    }

    admin_client.post(url, payload)

    stored = models.IngestToken.objects.get(pk=token.pk)

    result = (stored.name, stored.token)
    expected = ("renamed", token.token)

    assert result == expected


# dsn key admin


def test_the_dsn_form_never_asks_for_the_public_key():
    """Should generate the key rather than let a human invent one."""
    result = admin.DsnKeyAdmin.readonly_fields

    assert "public_key" in result


def test_adding_a_dsn_key_generates_one(admin_client, project):
    """Should mint a key so the DSN can be assembled straight away."""
    response = admin_client.post(
        "/admin/core/dsnkey/add/",
        {"project": str(project.pk), "active": "on"},
    )

    key = models.DsnKey.objects.get(project=project)

    assert response.status_code == http.HTTPStatus.FOUND
    assert len(key.public_key) == 32


def test_editing_a_dsn_key_keeps_it(admin_client, project):
    """Should not rotate a live DSN because someone toggled the active flag."""
    key = models.DsnKey.objects.create(project=project, public_key="c" * 32)
    url = reverse("admin:core_dsnkey_change", args=[key.pk])

    admin_client.post(url, {"project": str(project.pk)})

    stored = models.DsnKey.objects.get(pk=key.pk)

    result = (stored.public_key, stored.active)
    expected = ("c" * 32, False)

    assert result == expected


def test_the_dsn_list_shows_the_envelope_path(admin_client, project):
    """Should spell out where an SDK points without reading the router."""
    key = models.DsnKey.objects.create(project=project, public_key="a" * 32)
    view = admin.DsnKeyAdmin(models.DsnKey, django_admin.site)

    result = view.envelope_path(key)
    expected = f"/api/{project.pk}/envelope/"

    assert result == expected


def test_the_dsn_changelist_renders(admin_client, project):
    """Should list keys without a template or query error."""
    models.DsnKey.objects.create(project=project, public_key="b" * 32)

    response = admin_client.get(DSN_CHANGELIST)

    result = response.status_code
    expected = http.HTTPStatus.OK

    assert result == expected
