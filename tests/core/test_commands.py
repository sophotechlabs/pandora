import datetime
import io
import re

import pytest
from django import db
from django.contrib import auth
from django.core import management

from pandora.core.management.commands import backup

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


# backup


def run_backup(target):
    out = io.StringIO()
    management.call_command("backup", "--to", str(target), stdout=out)
    return out.getvalue()


@pytest.fixture
def on_sqlite(monkeypatch):
    for alias in db.connections:
        candidate = db.connections[alias]
        if candidate.vendor == "sqlite":
            monkeypatch.setattr(backup, "default_connection", candidate)
            return candidate
    pytest.skip("no sqlite connection in this run")


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_backup_writes_a_snapshot(on_sqlite, tmp_path):
    """Should leave a file the operator can hand to the uploader."""
    target = tmp_path / "pandora.sqlite3"

    result = run_backup(target)

    assert target.is_file()
    assert target.stat().st_size > 0
    assert f"wrote {target.stat().st_size} bytes" in result


@pytest.mark.django_db(databases="__all__")
def test_backup_refuses_to_overwrite(on_sqlite, tmp_path):
    """Should never destroy an existing snapshot — VACUUM INTO cannot merge."""
    target = tmp_path / "pandora.sqlite3"
    target.write_bytes(b"")

    with pytest.raises(management.CommandError, match="already exists"):
        run_backup(target)


@pytest.mark.django_db(databases="__all__")
def test_backup_refuses_a_missing_directory(on_sqlite, tmp_path):
    """Should name the missing directory rather than fail inside SQLite."""
    target = tmp_path / "absent" / "pandora.sqlite3"

    with pytest.raises(management.CommandError, match="is not a directory"):
        run_backup(target)


def test_backup_refuses_another_vendor(tmp_path, monkeypatch):
    """Should say plainly that another backend has its own backup path."""

    class Elsewhere:
        vendor = "postgresql"

    monkeypatch.setattr(backup, "default_connection", Elsewhere())
    target = tmp_path / "pandora.sqlite3"

    with pytest.raises(management.CommandError, match="runs on SQLite only"):
        run_backup(target)


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_backup_names_the_snapshot_when_given_a_directory(on_sqlite, tmp_path):
    """Should stamp the filename itself so no shell has to build it."""
    result = run_backup(tmp_path)

    written = list(tmp_path.iterdir())
    assert len(written) == 1
    assert re.fullmatch(
        r"pandora-\d{8}T\d{6}Z\.sqlite3",
        written[0].name,
    )
    assert str(written[0]) in result


def test_the_snapshot_name_is_utc():
    """Should name every snapshot in UTC whatever the container clock is."""
    moment = datetime.datetime(2026, 8, 20, 14, 5, 9, tzinfo=datetime.UTC)

    result = backup.snapshot_name(moment)
    expected = "pandora-20260820T140509Z.sqlite3"

    assert result == expected
