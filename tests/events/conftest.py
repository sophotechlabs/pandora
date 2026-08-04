import datetime

import pytest
from django import db
from django.utils import timezone

from pandora.events import store
from tests.events import support

VENDORS = ("sqlite", "postgresql")


def connection_for(vendor):
    for alias in db.connections:
        candidate = db.connections[alias]
        if candidate.vendor == vendor:
            return candidate
    return None


def store_for(vendor):
    connection = connection_for(vendor)
    if connection is None:
        pytest.skip(f"no {vendor} connection in this run — set TEST_DATABASE_URL")
    return store.get_store(connection)


@pytest.fixture(params=VENDORS)
def event_store(request):
    return store_for(request.param)


@pytest.fixture
def sqlite_event_store():
    return store_for("sqlite")


@pytest.fixture
def postgres_event_store():
    return store_for("postgresql")


@pytest.fixture
def moment():
    now = timezone.now()
    floor = support.month_start(now) + datetime.timedelta(hours=6)
    return max(now - datetime.timedelta(hours=1), floor)


@pytest.fixture
def window(moment):
    return (
        moment - datetime.timedelta(hours=1),
        moment + datetime.timedelta(hours=1),
    )
