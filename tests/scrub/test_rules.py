import pytest

from pandora.scrub import paths, rules

# keyword removal


@pytest.mark.parametrize(
    "key",
    ["password", "PASSWORD", "api_key", "authorization", "X-Auth-Token", "session_id"],
)
def test_a_secret_looking_key_is_removed(key):
    """Should redact by key name, which catches the field nobody remembered to configure."""
    result = rules.scrub({key: "hunter2"})
    expected = {key: rules.REDACTED}

    assert result == expected


def test_an_ordinary_key_is_left_alone():
    """Should not redact everything — a scrubber that eats the payload is one people turn off."""
    result = rules.scrub({"namespace": "payments"})
    expected = {"namespace": "payments"}

    assert result == expected


def test_a_secret_nested_in_a_list_is_removed():
    """Should walk arrays too, because frame locals and headers arrive inside them."""
    result = rules.scrub({"frames": [{"vars": {"token": "abc"}}]})
    expected = {"frames": [{"vars": {"token": rules.REDACTED}}]}

    assert result == expected


def test_a_safe_key_survives_the_keyword_list():
    """Should let an operator keep a field whose name only looks secret."""
    result = rules.scrub({"auth_provider": "keycloak"}, safe_keys=["auth_provider"])
    expected = {"auth_provider": "keycloak"}

    assert result == expected


def test_an_extra_keyword_is_honoured():
    """Should let an install redact a field name only it uses."""
    result = rules.scrub({"pin": "1234"}, keywords=[*rules.KEYWORDS, "pin"])
    expected = {"pin": rules.REDACTED}

    assert result == expected


def test_recursion_stops_at_the_depth_limit():
    """Should not follow a hostile payload down forever."""
    node = {"token": "abc"}
    for _ in range(rules.MAX_DEPTH + 4):
        node = {"deeper": node}

    result = rules.scrub(node)

    assert isinstance(result, dict)


# card numbers


def test_a_card_number_is_masked_anywhere_in_a_string():
    """Should catch what a keyword cannot — a number in a free-text message."""
    result = rules.mask_card("charged 4111 1111 1111 1111 for the order")
    expected = f"charged {rules.REDACTED} for the order"

    assert result == expected


def test_a_number_that_is_not_a_card_is_left_alone():
    """Should check the Luhn digit, or every long id in a log line reads as a card."""
    result = rules.mask_card("request 1234567890123456 finished")
    expected = "request 1234567890123456 finished"

    assert result == expected


def test_a_card_inside_a_nested_value_is_masked():
    """Should reach the payment id a client put in extra."""
    result = rules.scrub({"extra": {"note": "card 4111111111111111"}})
    expected = {"extra": {"note": f"card {rules.REDACTED}"}}

    assert result == expected


# ip addresses


def test_an_ip_address_loses_its_last_octet():
    """Should keep the network useful for debugging while dropping the household."""
    result = rules.mask_ip("client 203.0.113.44 connected")
    expected = "client 203.0.113.0 connected"

    assert result == expected


def test_something_that_looks_like_an_ip_but_is_not_is_left_alone():
    """Should not rewrite a version string into nonsense."""
    result = rules.mask_ip("version 999.888.777.666")
    expected = "version 999.888.777.666"

    assert result == expected


def test_ip_anonymisation_can_be_turned_off():
    """Should let an operator who needs the exact address keep it."""
    result = rules.scrub({"ip_address": "203.0.113.44"}, anonymise_ip=False)
    expected = {"ip_address": "203.0.113.44"}

    assert result == expected


def test_a_non_string_value_is_untouched():
    """Should leave numbers and booleans as they are so the API keeps round-tripping."""
    result = rules.scrub({"count": 3, "ok": True, "missing": None})
    expected = {"count": 3, "ok": True, "missing": None}

    assert result == expected


# path selectors


@pytest.mark.parametrize(
    ("path", "trail", "matched"),
    [
        ("user.email", ("user", "email"), True),
        ("user.email", ("user", "name"), False),
        ("user.*", ("user", "email"), True),
        ("**.password", ("a", "b", "password"), True),
        ("**.password", ("password",), True),
        ("request.headers.*", ("request", "headers", "Cookie"), True),
        ("request.headers.*", ("request", "headers"), False),
        ("frames.*.vars", ("frames", "0", "vars"), True),
    ],
)
def test_a_path_matches_the_trail_it_describes(path, trail, matched):
    """Should read the way an operator would expect a dotted path with wildcards to read."""
    result = paths.matches(paths.split(path), trail)

    assert result is matched


def test_a_path_rule_removes_the_value_it_names():
    """Should let an operator redact one field without touching its neighbours."""
    payload = {"user": {"email": "a@b.test", "id": "7"}}

    result = paths.apply(payload, "user.email", "remove")
    expected = {"user": {"email": rules.REDACTED, "id": "7"}}

    assert result == expected


def test_a_mask_action_keeps_the_shape_and_hides_the_content():
    """Should leave a masked card readable as 'there was a card here'."""
    payload = {"extra": {"note": "4111111111111111"}}

    result = paths.apply(payload, "extra.note", "mask")
    expected = {"extra": {"note": rules.REDACTED}}

    assert result == expected


def test_a_mask_action_leaves_a_non_string_alone():
    """Should not turn a number into a redaction marker."""
    payload = {"extra": {"count": 3}}

    result = paths.apply(payload, "extra.count", "mask")
    expected = {"extra": {"count": 3}}

    assert result == expected


def test_a_deep_wildcard_reaches_every_frame():
    """Should redact locals across a whole stack with one rule."""
    payload = {
        "exceptions": [{"frames": [{"vars": {"card": "x"}}, {"vars": {"card": "y"}}]}]
    }

    result = paths.apply(payload, "**.vars", "remove")
    expected = {
        "exceptions": [{"frames": [{"vars": rules.REDACTED}, {"vars": rules.REDACTED}]}]
    }

    assert result == expected


def test_a_path_that_matches_nothing_changes_nothing():
    """Should be safe to leave a rule in place after the field it named is gone."""
    payload = {"user": {"id": "7"}}

    result = paths.apply(payload, "request.cookies", "remove")
    expected = payload

    assert result == expected
