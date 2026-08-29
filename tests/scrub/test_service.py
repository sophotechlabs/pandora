import pytest

from pandora.core import models as core_models
from pandora.scrub import service
from pandora.scrub.models import DropRule, RuleAction, ScrubRule
from pandora.scrub.rules import REDACTED

pytestmark = pytest.mark.django_db


# the default pass


def test_scrubbing_is_on_without_configuration(project):
    """Should protect an install nobody configured — a default that leaks is not a default."""
    result = service.scrub_payload({"user": {"password": "hunter2"}}, project)
    expected = {"user": {"password": REDACTED}}

    assert result == expected


def test_scrubbing_can_be_turned_off(project, settings):
    """Should let an operator who needs the raw payload keep it, deliberately."""
    settings.PANDORA_SCRUB_ENABLED = False

    result = service.scrub_payload({"user": {"password": "hunter2"}}, project)
    expected = {"user": {"password": "hunter2"}}

    assert result == expected


def test_extra_keywords_come_from_the_environment(project, settings):
    """Should let an install name a field only it knows about."""
    settings.PANDORA_SCRUB_KEYWORDS = "pin, ssn"

    result = service.scrub_payload({"ssn": "1234"}, project)
    expected = {"ssn": REDACTED}

    assert result == expected


def test_a_safe_key_is_exempted(project, settings):
    """Should stop a false positive from hiding a field the operator needs."""
    settings.PANDORA_SCRUB_SAFE_KEYS = "auth_provider"

    result = service.scrub_payload({"auth_provider": "keycloak"}, project)
    expected = {"auth_provider": "keycloak"}

    assert result == expected


def test_ip_anonymisation_can_be_turned_off(project, settings):
    """Should let an operator who needs the exact client address keep it."""
    settings.PANDORA_SCRUB_ANONYMISE_IP = False

    result = service.scrub_payload({"user": {"ip_address": "203.0.113.44"}}, project)
    expected = {"user": {"ip_address": "203.0.113.44"}}

    assert result == expected


def test_a_message_is_masked_but_not_emptied():
    """Should keep an exception message readable while hiding a card inside it."""
    result = service.scrub_message("declined 4111111111111111 from 203.0.113.44")
    expected = f"declined {REDACTED} from 203.0.113.0"

    assert result == expected


def test_a_message_is_untouched_when_scrubbing_is_off(settings):
    """Should mean off when it says off."""
    settings.PANDORA_SCRUB_ENABLED = False

    result = service.scrub_message("card 4111111111111111")
    expected = "card 4111111111111111"

    assert result == expected


# configured rules


def test_a_rule_redacts_the_path_it_names(project):
    """Should let an operator remove a field the keyword list cannot know about."""
    ScrubRule.objects.create(name="email", path="user.email")

    result = service.scrub_payload({"user": {"email": "a@b.test", "id": "7"}}, project)
    expected = {"user": {"email": REDACTED, "id": "7"}}

    assert result == expected


def test_a_masking_rule_keeps_the_field(project):
    """Should let a field stay present when its shape matters and its content does not."""
    ScrubRule.objects.create(name="note", path="extra.note", action=RuleAction.MASK)

    result = service.scrub_payload({"extra": {"note": "4111111111111111"}}, project)
    expected = {"extra": {"note": REDACTED}}

    assert result == expected


def test_an_inactive_rule_does_nothing(project):
    """Should let a rule be turned off without deleting it."""
    ScrubRule.objects.create(name="email", path="user.email", active=False)

    result = service.scrub_payload({"user": {"email": "a@b.test"}}, project)
    expected = {"user": {"email": "a@b.test"}}

    assert result == expected


def test_a_rule_scoped_to_another_project_does_not_apply(project):
    """Should keep one project's policy off another's payloads."""
    other = core_models.Project.objects.create(slug="apps", name="Applications")
    ScrubRule.objects.create(name="email", path="user.email", project=other)

    result = service.scrub_payload({"user": {"email": "a@b.test"}}, project)
    expected = {"user": {"email": "a@b.test"}}

    assert result == expected


def test_a_rule_scoped_to_this_project_applies(project):
    """Should let a project carry a policy the others do not."""
    ScrubRule.objects.create(name="email", path="user.email", project=project)

    result = service.scrub_payload({"user": {"email": "a@b.test"}}, project)
    expected = {"user": {"email": REDACTED}}

    assert result == expected


def test_rules_apply_without_a_project(project):
    """Should still run the global rules when the caller has no project in hand."""
    ScrubRule.objects.create(name="email", path="user.email")

    result = service.scrub_payload({"user": {"email": "a@b.test"}}, None)
    expected = {"user": {"email": REDACTED}}

    assert result == expected


# drop rules


def test_a_drop_rule_matches_a_field(project):
    """Should identify what to refuse before anything is written."""
    rule = DropRule.objects.create(name="noisy", field="type", pattern="^Broken")

    payload = {"exception": {"values": [{"type": "BrokenPipeError"}]}}

    result = service.dropped_by(payload, project)

    assert result == rule


