import http

import pytest
from django.contrib.auth import models as auth_models
from django.core.files.base import ContentFile
from django.utils import timezone

from pandora.attachments import models as attachment_models
from pandora.events import types
from pandora.ingest import models as ingest_models
from tests.web import fakes

pytestmark = pytest.mark.django_db


def event_for(issue):
    return types.Event(
        id="01J8ZQ7X4N0000000000000001",
        project_id=issue.project_id,
        issue_id=issue.pk,
        timestamp=timezone.now(),
        level="error",
        message="boom",
        extra={"event_id": "a" * 32},
        source="sdk",
    )


def attachment_for(issue):
    return attachment_models.EventAttachment.objects.create(
        project=issue.project,
        event_id="a" * 32,
        filename="debug.txt",
        content_type="text/plain",
        size=4,
        sha256="b" * 64,
        blob=ContentFile(b"data", name="debug.txt"),
    )


def install_store(mocker, event):
    mocker.patch(
        "pandora.ui.views.get_store",
        return_value=fakes.FakeEventStore([event]),
    )


def test_attachment_metadata_is_visible_to_an_operator(
    operator_client, make_issue, mocker
):
    issue = make_issue()
    install_store(mocker, event_for(issue))
    attachment = attachment_for(issue)

    page = operator_client.get(f"/issues/{issue.pk}/occurrences/").content.decode()

    assert "Attachments" in page
    assert "debug.txt" in page
    assert f"/issues/{issue.pk}/attachments/{attachment.pk}/download/" not in page


def test_a_non_owner_cannot_download_an_attachment(operator_client, make_issue, mocker):
    issue = make_issue()
    event = event_for(issue)
    install_store(mocker, event)
    attachment = attachment_for(issue)
    ingest_models.ProcessedEvent.objects.create(
        project=issue.project,
        event_id="a" * 32,
        issue=issue,
    )

    response = operator_client.get(
        f"/issues/{issue.pk}/attachments/{attachment.pk}/download/"
    )

    assert response.status_code == http.HTTPStatus.FORBIDDEN


def test_a_superuser_can_download_an_attachment(client, make_issue, mocker):
    issue = make_issue()
    event = event_for(issue)
    install_store(mocker, event)
    attachment = attachment_for(issue)
    ingest_models.ProcessedEvent.objects.create(
        project=issue.project,
        event_id="a" * 32,
        issue=issue,
    )
    owner = auth_models.User.objects.create_superuser(
        username="owner",
        email="owner@example.test",
        password="owner-pass",
    )
    client.force_login(owner)

    response = client.get(f"/issues/{issue.pk}/attachments/{attachment.pk}/download/")

    assert response.status_code == http.HTTPStatus.OK
    assert b"".join(response.streaming_content) == b"data"
