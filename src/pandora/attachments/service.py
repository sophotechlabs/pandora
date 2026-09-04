from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from typing import IO, Any

from django.core.files import File
from django.db import IntegrityError, transaction

from pandora.attachments.models import EventAttachment
from pandora.core.models import Project


def store(
    project: Project,
    event_id: str,
    *,
    filename: str,
    content_type: str,
    attachment_type: str,
    size: int,
    sha256: str,
    body: IO[bytes],
    received_at: datetime,
) -> tuple[EventAttachment, bool]:
    existing = EventAttachment.objects.filter(
        project=project,
        event_id=event_id[:64],
        filename=filename[:255],
        sha256=sha256,
    ).first()
    if existing is not None:
        return existing, False
    body.seek(0)
    attachment = EventAttachment(
        project=project,
        event_id=event_id[:64],
        filename=filename[:255],
        content_type=content_type[:255],
        attachment_type=attachment_type[:64],
        size=size,
        sha256=sha256,
        received_at=received_at,
    )
    attachment.blob.save(sha256, File(body), save=False)
    try:
        with transaction.atomic():
            attachment.save()
    except IntegrityError:
        attachment.blob.delete(save=False)
        return (
            EventAttachment.objects.get(
                project=project,
                event_id=event_id[:64],
                filename=filename[:255],
                sha256=sha256,
            ),
            False,
        )
    except Exception:
        attachment.blob.delete(save=False)
        raise
    return attachment, True


def sentry_id(event: Any) -> str:
    extra = event.extra
    if not isinstance(extra, dict):
        return ""
    return str(extra.get("event_id", ""))[:64]


def for_events(
    project_id: int,
    events: Iterable[Any],
) -> dict[str, tuple[EventAttachment, ...]]:
    event_ids = {sentry_id(event) for event in events}
    event_ids.discard("")
    grouped: dict[str, list[EventAttachment]] = defaultdict(list)
    for attachment in EventAttachment.objects.filter(
        project_id=project_id,
        event_id__in=event_ids,
    ):
        grouped[attachment.event_id].append(attachment)
    return {event_id: tuple(rows) for event_id, rows in grouped.items()}


def delete_for_events(project_id: int, events: Iterable[Any]) -> int:
    event_ids = {sentry_id(event) for event in events}
    event_ids.discard("")
    if not event_ids:
        return 0
    _, details = EventAttachment.objects.filter(
        project_id=project_id,
        event_id__in=event_ids,
    ).delete()
    return details.get(EventAttachment._meta.label, 0)


def prune(before: datetime) -> int:
    _, details = EventAttachment.objects.filter(received_at__lt=before).delete()
    return details.get(EventAttachment._meta.label, 0)
