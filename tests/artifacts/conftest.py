import pytest

from pandora.artifacts import service
from pandora.core import models as core_models
from tests import bundles
from tests.bundles import DEBUG_ID, MAP

__all__ = ["DEBUG_ID", "MAP"]


@pytest.fixture(autouse=True)
def empty_cache():
    service.clear_cache()
    yield
    service.clear_cache()


@pytest.fixture
def token(project):
    return core_models.IngestToken.objects.create(
        project=project,
        name="ci",
        token="upload-token",
        source=core_models.TokenSource.SDK,
        scope=core_models.TokenScope.INGEST,
    )


@pytest.fixture
def bundle_bytes():
    return bundles.build
