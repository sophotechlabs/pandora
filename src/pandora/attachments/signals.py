from __future__ import annotations

from django.db import transaction
from django.db.models.signals import post_delete

from pandora.attachments.models import EventAttachment


def delete_blob(
    sender: type[EventAttachment],
    instance: EventAttachment,
    using: str,
    **kwargs: object,
) -> None:
    if instance.blob:
        storage = instance.blob.storage
        name = instance.blob.name
        if name is None:
            return
        transaction.on_commit(lambda: storage.delete(name), using=using)


def connect() -> None:
    post_delete.connect(
        delete_blob,
        sender=EventAttachment,
        dispatch_uid="pandora.attachments.delete_blob",
    )
