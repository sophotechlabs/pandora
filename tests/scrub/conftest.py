import pytest

from pandora.core import models as core_models


@pytest.fixture
def dsn_key(project):
    return core_models.DsnKey.objects.create(project=project, public_key="d" * 32)
