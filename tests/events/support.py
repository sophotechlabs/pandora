import datetime

from pandora.events import types

BASE_TAGS = {
    "alertname": "KubePodCrashLooping",
    "namespace": "payments",
    "severity": "critical",
}
BASE_FINGERPRINT = ["alertname:KubePodCrashLooping", "namespace:payments"]
BASE_EXTRA = {
    "summary": "Pod payments/ledger is in CrashLoopBackOff",
    "generatorURL": "https://prometheus.example.test/graph?g0.expr=up",
}


def event_id(index):
    return f"01J{index:023d}"


def make_event(index, moment, **overrides):
    fields = {
        "id": event_id(index),
        "project_id": 1,
        "timestamp": moment + datetime.timedelta(minutes=index),
        "level": "error",
        "message": "Pod is crash looping.",
        "issue_id": 10,
        "episode_id": "100",
        "fingerprint": list(BASE_FINGERPRINT),
        "tags": dict(BASE_TAGS),
        "extra": dict(BASE_EXTRA),
        "source": "am",
        "environment": "p-mk1",
    }
    fields.update(overrides)
    return types.Event(**fields)


def make_events(count, moment, **overrides):
    return [make_event(index, moment, **overrides) for index in range(count)]


def month_start(moment):
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def inside_previous_month(moment):
    return month_start(moment) - datetime.timedelta(hours=12)


def ids(events):
    return [event.id for event in events]
