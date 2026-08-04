class FakeEventStore:
    def __init__(self, events=()):
        self.events = list(events)
        self.calls = []

    def insert(self, events):
        self.events.extend(events)

    def fetch(
        self, project_id, *, issue_id=None, episode_id=None, before=None, limit=100
    ):
        self.calls.append(
            {
                "project_id": project_id,
                "issue_id": issue_id,
                "episode_id": episode_id,
                "before": before,
                "limit": limit,
            }
        )
        found = [event for event in self.events if event.project_id == project_id]
        if issue_id is not None:
            found = [event for event in found if event.issue_id == issue_id]
        if episode_id is not None:
            found = [event for event in found if event.episode_id == episode_id]
        if before is not None:
            found = [event for event in found if event.id < before]
        found.sort(key=lambda event: event.id, reverse=True)
        return found[:limit]

    def search(self, project_id, tags, since, until, limit=100):
        return []

    def prune(self, before):
        return 0

    def ensure_partitions(self, months_ahead=2):
        return None


class UnbuiltEventStore:
    def __init__(self):
        self.calls = []

    def insert(self, events):
        raise NotImplementedError

    def fetch(
        self, project_id, *, issue_id=None, episode_id=None, before=None, limit=100
    ):
        self.calls.append({"project_id": project_id, "issue_id": issue_id})
        raise NotImplementedError

    def search(self, project_id, tags, since, until, limit=100):
        raise NotImplementedError

    def prune(self, before):
        raise NotImplementedError

    def ensure_partitions(self, months_ahead=2):
        raise NotImplementedError
