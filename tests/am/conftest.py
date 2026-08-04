import pytest

from pandora.am import client as am_client
from tests.am import fake_am


@pytest.fixture
def alertmanager():
    server = fake_am.FakeAlertmanager().start()
    yield server
    server.stop()


@pytest.fixture
def client_factory(alertmanager):
    def build(**overrides):
        kwargs = {"backoff_factor": 0.0}
        kwargs.update(overrides)
        return am_client.AlertmanagerClient(alertmanager.url, **kwargs)

    return build


@pytest.fixture
def alertmanager_client(client_factory):
    return client_factory()
