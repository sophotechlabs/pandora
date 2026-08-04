from __future__ import annotations

from typing import Protocol

from django.conf import settings
from django.utils.module_loading import import_string

from pandora.ingest.processor import process_envelope


class Queue(Protocol):
    def publish(self, envelope_id: int) -> None: ...


class SyncQueue:
    def publish(self, envelope_id: int) -> None:
        process_envelope(envelope_id)


def get_queue() -> Queue:
    queue_class = import_string(settings.PANDORA_QUEUE)
    return queue_class()
