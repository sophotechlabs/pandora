from __future__ import annotations

import os
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


class Command(BaseCommand):
    help = "Create or update the superuser described by the DJANGO_SUPERUSER_* env vars"

    def handle(self, *args: Any, **options: Any) -> None:
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "").strip()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "")
        if not username or not password:
            self.stdout.write("ensure_superuser: env not set, skipping")
            return

        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "").strip()
        manager = get_user_model()._default_manager
        user = manager.filter(username=username).first()
        if user is None:
            manager.create_superuser(
                username=username,
                email=email,
                password=password,
            )
            self.stdout.write(f"ensure_superuser: created {username}")
            return

        changed = []
        if _flag("DJANGO_SUPERUSER_RESET_PASSWORD") and not user.check_password(
            password
        ):
            user.set_password(password)
            changed.append("password")
        if user.email != email:
            user.email = email
            changed.append("email")
        if not user.is_staff:
            user.is_staff = True
            changed.append("is_staff")
        if not user.is_superuser:
            user.is_superuser = True
            changed.append("is_superuser")
        if not user.is_active:
            user.is_active = True
            changed.append("is_active")
        if not changed:
            self.stdout.write(f"ensure_superuser: {username} up to date")
            return

        user.save()
        self.stdout.write(
            f"ensure_superuser: updated {username} ({', '.join(changed)})"
        )
