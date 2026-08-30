import pytest

from pandora.issues import normalise


@pytest.fixture
def on(settings):
    settings.PANDORA_GROUPING_NORMALISE = True
    return settings


# what leaves the key


def test_a_uuid_is_replaced():
    """Should be the commonest reason one fault mints a thousand issues."""
    result = normalise.value("order 3f2504e0-4f89-11d3-9a0c-0305e82c3301 failed")
    expected = "order <uuid> failed"

    assert result == expected


def test_a_uuid_in_upper_case_is_replaced_too():
    """Should not depend on which library formatted it."""
    result = normalise.value("3F2504E0-4F89-11D3-9A0C-0305E82C3301")
    expected = "<uuid>"

    assert result == expected


def test_a_url_is_replaced_whole():
    """Should collapse a path with an id in it to one thing, not to two."""
    result = normalise.value("GET https://shop.test/orders/8891 failed")
    expected = "GET <url> failed"

    assert result == expected


def test_an_email_is_replaced():
    """Should keep a per-user error from becoming a per-user issue."""
    result = normalise.value("no mailbox for ada@example.test")
    expected = "no mailbox for <email>"

    assert result == expected


def test_an_ipv4_address_is_replaced():
    """Should group one connection failure rather than one per peer."""
    result = normalise.value("connect 10.10.0.14 refused")
    expected = "connect <ip> refused"

    assert result == expected


def test_an_ipv6_address_is_replaced():
    """Should do the same on a cluster that runs dual stack."""
    result = normalise.value("connect fd00:1234:5678:9abc::1 refused")

    assert "<ip>" in result


def test_an_iso_timestamp_is_replaced():
    """Should not put the moment of failure into the identity of the failure."""
    result = normalise.value("expired at 2026-08-30T12:04:11Z")
    expected = "expired at <date>"

    assert result == expected


def test_a_bare_date_is_replaced():
    """Should catch the short form as well as the full one."""
    result = normalise.value("partition 2026-08-30 missing")
    expected = "partition <date> missing"

    assert result == expected


def test_a_long_hex_string_is_replaced():
    """Should collapse a git sha, a request id or a pod's hash suffix."""
    result = normalise.value("ledger-7d9f4c8b6d-hk2mp")
    expected = "ledger-<hex>-hk2mp"

    assert result == expected


def test_a_bare_number_is_replaced():
    """Should be the last resort, after the shapes that contain numbers."""
    result = normalise.value("retry 47 of 50")
    expected = "retry <n> of <n>"

    assert result == expected


# what stays


def test_a_word_is_left_alone():
    """Should not touch the part of the key that identifies the fault."""
    result = normalise.value("KubePodCrashLooping")
    expected = "KubePodCrashLooping"

    assert result == expected


def test_a_short_hex_looking_word_is_left_alone():
    """Should not eat 'added' or 'faced' — the threshold is eight characters."""
    result = normalise.value("decade")
    expected = "decade"

    assert result == expected


def test_a_number_glued_to_a_word_is_left_alone():
    """Should replace a number that is its own token, not one inside an identifier.

    `v1` and `http2` are names; the `47` in `retry 47` is not. The word boundary
    is what separates them, and it is the whole rule.
    """
    result = normalise.value("v1.2.3 on http2")
    expected = "v1.<n>.<n> on http2"

    assert result == expected


# the switch


def test_nothing_changes_while_it_is_off(settings):
    """Should be inert until an operator has run regroup --dry-run against real data."""
    settings.PANDORA_GROUPING_NORMALISE = False

    result = normalise.parts(["order 3f2504e0-4f89-11d3-9a0c-0305e82c3301"])
    expected = ["order 3f2504e0-4f89-11d3-9a0c-0305e82c3301"]

    assert result == expected


def test_every_part_is_normalised_when_it_is_on(on):
    """Should apply to the whole fingerprint, not to its first component."""
    result = normalise.parts(["ValueError", "order 8891"])
    expected = ["ValueError", "order <n>"]

    assert result == expected


def test_a_label_is_left_alone_while_it_is_off(settings):
    """Should keep the Alertmanager path inert too."""
    settings.PANDORA_GROUPING_NORMALISE = False

    result = normalise.label("ledger-7d9f4c8b6d-hk2mp")
    expected = "ledger-7d9f4c8b6d-hk2mp"

    assert result == expected


def test_a_label_is_normalised_when_it_is_on(on):
    """Should collapse the pod suffix that fragments a KubePodCrashLooping issue."""
    result = normalise.label("ledger-7d9f4c8b6d-hk2mp")
    expected = "ledger-<hex>-hk2mp"

    assert result == expected


def test_normalising_is_stable(on):
    """Should give the same key twice, or the hash means nothing."""
    once = normalise.value("order 3f2504e0-4f89-11d3-9a0c-0305e82c3301 at 2026-08-30")

    result = normalise.value(once)
    expected = once

    assert result == expected
