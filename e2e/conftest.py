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

from pandora.artifacts import models as artifact_models
from pandora.core import models as core_models
from pandora.ingest import models as ingest_models
from pandora.issues import models as issue_models
from pandora.people import models as people_models
from pandora.releases import models as release_models

PASSWORD = "e2e-operator-pass"
OWNED = (
    artifact_models.UploadChunk,
    artifact_models.BundleFile,
    artifact_models.ArtifactBundle,
    ingest_models.ClientDiscard,
    ingest_models.Monitor,
    issue_models.UserReport,
    issue_models.SavedView,
    issue_models.IssueAlias,
    issue_models.IssueEnvironment,
    release_models.Resolution,
    release_models.Deploy,
    release_models.SessionBucket,
    release_models.ReleaseEnvironment,
    release_models.Release,
    people_models.Assignment,
    people_models.OwnershipRule,
    people_models.Membership,
    people_models.Team,
    people_models.AuditEntry,
    issue_models.IssueActivity,
    issue_models.HourlyStat,
    issue_models.Episode,
    issue_models.Issue,
    ingest_models.ProcessedEvent,
    ingest_models.RawEnvelope,
    core_models.DsnKey,
    core_models.IngestToken,
    core_models.Project,
)


@pytest.fixture(scope="session")
def base_url():
    return os.environ.get("PANDORA_E2E_URL", "http://web:8000").rstrip("/")


@pytest.fixture(autouse=True)
def clean():
    _wipe()
    yield
    _wipe()


def _wipe():
    for model in OWNED:
        model.objects.all().delete()
    get_user_model().objects.filter(username__startswith="e2e-").delete()


@pytest.fixture
def project():
    return core_models.Project.objects.create(slug="e2e", name="End to end")


@pytest.fixture
def dsn_key(project):
    return core_models.DsnKey.objects.create(
        project=project,
        public_key="e2epublickey000000000000000000ff",
    )


@pytest.fixture
def make_user():
    def build(name, **overrides):
        fields = {"username": f"e2e-{name}", "is_staff": True}
        fields.update(overrides)
        user = get_user_model()(**fields)
        user.set_password(PASSWORD)
        user.save()
        return user

    return build


@pytest.fixture
def sign_in(page, base_url):
    def go(user):
        page.goto(f"{base_url}/login/")
        page.fill("input[name=username]", user.get_username())
        page.fill("input[name=password]", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url(f"{base_url}/**")

    return go
