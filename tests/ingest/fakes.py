from typing import ClassVar

from pandora.ingest import processor

INLINE_ROWS = []


class RecordingEventStore:
    def __init__(self, rows=None):
        if rows is None:
            rows = []
        self.rows = rows

    def insert(self, events):
        self.rows.extend(events)

    def fetch(
        self, project_id, *, issue_id=None, episode_id=None, before=None, limit=100
    ):
        return list(self.rows)

    def search(self, project_id, tags, since, until, limit=100):
        return list(self.rows)

    def prune(self, before):
        return 0

    def ensure_partitions(self, months_ahead=2):
        return None


class FailingEventStore(RecordingEventStore):
    def insert(self, events):
        raise RuntimeError("event store is unreachable")


class FlakyEventStore(RecordingEventStore):
    def __init__(self, rows=None, failures=1):
        super().__init__(rows)
        self.failures = failures

    def insert(self, events):
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("event store is unreachable")
        super().insert(events)


class InlineQueue:
    def publish(self, envelope_id):
        processor.process_envelope(envelope_id, store=RecordingEventStore(INLINE_ROWS))


class RecordingQueue:
    published: ClassVar[list[int]] = []

    def publish(self, envelope_id):
        RecordingQueue.published.append(envelope_id)
