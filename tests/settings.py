import os

os.environ.setdefault("DJANGO_DEBUG", "False")
os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key-not-used-in-production")
os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "testserver,localhost")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pandora.web.settings import *  # noqa: F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
_TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
if _TEST_DATABASE_URL:
    import dj_database_url

    DATABASES = {"default": dj_database_url.parse(_TEST_DATABASE_URL)}

DEBUG = False
SECRET_KEY = "test-secret-key-not-used-in-production"
ALLOWED_HOSTS = ["testserver", "localhost"]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
