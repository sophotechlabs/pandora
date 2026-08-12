import http

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

PAGES = ["/", "/overview/", "/ingest/"]


@pytest.mark.parametrize("path", PAGES)
def test_every_page_asks_a_stranger_to_sign_in(client, path):
    """Should keep the whole operator surface behind a session."""
    response = client.get(path)

    result = (response.status_code, response.url)
    expected = (http.HTTPStatus.FOUND, f"/login/?next={path}")

    assert result == expected


def test_an_issue_page_asks_a_stranger_to_sign_in(client, make_issue):
    """Should not leak an issue title to an unauthenticated reader."""
    issue = make_issue()

    response = client.get(f"/issues/{issue.pk}/")

    assert response.status_code == http.HTTPStatus.FOUND
    assert response.url == f"/login/?next=/issues/{issue.pk}/"


def test_a_signed_in_reader_without_staff_is_sent_to_the_login_page(client, reader):
    """Should mirror the admin rule — staff only, no half-open surface."""
    client.force_login(reader)

    response = client.get("/")

    assert response.status_code == http.HTTPStatus.FOUND
    assert response.url.startswith("/login/")


def test_a_staff_operator_reaches_the_stream(operator_client):
    """Should let anyone who could use the admin use the UI."""
    response = operator_client.get("/")

    result = response.status_code
    expected = http.HTTPStatus.OK

    assert result == expected


def test_the_login_page_renders_its_own_chrome(client):
    """Should look like pandora, not like the Django admin."""
    body = client.get("/login/").content.decode()

    assert "pandora" in body
    assert "auth-form" in body


def test_a_bad_password_says_so_without_naming_the_account(client, operator):
    """Should not confirm which half of the pair was wrong."""
    response = client.post(
        reverse("ui:login"),
        {"username": "operator", "password": "wrong"},
    )
    body = response.content.decode()

    assert response.status_code == http.HTTPStatus.OK
    assert "do not match an account" in body


def test_signing_in_lands_on_the_stream(client, operator):
    """Should drop an operator straight into the triage queue."""
    response = client.post(
        reverse("ui:login"),
        {"username": "operator", "password": "operator-pass"},
    )

    result = (response.status_code, response.url)
    expected = (http.HTTPStatus.FOUND, "/")

    assert result == expected


def test_signing_out_needs_a_post(operator_client):
    """Should keep a prefetch or a stray link from ending the session."""
    response = operator_client.get(reverse("ui:logout"))

    assert response.status_code == http.HTTPStatus.METHOD_NOT_ALLOWED


def test_signing_out_returns_to_the_login_page(operator_client):
    """Should leave the operator somewhere they can sign back in."""
    response = operator_client.post(reverse("ui:logout"))

    result = (response.status_code, response.url)
    expected = (http.HTTPStatus.FOUND, "/login/")

    assert result == expected
