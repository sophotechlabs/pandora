import io

import pytest
from django.contrib import auth
from django.core import management

pytestmark = pytest.mark.django_db


def run_ensure_superuser():
    out = io.StringIO()
    management.call_command("ensure_superuser", stdout=out)
    return out.getvalue()


@pytest.fixture
def superuser_env(monkeypatch):
    monkeypatch.setenv("DJANGO_SUPERUSER_USERNAME", "admin")
    monkeypatch.setenv("DJANGO_SUPERUSER_PASSWORD", "admin-password")
    monkeypatch.setenv("DJANGO_SUPERUSER_EMAIL", "admin@example.test")


# ensure_superuser tests


def test_ensure_superuser_skips_without_env(monkeypatch):
    """Should do nothing when the DJANGO_SUPERUSER_* env is absent."""
    monkeypatch.delenv("DJANGO_SUPERUSER_USERNAME", raising=False)
    monkeypatch.delenv("DJANGO_SUPERUSER_PASSWORD", raising=False)

    result = run_ensure_superuser()

    assert "ensure_superuser: env not set, skipping" in result
    assert auth.get_user_model().objects.exists() is False


def test_ensure_superuser_creates_the_account(superuser_env):
    """Should create the superuser described by the env."""
    result = run_ensure_superuser()

    user = auth.get_user_model().objects.get(username="admin")
    state = {
        "email": user.email,
        "is_staff": user.is_staff,
        "is_superuser": user.is_superuser,
    }
    expected = {
        "email": "admin@example.test",
        "is_staff": True,
        "is_superuser": True,
    }
    assert state == expected
    assert "ensure_superuser: created admin" in result


def test_ensure_superuser_is_idempotent(superuser_env):
    """Should report no change on a second identical run."""
    run_ensure_superuser()

    result = run_ensure_superuser()

    assert "ensure_superuser: admin up to date" in result


def test_ensure_superuser_repairs_a_downgraded_account(superuser_env):
    """Should restore staff, superuser, active, email and password in one pass."""
    user = auth.get_user_model().objects.create_user(
        username="admin",
        email="stale@example.test",
        password="old-password",
    )
    user.is_active = False
    user.save()

    result = run_ensure_superuser()

    user.refresh_from_db()
    state = {
        "email": user.email,
        "is_staff": user.is_staff,
        "is_superuser": user.is_superuser,
        "is_active": user.is_active,
    }
    expected = {
        "email": "admin@example.test",
        "is_staff": True,
        "is_superuser": True,
        "is_active": True,
    }
    assert state == expected
    assert "ensure_superuser: updated admin" in result


# the password is not rewritten on every container start


def test_an_existing_password_survives_a_restart(superuser_env):
    """Should not silently reset a password the operator changed in the admin."""
    user = auth.get_user_model().objects.create_superuser(
        username="admin",
        email="admin@example.test",
        password="chosen-in-the-admin",
    )

    run_ensure_superuser()

    user.refresh_from_db()
    assert user.check_password("chosen-in-the-admin") is True


def test_the_password_is_reset_only_when_asked(superuser_env, monkeypatch):
    """Should still allow a deliberate reset from the environment."""
    monkeypatch.setenv("DJANGO_SUPERUSER_RESET_PASSWORD", "1")
    user = auth.get_user_model().objects.create_superuser(
        username="admin",
        email="admin@example.test",
        password="chosen-in-the-admin",
    )

    run_ensure_superuser()

    user.refresh_from_db()
    assert user.check_password("admin-password") is True
