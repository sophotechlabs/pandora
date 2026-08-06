import importlib
import os

import pytest
from django.core import exceptions

MODULE = "pandora.web.settings"


@pytest.fixture
def load_settings():
    module = importlib.import_module(MODULE)
    original = dict(os.environ)

    def load(**env):
        for key, value in env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)
        return importlib.reload(module)

    yield load

    os.environ.clear()
    os.environ.update(original)
    importlib.reload(module)


# pandora configuration


def test_pandora_defaults_are_the_documented_ones(load_settings):
    """Should default retention, envelope retention and the ingest body cap."""
    settings = load_settings(
        PANDORA_RETENTION_DAYS=None,
        PANDORA_ENVELOPE_RETENTION_DAYS=None,
        PANDORA_INGEST_MAX_BYTES=None,
    )

    result = {
        "retention_days": settings.PANDORA_RETENTION_DAYS,
        "envelope_retention_days": settings.PANDORA_ENVELOPE_RETENTION_DAYS,
        "ingest_max_bytes": settings.PANDORA_INGEST_MAX_BYTES,
    }
    expected = {
        "retention_days": 90,
        "envelope_retention_days": 7,
        "ingest_max_bytes": 1048576,
    }
    assert result == expected


def test_pandora_numeric_settings_come_from_the_env(load_settings):
    """Should read the numeric knobs from the environment when set."""
    settings = load_settings(
        PANDORA_RETENTION_DAYS=30,
        PANDORA_INGEST_MAX_BYTES=2048,
    )

    result = (settings.PANDORA_RETENTION_DAYS, settings.PANDORA_INGEST_MAX_BYTES)
    expected = (30, 2048)

    assert result == expected


def test_the_seams_default_to_their_pass_through_implementations(load_settings):
    """Should wire the gate and queue seams to the day-one pass-throughs."""
    settings = load_settings(PANDORA_GATE=None, PANDORA_QUEUE=None)

    result = (settings.PANDORA_GATE, settings.PANDORA_QUEUE)
    expected = (
        "pandora.ingest.gate.PassThroughGate",
        "pandora.ingest.queue.SyncQueue",
    )

    assert result == expected


def test_the_enrichment_urls_default_to_empty(load_settings):
    """Should leave Alertmanager, Grafana and Loki unset until deployed."""
    settings = load_settings(
        PANDORA_AM_URL=None,
        PANDORA_AM_CA_BUNDLE=None,
        PANDORA_GRAFANA_URL=None,
        PANDORA_LOKI_QUERY_URL=None,
    )

    result = [
        settings.PANDORA_AM_URL,
        settings.PANDORA_AM_CA_BUNDLE,
        settings.PANDORA_GRAFANA_URL,
        settings.PANDORA_LOKI_QUERY_URL,
    ]
    expected = ["", "", "", ""]

    assert result == expected


def test_every_pandora_app_is_installed(load_settings):
    """Should install all five pandora apps plus the metrics exporter."""
    settings = load_settings()

    result = [app for app in settings.INSTALLED_APPS if app.startswith("pandora.")]
    expected = [
        "pandora.core",
        "pandora.ingest",
        "pandora.issues",
        "pandora.events",
        "pandora.am",
    ]

    assert result == expected
    assert "django_prometheus" in settings.INSTALLED_APPS


def test_the_prometheus_middleware_brackets_the_stack(load_settings):
    """Should place the metrics middleware first and last to time whole requests."""
    settings = load_settings()

    result = (settings.MIDDLEWARE[0], settings.MIDDLEWARE[-1])
    expected = (
        "django_prometheus.middleware.PrometheusBeforeMiddleware",
        "django_prometheus.middleware.PrometheusAfterMiddleware",
    )

    assert result == expected


# database configuration


def test_sqlite_gets_wal_immediate_and_a_busy_timeout(load_settings):
    """Should harden SQLite for the web and reconcile writers sharing one file."""
    settings = load_settings(DATABASE_URL="sqlite:///pandora.sqlite3")

    result = settings.DATABASES["default"]["OPTIONS"]
    expected = {
        "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
        "transaction_mode": "IMMEDIATE",
        "timeout": 20,
    }

    assert result == expected


def test_postgres_gets_no_sqlite_options(load_settings):
    """Should leave the Postgres connection untouched by the SQLite hardening."""
    settings = load_settings(
        DATABASE_URL="postgres://pandora:pandora@db:5432/pandora",
    )

    result = settings.DATABASES["default"].get("OPTIONS", {})
    expected = {}

    assert result == expected


def test_a_persistent_connection_is_health_checked(load_settings):
    """Should revalidate a pooled connection, or every request after a database
    restart fails until the connection ages out."""
    settings = load_settings(
        DATABASE_URL="postgres://pandora:pandora@db:5432/pandora",
    )

    result = (
        settings.DATABASES["default"]["CONN_MAX_AGE"],
        settings.DATABASES["default"]["CONN_HEALTH_CHECKS"],
    )
    expected = (600, True)

    assert result == expected


def test_debug_falls_back_to_a_local_sqlite_file(load_settings):
    """Should run with no configuration at all in DEBUG."""
    settings = load_settings(
        DJANGO_DEBUG="True",
        DATABASE_URL=None,
        DJANGO_SECRET_KEY=None,
        DJANGO_ALLOWED_HOSTS=None,
    )

    result = {
        "engine": settings.DATABASES["default"]["ENGINE"],
        "hosts": settings.ALLOWED_HOSTS,
        "insecure_key": settings.SECRET_KEY.startswith("django-insecure"),
    }
    expected = {
        "engine": "django.db.backends.sqlite3",
        "hosts": ["*"],
        "insecure_key": True,
    }
    assert result == expected


