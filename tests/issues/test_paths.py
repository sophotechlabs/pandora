import pytest

from pandora.issues import paths
from pandora.issues.models import PathRule

pytestmark = pytest.mark.django_db


@pytest.fixture
def rule(db):
    def build(pattern, replacement, **overrides):
        fields = {
            "name": overrides.pop("name", "venv"),
            "pattern": pattern,
            "replacement": replacement,
        }
        fields.update(overrides)
        return PathRule.objects.create(**fields)

    return build


def canonical(path, project):
    return paths.canonical(path, paths.load_rules(project))


# what a rule does


def test_a_path_with_no_rules_is_left_alone(project):
    """Should change nothing on an install that configured nothing."""
    result = canonical("/app/src/payments/charge.py", project)
    expected = "/app/src/payments/charge.py"

    assert result == expected


def test_a_venv_prefix_is_collapsed(rule, project):
    """Should put the same library file at one address on every machine."""
    rule(r"^.*/(lib/python3\.\d+/site-packages/)", r"<venv>/\1")

    result = canonical(
        "/home/deploy/app/.venv/lib/python3.12/site-packages/requests/api.py", project
    )
    expected = "<venv>/lib/python3.12/site-packages/requests/api.py"

    assert result == expected


def test_a_backreference_keeps_the_part_that_matters(rule, project):
    """Should be a rewrite, not a deletion — the file still identifies the fault."""
    rule(r"^/(?:app|srv|home/\w+)/(.*)$", r"<root>/\1")

    result = canonical("/srv/checkout/gateway.py", project)
    expected = "<root>/checkout/gateway.py"

    assert result == expected


def test_rules_run_in_their_configured_order(rule, project):
    """Should let one rule feed the next, which is why they are ordered."""
    rule(r"^/opt/app/", "/app/", name="first", ordering=10)
    rule(r"^/app/", "<root>/", name="second", ordering=20)

    result = canonical("/opt/app/checkout.py", project)
    expected = "<root>/checkout.py"

    assert result == expected


def test_an_inactive_rule_does_nothing(rule, project):
    """Should let a rule be parked without deleting it."""
    rule(r"^/app/", "<root>/", active=False)

    result = canonical("/app/checkout.py", project)
    expected = "/app/checkout.py"

    assert result == expected


def test_a_rule_for_another_project_does_not_apply(rule, project):
    """Should keep one project's layout out of another's."""
    from pandora.core import models as core_models

    other = core_models.Project.objects.create(slug="apps", name="Applications")
    rule(r"^/app/", "<root>/", project=other)

    result = canonical("/app/checkout.py", project)
    expected = "/app/checkout.py"

    assert result == expected


def test_an_invalid_pattern_is_skipped_rather_than_raising(rule, project):
    """Should never let a typed regex take ingest down."""
    rule(r"^/app/(unclosed", "<root>/")

    result = canonical("/app/checkout.py", project)
    expected = "/app/checkout.py"

    assert result == expected


def test_a_rule_reads_as_its_substitution(rule):
    """Should be legible in a list without opening the row."""
    result = str(rule(r"^/app/", "<root>/"))
    expected = "venv: ^/app/ -> <root>/"

    assert result == expected


# through the door


def test_two_machines_group_into_one_issue(rule, project):
    """Should be the reason the table exists — a venv prefix is not the fault."""
    from tests.ingest.test_sdk_processor import deliver, event_payload

    rule(r"^.*/(site-packages/)", r"<venv>/\1")
    first = event_payload(
        event_id="1" * 32,
        exception={
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad",
                    "stacktrace": {
                        "frames": [
                            {"filename": "/home/a/.venv/site-packages/req/api.py"}
                        ]
                    },
                }
            ]
        },
    )
    second = event_payload(
        event_id="2" * 32,
        exception={
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad",
                    "stacktrace": {
                        "frames": [{"filename": "/opt/b/lib/site-packages/req/api.py"}]
                    },
                }
            ]
        },
    )
    deliver(project, first)
    deliver(project, second)

    from pandora.issues import models as issue_models

    result = issue_models.Issue.objects.count()
    expected = 1

    assert result == expected


def test_the_frame_keeps_the_path_the_key_rewrote(rule, project):
    """Should never lose the real path — it leaves the key, not the event."""
    from pandora.ingest import processor
    from tests.ingest import fakes
    from tests.ingest.test_sdk_processor import event_payload, store_event

    rule(r"^.*/(site-packages/)", r"<venv>/\1")
    payload = event_payload(
        exception={
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad",
                    "stacktrace": {
                        "frames": [
                            {"filename": "/home/a/.venv/site-packages/req/api.py"}
                        ]
                    },
                }
            ]
        }
    )
    store = fakes.RecordingEventStore()
    envelope = store_event(project, payload)
    processor.process_envelope(envelope.pk, store=store)

    frames = store.rows[0].payload["exceptions"][0]["frames"]

    assert frames[0]["filename"] == "/home/a/.venv/site-packages/req/api.py"
