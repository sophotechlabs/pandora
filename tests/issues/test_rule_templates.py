import pytest

from pandora.issues import grouping, templates
from pandora.issues.models import GroupingRule

DOCUMENT = {
    "level": "error",
    "message": "charge declined",
    "tags": {"tenant": "acme"},
    "labels": {"namespace": "payments"},
    "request": {"url": "https://shop.test/checkout"},
}
DEFAULT = ["GatewayError", "charge"]


def rule(**overrides):
    return GroupingRule(**overrides)


# rendering


def test_a_path_is_interpolated():
    """Should let a template name a value from the occurrence being grouped."""
    result = templates.render("tenant {{ tags.tenant }}", DOCUMENT)
    expected = "tenant acme"

    assert result == expected


def test_a_path_with_no_spaces_works_too():
    """Should not make whitespace part of the syntax."""
    result = templates.render("{{tags.tenant}}", DOCUMENT)
    expected = "acme"

    assert result == expected


def test_a_missing_path_renders_as_nothing():
    """Should let a template name a tag that not every event carries."""
    result = templates.render("tenant {{ tags.missing }}", DOCUMENT)
    expected = "tenant "

    assert result == expected


def test_several_paths_render_in_one_string():
    """Should compose, which is the point of a template over a field."""
    result = templates.render("{{ labels.namespace }}/{{ tags.tenant }}", DOCUMENT)
    expected = "payments/acme"

    assert result == expected


def test_the_default_placeholder_is_recognised():
    """Should be the one name that means the built-in algorithm."""
    result = (
        templates.is_default("{{ default }}"),
        templates.is_default("{{ tags.x }}"),
    )
    expected = (True, False)

    assert result == expected


def test_a_default_placeholder_with_text_around_it_is_not_the_default():
    """Should only expand when the whole part is the placeholder."""
    result = templates.is_default("a {{ default }}")

    assert result is False


# the fingerprint a rule declares


def test_a_rule_with_no_fingerprint_keeps_the_default():
    """Should leave the built-in algorithm alone unless asked."""
    result = grouping.fingerprint_for(rule(), DOCUMENT, DEFAULT)
    expected = DEFAULT

    assert result == expected


def test_a_rule_can_replace_the_fingerprint_entirely():
    """Should let an operator group on something the algorithm cannot see."""
    result = grouping.fingerprint_for(rule(fingerprint=["checkout"]), DOCUMENT, DEFAULT)
    expected = ["checkout"]

    assert result == expected


def test_a_rule_can_refine_the_default_rather_than_replace_it():
    """Should be the difference between splitting an issue and throwing it away."""
    result = grouping.fingerprint_for(
        rule(fingerprint=["{{ default }}", "{{ tags.tenant }}"]), DOCUMENT, DEFAULT
    )
    expected = ["GatewayError", "charge", "acme"]

    assert result == expected


def test_a_part_that_renders_to_nothing_is_dropped():
    """Should not put an empty component into the key."""
    result = grouping.fingerprint_for(
        rule(fingerprint=["{{ default }}", "{{ tags.missing }}"]), DOCUMENT, DEFAULT
    )
    expected = DEFAULT

    assert result == expected


def test_a_fingerprint_that_renders_to_nothing_falls_back_to_the_default():
    """Should never leave an occurrence with no key at all."""
    result = grouping.fingerprint_for(
        rule(fingerprint=["{{ tags.missing }}"]), DOCUMENT, DEFAULT
    )
    expected = DEFAULT

    assert result == expected


# the title a rule declares


def test_a_rule_with_no_title_keeps_the_derived_one():
    """Should leave the title alone unless asked."""
    result = grouping.title_for(rule(), DOCUMENT, "GatewayError: charge")
    expected = "GatewayError: charge"

    assert result == expected


def test_a_rule_can_set_the_title_from_the_payload():
    """Should be one field, and nobody free ships it."""
    result = grouping.title_for(
        rule(title_template="checkout failed for {{ tags.tenant }}"),
        DOCUMENT,
        "GatewayError: charge",
    )
    expected = "checkout failed for acme"

    assert result == expected


def test_a_title_that_renders_to_nothing_falls_back():
    """Should not replace a real title with an empty one."""
    result = grouping.title_for(
        rule(title_template="{{ tags.missing }}"), DOCUMENT, "GatewayError: charge"
    )
    expected = "GatewayError: charge"

    assert result == expected


def test_a_long_title_is_cut_to_the_column():
    """Should store a huge template rather than raising on the write."""
    result = grouping.title_for(
        rule(title_template="x" * 900), DOCUMENT, "GatewayError"
    )

    assert len(result) == 500


# which rule applies


@pytest.mark.django_db
def test_a_rule_whose_conditions_hold_is_chosen(project):
    """Should route by what is in the payload, not only by the alertname."""
    wanted = GroupingRule.objects.create(
        priority=10,
        conditions={"path": "labels.namespace", "value": "payments"},
        fingerprint=["payments"],
    )

    result = grouping.select([wanted], document=DOCUMENT)

    assert result.pk == wanted.pk


@pytest.mark.django_db
def test_a_rule_whose_conditions_fail_is_skipped(project):
    """Should fall through to the next rule rather than matching everything."""
    skipped = GroupingRule.objects.create(
        priority=10,
        conditions={"path": "labels.namespace", "value": "search"},
        fingerprint=["search"],
    )

    result = grouping.select([skipped], document=DOCUMENT)

    assert result.pk is None


@pytest.mark.django_db
def test_a_conditional_rule_needs_a_document(project):
    """Should not apply a payload condition where there is no payload."""
    conditional = GroupingRule.objects.create(
        priority=10, conditions={"path": "labels.namespace", "value": "payments"}
    )

    result = grouping.select([conditional], document=None)

    assert result.pk is None


@pytest.mark.django_db
def test_an_unusable_condition_skips_the_rule_rather_than_raising(project):
    """Should never let a badly typed rule take ingest down."""
    broken = GroupingRule.objects.create(priority=10, conditions={"op": "eq"})

    result = grouping.select([broken], document=DOCUMENT)

    assert result.pk is None


@pytest.mark.django_db
def test_a_label_rule_says_nothing_to_an_sdk_event(project):
    """Should keep an Alertmanager denylist from claiming a stack trace."""
    labels_only = GroupingRule.objects.create(priority=10, labels=["pod"])

    result = grouping.select([labels_only], document=DOCUMENT, require_declaration=True)

    assert result.pk is None


@pytest.mark.django_db
def test_a_declaring_rule_does_apply_to_an_sdk_event(project):
    """Should let one rule table serve both doors when a rule opts in."""
    declaring = GroupingRule.objects.create(
        priority=10, title_template="checkout: {{ tags.tenant }}"
    )

    result = grouping.select([declaring], document=DOCUMENT, require_declaration=True)

    assert result.pk == declaring.pk


def test_a_null_renders_as_nothing():
    """Should not put the word None into a title."""
    result = templates.render("note {{ note }}", {"note": None})
    expected = "note "

    assert result == expected


def test_a_boolean_renders_as_a_word():
    """Should read the way the payload spelled it, not the way Python does."""
    result = templates.render("{{ flag }}", {"flag": True})
    expected = "true"

    assert result == expected
