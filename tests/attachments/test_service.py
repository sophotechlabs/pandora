import io
from pathlib import Path

import pytest
from django import test
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from pandora.attachments import models, service

pytestmark = pytest.mark.django_db


def test_a_failed_database_write_removes_its_blob(project, mocker, tmp_path):
    mocker.patch.object(models.EventAttachment, "save", side_effect=RuntimeError)

    with test.override_settings(MEDIA_ROOT=tmp_path), pytest.raises(RuntimeError):
        service.store(
            project,
            "a" * 32,
            filename="debug.txt",
            content_type="text/plain",
            attachment_type="event.attachment",
            size=4,
            sha256="b" * 64,
            body=io.BytesIO(b"data"),
            received_at=timezone.now(),
        )

    assert not any(path.is_file() for path in tmp_path.rglob("*"))


def test_a_rolled_back_delete_keeps_its_blob(project, tmp_path):
    with test.override_settings(MEDIA_ROOT=tmp_path):
        attachment = models.EventAttachment.objects.create(
            project=project,
            event_id="a" * 32,
            filename="debug.txt",
            size=4,
            sha256="b" * 64,
            blob=ContentFile(b"data", name="debug.txt"),
            received_at=timezone.now(),
        )
        attachment_id = attachment.pk
        path = attachment.blob.path

        with pytest.raises(RuntimeError), transaction.atomic():
            attachment.delete()
            raise RuntimeError

        assert models.EventAttachment.objects.filter(pk=attachment_id).exists()
        assert Path(path).exists()