def test_a_missing_database_url_outside_debug_is_fatal(load_settings):
    """Should refuse to start against an implicit database in production."""
    with pytest.raises(exceptions.ImproperlyConfigured) as error:
        load_settings(DJANGO_DEBUG="False", DATABASE_URL=None)

    result = str(error.value)
    expected = "DATABASE_URL must be set when DJANGO_DEBUG is off"
    assert result == expected


# django hardening


def test_a_missing_secret_key_outside_debug_is_fatal(load_settings):
    """Should refuse to start on the built-in development key."""
    with pytest.raises(exceptions.ImproperlyConfigured) as error:
        load_settings(DJANGO_DEBUG="False", DJANGO_SECRET_KEY=None)

    result = str(error.value)
    expected = "DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off"
    assert result == expected


def test_missing_allowed_hosts_outside_debug_is_fatal(load_settings):
    """Should refuse to start without an explicit host allowlist."""
    with pytest.raises(exceptions.ImproperlyConfigured) as error:
        load_settings(DJANGO_DEBUG="False", DJANGO_ALLOWED_HOSTS=None)

    result = str(error.value)
    expected = "DJANGO_ALLOWED_HOSTS must be set when DJANGO_DEBUG is off"
    assert result == expected


def test_debug_is_off_unless_asked_for(load_settings):
    """Should treat an unset DJANGO_DEBUG as production."""
    settings = load_settings(DJANGO_DEBUG=None)

    assert settings.DEBUG is False


def test_cookies_are_secure_outside_debug(load_settings):
    """Should mark session and CSRF cookies secure in production."""
    settings = load_settings(DJANGO_DEBUG="False")

    result = (settings.SESSION_COOKIE_SECURE, settings.CSRF_COOKIE_SECURE)
    expected = (True, True)

    assert result == expected


def test_cookies_are_plain_in_debug(load_settings):
    """Should not require TLS for cookies on a local http dev server."""
    settings = load_settings(DJANGO_DEBUG="True")

    result = (settings.SESSION_COOKIE_SECURE, settings.CSRF_COOKIE_SECURE)
    expected = (False, False)

    assert result == expected


def test_csrf_origins_default_to_the_allowed_hosts_over_https(load_settings):
    """Should derive trusted origins from the host allowlist."""
    settings = load_settings(
        DJANGO_ALLOWED_HOSTS="pandora.c.p-mk1.sopho.tech",
        DJANGO_DEBUG="False",
        DJANGO_CSRF_TRUSTED_ORIGINS=None,
    )

    result = settings.CSRF_TRUSTED_ORIGINS
    expected = ["https://pandora.c.p-mk1.sopho.tech"]

    assert result == expected


def test_csrf_origins_ignore_the_wildcard_host(load_settings):
    """Should not build an origin out of the wildcard host."""
    settings = load_settings(
        DJANGO_ALLOWED_HOSTS="*",
        DJANGO_DEBUG="False",
        DJANGO_CSRF_TRUSTED_ORIGINS=None,
    )

    result = settings.CSRF_TRUSTED_ORIGINS
    expected = []

    assert result == expected


def test_csrf_origins_can_be_set_explicitly(load_settings):
    """Should let an operator override the derived origin list."""
    settings = load_settings(
        DJANGO_ALLOWED_HOSTS="pandora.c.p-mk1.sopho.tech",
        DJANGO_CSRF_TRUSTED_ORIGINS="https://one.test, https://two.test",
    )

    result = settings.CSRF_TRUSTED_ORIGINS
    expected = ["https://one.test", "https://two.test"]

    assert result == expected


def test_the_proxy_header_is_only_trusted_when_declared(load_settings):
    """Should ignore X-Forwarded-Proto unless the operator opts in."""
    untrusted = load_settings(DJANGO_TRUST_PROXY_HEADER=None)
    assert hasattr(untrusted, "SECURE_PROXY_SSL_HEADER") is False

    trusted = load_settings(DJANGO_TRUST_PROXY_HEADER="1")
    result = trusted.SECURE_PROXY_SSL_HEADER
    expected = ("HTTP_X_FORWARDED_PROTO", "https")

    assert result == expected


# unfold environment banner


def test_the_environment_banner_is_absent_when_unset(load_settings, rf):
    """Should show no environment banner on an unlabelled deployment."""
    settings = load_settings(PANDORA_ENV=None)

    result = settings.UNFOLD["ENVIRONMENT"](rf.get("/"))

    assert result is None


def test_a_production_environment_banner_is_red(load_settings, rf):
    """Should colour the production banner danger."""
    settings = load_settings(PANDORA_ENV="prod")

    result = settings.UNFOLD["ENVIRONMENT"](rf.get("/"))
    expected = ("PROD", "danger")

    assert result == expected


def test_an_unknown_environment_banner_falls_back_to_info(load_settings, rf):
    """Should still label a cluster whose name is not in the colour map."""
    settings = load_settings(PANDORA_ENV="p-mk1")

    result = settings.UNFOLD["ENVIRONMENT"](rf.get("/"))
    expected = ("P-MK1", "info")

    assert result == expected
