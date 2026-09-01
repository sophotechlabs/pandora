import http

import pytest
from django.contrib.auth import models as auth_models

from pandora.people import oidc
from pandora.people.models import AuditEntry

pytestmark = pytest.mark.django_db


def test_the_web_app_can_load_without_the_optional_oidc_dependency(
    monkeypatch, settings
):
    settings.PANDORA_OIDC_ISSUER = "https://identity.example.test"
    settings.PANDORA_OIDC_CLIENT_ID = "pandora"
    settings.PANDORA_OIDC_CLIENT_SECRET = "secret"

    def missing_authlib(_name):
        raise ModuleNotFoundError("authlib")

    monkeypatch.setattr(oidc, "import_module", missing_authlib)

    with pytest.raises(oidc.OidcError, match="not installed"):
        oidc.client()


CONFIGURED = {
    "PANDORA_OIDC_ISSUER": "https://keycloak.test/realms/pandora",
    "PANDORA_OIDC_CLIENT_ID": "pandora",
    "PANDORA_OIDC_CLIENT_SECRET": "shh",
}


class FakeProvider:
    def __init__(self, token=None, error=None):
        self.token = token
        self.error = error
        self.redirect_uri = None

    def authorize_redirect(self, request, redirect_uri):
        from django.shortcuts import redirect

        self.redirect_uri = redirect_uri
        return redirect(f"https://keycloak.test/auth?redirect_uri={redirect_uri}")

    def authorize_access_token(self, request):
        if self.error:
            raise self.error
        return self.token


@pytest.fixture
def configured(settings):
    for name, value in CONFIGURED.items():
        setattr(settings, name, value)
    return settings


@pytest.fixture
def provider(monkeypatch):
    def install(token=None, error=None):
        fake = FakeProvider(token=token, error=error)
        monkeypatch.setattr(oidc, "client", lambda: fake)
        return fake

    return install


# starting the flow


def test_the_login_page_offers_sso_when_it_is_configured(client, configured):
    """Should be one button — nobody types an issuer URL into a login form."""
    body = client.get("/login/").content.decode()

    assert "single sign-on" in body


def test_the_login_page_offers_nothing_when_sso_is_off(client):
    """Should not show a button that answers 404."""
    body = client.get("/login/").content.decode()

    assert "single sign-on" not in body


def test_starting_the_flow_redirects_to_the_provider(client, configured, provider):
    """Should hand the browser straight to the provider, no interstitial."""
    provider()

    response = client.get("/sso/")

    result = response.status_code
    expected = http.HTTPStatus.FOUND

    assert result == expected


def test_the_provider_is_told_where_to_come_back(client, configured, provider):
    """Should send the callback this install actually serves."""
    fake = provider()

    client.get("/sso/")

    result = fake.redirect_uri
    expected = "http://testserver/sso/callback/"

    assert result == expected


def test_starting_the_flow_while_sso_is_off_is_not_found(client):
    """Should not exist as a route when the operator did not configure it."""
    result = client.get("/sso/").status_code
    expected = http.HTTPStatus.NOT_FOUND

    assert result == expected


# coming back


def test_a_good_callback_signs_the_person_in(client, configured, provider):
    """Should end on the stream, signed in, with no second password prompt."""
    provider(token={"userinfo": {"preferred_username": "dev"}})

    response = client.get("/sso/callback/")

    result = (response.status_code, response.url)
    expected = (http.HTTPStatus.FOUND, "/")

    assert result == expected


def test_a_good_callback_leaves_a_usable_session(client, configured, provider):
    """Should not bounce the person back to the login page on the next click."""
    provider(token={"userinfo": {"preferred_username": "dev"}})
    client.get("/sso/callback/")

    result = client.get("/").status_code
    expected = http.HTTPStatus.OK

    assert result == expected


def test_a_good_callback_creates_the_account(client, configured, provider):
    """Should not need the operator to pre-create every colleague."""
    provider(token={"userinfo": {"preferred_username": "dev"}})

    client.get("/sso/callback/")

    assert auth_models.User.objects.filter(username="dev").exists()


def test_the_sign_in_is_recorded(client, configured, provider):
    """Should answer "who has access to this" from the history page."""
    provider(token={"userinfo": {"preferred_username": "dev"}})

    client.get("/sso/callback/")

    entry = AuditEntry.objects.get()
    result = (entry.actor, entry.action, entry.data)
    expected = ("dev", "auth.sign-in", {"via": "oidc"})

    assert result == expected


def test_a_refused_token_returns_to_the_login_page(client, configured, provider):
    """Should not show a traceback to someone who cancelled the provider prompt."""
    provider(error=RuntimeError("access_denied"))

    response = client.get("/sso/callback/")

    result = (response.status_code, response.url)
    expected = (http.HTTPStatus.FOUND, "/login/")

    assert result == expected


def test_a_refused_token_says_why(client, configured, provider):
    """Should tell the person what the provider said, not just "failed"."""
    provider(error=RuntimeError("access_denied"))

    response = client.get("/sso/callback/", follow=True)

    body = response.content.decode()

    assert "access_denied" in body


def test_a_token_with_no_username_returns_to_the_login_page(
    client, configured, provider
):
    """Should refuse rather than create an account named after nothing."""
    provider(token={"userinfo": {}})

    response = client.get("/sso/callback/")

    result = response.url
    expected = "/login/"

    assert result == expected


def test_a_refused_sign_in_creates_no_account(client, configured, provider):
    """Should leave no half-made account behind after a failure."""
    provider(token={"userinfo": {}})

    client.get("/sso/callback/")

    assert auth_models.User.objects.count() == 0


def test_a_roleless_oidc_account_is_not_signed_in(client, configured, provider):
    configured.PANDORA_OIDC_DEFAULT_ROLE = ""
    provider(token={"userinfo": {"preferred_username": "dev"}})

    response = client.get("/sso/callback/")

    assert response.url == "/login/"
    assert "_auth_user_id" not in client.session
    assert auth_models.User.objects.get(username="dev").is_staff is False


def test_a_callback_while_sso_is_off_is_not_found(client):
    """Should close the route completely, not just the button."""
    result = client.get("/sso/callback/").status_code
    expected = http.HTTPStatus.NOT_FOUND

    assert result == expected


def test_a_password_sign_in_is_recorded(client, operator):
    """Should record every way into the install, not only the new one."""
    client.post("/login/", {"username": "operator", "password": "operator-pass"})

    entry = AuditEntry.objects.get()
    result = (entry.actor, entry.action, entry.data)
    expected = ("operator", "auth.sign-in", {"via": "password"})

    assert result == expected


def test_a_failed_password_sign_in_records_nothing(client, operator):
    """Should not let a guessing attempt fill the log it would be found in."""
    client.post("/login/", {"username": "operator", "password": "wrong"})

    assert AuditEntry.objects.count() == 0


def test_a_sign_out_is_recorded(client, operator):
    """Should show when someone's session ended, not only when it started."""
    client.force_login(operator)

    client.post("/logout/")

    result = [entry.action for entry in AuditEntry.objects.all()]
    expected = ["auth.sign-out", "auth.sign-in"]

    assert result == expected
