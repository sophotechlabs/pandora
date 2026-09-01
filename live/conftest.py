import os
import pathlib
import sys

import django
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pandora.web.settings")
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")
django.setup()

from django.contrib.auth import get_user_model

PASSWORD = "live-operator-pass"


@pytest.fixture(scope="session")
def base_url():
    return os.environ.get("PANDORA_LIVE_URL", "http://web:8000").rstrip("/")


@pytest.fixture(scope="session")
def read_token():
    return "live-read-token"


@pytest.fixture(scope="session")
def operator():
    user, _ = get_user_model().objects.get_or_create(
        username="live-operator",
        defaults={"is_staff": True, "is_superuser": True},
    )
    user.is_staff = True
    user.is_superuser = True
    user.set_password(PASSWORD)
    user.save()
    return user


@pytest.fixture
def signed_in(page, base_url, operator):
    page.goto(f"{base_url}/login/")
    page.fill("input[name=username]", operator.get_username())
    page.fill("input[name=password]", PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_url(f"{base_url}/**")
    return page
