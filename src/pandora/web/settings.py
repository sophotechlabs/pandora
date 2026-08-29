from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
WEB_DIR = Path(__file__).resolve().parent


def _flag(name: str, default: str = "") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ImproperlyConfigured(f"{name} must be set when DJANGO_DEBUG is off")
    return value


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return int(raw)


DEBUG = _flag("DJANGO_DEBUG")

if DEBUG:
    SECRET_KEY = os.environ.get(
        "DJANGO_SECRET_KEY",
        "django-insecure-dev-key-change-me-in-production",
    )
    _ALLOWED_HOSTS_RAW = os.environ.get("DJANGO_ALLOWED_HOSTS", "*")
else:
    SECRET_KEY = _required("DJANGO_SECRET_KEY")
    _ALLOWED_HOSTS_RAW = _required("DJANGO_ALLOWED_HOSTS")

ALLOWED_HOSTS = [h.strip() for h in _ALLOWED_HOSTS_RAW.split(",") if h.strip()]

if _flag("DJANGO_TRUST_PROXY_HEADER"):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

_CSRF_TRUSTED_ORIGINS_ENV = os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "")
if _CSRF_TRUSTED_ORIGINS_ENV:
    CSRF_TRUSTED_ORIGINS = [
        o.strip() for o in _CSRF_TRUSTED_ORIGINS_ENV.split(",") if o.strip()
    ]
else:
    CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if h != "*"]

if DEBUG:
    _SECURE_COOKIES = _flag("DJANGO_SECURE_COOKIES", "0")
else:
    _SECURE_COOKIES = _flag("DJANGO_SECURE_COOKIES", "1")

SESSION_COOKIE_SECURE = _SECURE_COOKIES
CSRF_COOKIE_SECURE = _SECURE_COOKIES

INSTALLED_APPS = [
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_prometheus",
    "pandora.core",
    "pandora.ingest",
    "pandora.issues",
    "pandora.events",
    "pandora.am",
    "pandora.ui",
]

if importlib.util.find_spec("django_migration_linter"):
    INSTALLED_APPS.append("django_migration_linter")

MIDDLEWARE = [
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_prometheus.middleware.PrometheusAfterMiddleware",
]

ROOT_URLCONF = "pandora.web.urls"
WSGI_APPLICATION = "pandora.web.wsgi.application"
ASGI_APPLICATION = "pandora.web.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [WEB_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "pandora.ui.context.chrome",
            ],
        },
    },
]

SQLITE_OPTIONS = {
    "init_command": (
        "PRAGMA auto_vacuum=INCREMENTAL; "
        "PRAGMA journal_mode=WAL; "
        "PRAGMA synchronous=NORMAL;"
    ),
    "transaction_mode": "IMMEDIATE",
    "timeout": 20,
}

_DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if _DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            _DATABASE_URL,
            conn_max_age=600,
            conn_health_checks=True,
        )
    }
elif DEBUG:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(BASE_DIR / "pandora.sqlite3"),
        }
    }
else:
    raise ImproperlyConfigured("DATABASE_URL must be set when DJANGO_DEBUG is off")

if DATABASES["default"]["ENGINE"].endswith("sqlite3"):
    DATABASES["default"].setdefault("OPTIONS", {})
    DATABASES["default"]["OPTIONS"].update(SQLITE_OPTIONS)

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [WEB_DIR / "static"]

if DEBUG:
    _STATICFILES_BACKEND = "django.contrib.staticfiles.storage.StaticFilesStorage"
else:
    _STATICFILES_BACKEND = "whitenoise.storage.CompressedManifestStaticFilesStorage"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": _STATICFILES_BACKEND},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"

PANDORA_ENV = os.environ.get("PANDORA_ENV", "")
PANDORA_BASE_URL = os.environ.get("PANDORA_BASE_URL", "")
PANDORA_RETENTION_DAYS = _int("PANDORA_RETENTION_DAYS", 30)
PANDORA_ENVELOPE_RETENTION_DAYS = _int("PANDORA_ENVELOPE_RETENTION_DAYS", 7)
PANDORA_INGEST_MAX_BYTES = _int("PANDORA_INGEST_MAX_BYTES", 1024 * 1024)
PANDORA_RECONCILE_IGNORE = os.environ.get(
    "PANDORA_RECONCILE_IGNORE",
    "Watchdog,InfoInhibitor",
)
PANDORA_AM_URL = os.environ.get("PANDORA_AM_URL", "")
PANDORA_AM_CA_BUNDLE = os.environ.get("PANDORA_AM_CA_BUNDLE", "")
PANDORA_GRAFANA_URL = os.environ.get("PANDORA_GRAFANA_URL", "")
PANDORA_LOKI_QUERY_URL = os.environ.get("PANDORA_LOKI_QUERY_URL", "")
PANDORA_CORRELATION_KEYS = os.environ.get(
    "PANDORA_CORRELATION_KEYS",
    "namespace,pod,node,cluster,service",
)
PANDORA_CORRELATION_WINDOW_MINUTES = _int(
    "PANDORA_CORRELATION_WINDOW_MINUTES",
    60,
)
PANDORA_GATE = os.environ.get("PANDORA_GATE", "pandora.ingest.gate.PassThroughGate")
PANDORA_QUEUE = os.environ.get("PANDORA_QUEUE", "pandora.ingest.queue.SyncQueue")

_ENVIRONMENT_COLORS = {
    "local": "info",
    "dev": "info",
    "development": "info",
    "staging": "warning",
    "stage": "warning",
    "prod": "danger",
    "production": "danger",
}


def _unfold_environment(request):
    env = os.environ.get("PANDORA_ENV", "")
    if not env:
        return None
    return (env.upper(), _ENVIRONMENT_COLORS.get(env.lower(), "info"))


UNFOLD = {
    "SITE_TITLE": "Pandora",
    "SITE_HEADER": "Pandora",
    "SITE_URL": "/",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "ENVIRONMENT": _unfold_environment,
    "DASHBOARD_CALLBACK": "pandora.web.dashboard.dashboard_callback",
    "SIDEBAR": {
        "show_search": False,
        "show_all_applications": True,
    },
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {"()": "pandora.core.log.JsonFormatter"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "json"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "pandora": {
            "handlers": ["console"],
            "level": os.environ.get("PANDORA_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}