def test_a_drop_rule_that_does_not_match_lets_it_through(project):
    """Should not refuse everything once one rule exists."""
    DropRule.objects.create(name="noisy", field="type", pattern="^Broken")

    payload = {"exception": {"values": [{"type": "ValueError"}]}}

    result = service.dropped_by(payload, project)

    assert result is None


def test_a_drop_rule_reads_an_alertmanager_group(project):
    """Should work on the Alertmanager door too, which has a different payload shape."""
    rule = DropRule.objects.create(
        name="watchdog", field="alertname", pattern="Watchdog"
    )

    payload = {"groupLabels": {"alertname": "Watchdog"}}

    result = service.dropped_by(payload, project)

    assert result == rule


def test_a_drop_rule_falls_back_to_common_labels(project):
    """Should find the label wherever Alertmanager put it in this group."""
    rule = DropRule.objects.create(name="info", field="severity", pattern="^info$")

    payload = {"commonLabels": {"severity": "info"}}

    result = service.dropped_by(payload, project)

    assert result == rule


def test_an_invalid_pattern_never_matches(project):
    """Should not take ingest down because someone typed a bad regex into the admin."""
    DropRule.objects.create(name="broken", field="type", pattern="[unclosed")

    payload = {"exception": {"values": [{"type": "ValueError"}]}}

    result = service.dropped_by(payload, project)

    assert result is None


def test_an_unknown_field_never_matches(project):
    """Should ignore a rule naming a field the payload shape has no path for."""
    DropRule.objects.create(name="odd", field="nonsense", pattern=".*")

    result = service.dropped_by({"exception": {"values": [{"type": "E"}]}}, project)

    assert result is None


def test_an_inactive_drop_rule_is_ignored(project):
    """Should let a drop be paused without losing the pattern."""
    DropRule.objects.create(name="noisy", field="type", pattern=".*", active=False)

    result = service.dropped_by({"exception": {"values": [{"type": "E"}]}}, project)

    assert result is None


def test_a_drop_rule_scoped_to_another_project_is_ignored(project):
    """Should keep one project's noise policy off another's ingest."""
    other = core_models.Project.objects.create(slug="apps", name="Applications")
    DropRule.objects.create(name="noisy", field="type", pattern=".*", project=other)

    result = service.dropped_by({"exception": {"values": [{"type": "E"}]}}, project)

    assert result is None


def test_a_global_drop_rule_applies_without_a_project():
    """Should still refuse when the caller has no project in hand."""
    rule = DropRule.objects.create(name="noisy", field="type", pattern=".*")

    result = service.dropped_by({"exception": {"values": [{"type": "E"}]}}, None)

    assert result == rule


def test_a_payload_that_is_not_a_mapping_is_never_dropped(project):
    """Should leave malformed input to the parser rather than swallowing it here."""
    DropRule.objects.create(name="noisy", field="type", pattern=".*")

    result = service.dropped_by(["not", "an", "object"], project)

    assert result is None


def test_a_missing_field_is_not_a_match(project):
    """Should not treat an absent field as an empty string a wildcard would match."""
    DropRule.objects.create(name="noisy", field="release", pattern=".*")

    result = service.dropped_by({"platform": "python"}, project)

    assert result is None


def test_an_exception_list_shorter_than_the_index_is_not_a_match(project):
    """Should tolerate an event whose exception interface is empty."""
    DropRule.objects.create(name="noisy", field="type", pattern=".*")

    result = service.dropped_by({"exception": {"values": []}}, project)

    assert result is None


def test_recording_a_drop_counts_it(project):
    """Should show an operator how much a rule is actually refusing."""
    rule = DropRule.objects.create(name="noisy", field="type", pattern=".*")

    service.record_drop(rule, "sdk")
    service.record_drop(rule, "sdk")
    rule.refresh_from_db()

    result = rule.dropped
    expected = 2

    assert result == expected


def test_a_rule_names_itself_readably(project):
    """Should read as what it does in the admin listings."""
    rule = DropRule.objects.create(name="noisy", field="type", pattern="^Broken")
    scrub_rule = ScrubRule.objects.create(name="email", path="user.email")

    result = (str(rule), str(scrub_rule))
    expected = ("noisy (type~^Broken)", "email (user.email)")

    assert result == expected


def test_a_bare_exception_list_is_read_like_a_values_wrapper(project):
    """Should match the older SDK shape the translator already accepts."""
    rule = DropRule.objects.create(name="noisy", field="type", pattern="^Value")

    result = service.dropped_by({"exception": [{"type": "ValueError"}]}, project)

    assert result == rule


def test_a_nested_field_whose_parent_is_not_an_object_is_not_a_match(project):
    """Should tolerate a client sending a scalar where an interface belongs."""
    DropRule.objects.create(name="noisy", field="message", pattern=".*")

    result = service.dropped_by({"logentry": "not an object"}, project)

    assert result is None
