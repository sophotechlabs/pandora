import datetime

import freezegun
import pytest
from django.contrib.auth import models as auth_models
from django.test import RequestFactory
from django.utils import timezone

from pandora.people import audit
from pandora.people.models import AuditEntry

pytestmark = pytest.mark.django_db


def test_an_entry_keeps_who_did_what_to_which_thing():
    """Should answer "who changed this" — the whole point of the log."""
    entry = audit.record("dev", audit.TRIAGE, "17", {"to": "resolved"})

    result = (entry.actor, entry.action, entry.target, entry.data)
    expected = ("dev", "issue.triage", "17", {"to": "resolved"})

    assert result == expected


def test_a_long_target_is_cut_to_the_column():
    """Should record a huge target rather than raising on the write."""
    entry = audit.record("dev", audit.REDACT, "x" * 500)

    result = len(entry.target)
    expected = 200

    assert result == expected


def test_data_defaults_to_an_empty_object():
    """Should keep the column shape stable for a reader that expects a dict."""
    result = audit.record("dev", audit.SIGN_IN).data
    expected = {}

    assert result == expected


def test_the_newest_entry_is_first():
    """Should read like a log — the thing that just happened at the top."""
    with freezegun.freeze_time("2026-08-01 10:00:00"):
        audit.record("dev", audit.SIGN_IN)
    with freezegun.freeze_time("2026-08-01 11:00:00"):
        audit.record("dev", audit.TRIAGE)

    result = [row.action for row in AuditEntry.objects.all()]
    expected = ["issue.triage", "auth.sign-in"]

    assert result == expected


def test_a_request_records_the_signed_in_username():
    """Should not make every call site dig the username out of the request."""
    request = RequestFactory().post("/issues/1/triage/")
    request.user = auth_models.User.objects.create_user(username="dev", password="x")

    result = audit.from_request(request, audit.TRIAGE, "1").actor
    expected = "dev"

    assert result == expected


def test_a_request_from_nobody_records_an_empty_actor():
    """Should still record the action when the session has already gone."""
    request = RequestFactory().get("/")
    request.user = auth_models.AnonymousUser()

    result = audit.from_request(request, audit.SIGN_OUT).actor
    expected = ""

    assert result == expected


def test_pruning_drops_only_what_is_older_than_the_cutoff():
    """Should let the log be kept for a window without growing forever."""
    with freezegun.freeze_time("2026-06-01 10:00:00"):
        audit.record("dev", audit.SIGN_IN)
    with freezegun.freeze_time("2026-08-01 10:00:00"):
        kept = audit.record("dev", audit.TRIAGE)

    dropped = audit.prune(timezone.now() - datetime.timedelta(days=30))

    result = (dropped, list(AuditEntry.objects.values_list("pk", flat=True)))
    expected = (1, [kept.pk])

    assert result == expected


def test_an_entry_reads_as_a_sentence():
    """Should be legible in the admin without opening the row."""
    result = str(audit.record("dev", audit.SNOOZE, "17"))
    expected = "dev issue.snooze 17"

    assert result == expected


def test_an_entry_with_no_actor_names_pandora_itself():
    """Should distinguish a background job from an anonymous person."""
    result = str(audit.record("", audit.CONFIG, "pandora.yml"))
    expected = "pandora config.apply pandora.yml"

    assert result == expected


def test_a_team_reads_as_its_name(make_team):
    """Should be pickable from a list in the admin."""
    result = str(make_team("platform"))
    expected = "platform"

    assert result == expected


def test_a_membership_reads_as_who_is_where(make_user, make_team, join):
    """Should answer the admin's only question about a membership row."""
    from pandora.people.models import Role

    result = str(join(make_user("dev"), make_team("platform"), Role.OWNER))
    expected = "dev in platform (owner)"

    assert result == expected


def test_a_sign_out_with_no_session_records_nothing():
    """Should not write a row with nobody's name on it when the session had expired."""
    from pandora.people import signals

    signals.on_logged_out(None, request=None, user=None)

    assert AuditEntry.objects.count() == 0
