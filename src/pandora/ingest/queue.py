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


class AsyncQueue:
    """Leave the envelope pending and let `manage.py consume` pick it up.

    The default stays synchronous — one container with no worker is the position
    for a single operator's cluster, and it is correct there. This is the option
    you turn on when the second door opens and an SDK's timeout should stop
    being coupled to the database.
    """

    def publish(self, envelope_id: int) -> None:
        return None


def get_queue() -> Queue:
    queue_class = import_string(settings.PANDORA_QUEUE)
    return queue_class()
