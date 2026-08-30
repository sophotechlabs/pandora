import pytest

from pandora.issues import conditions

DOCUMENT = {
    "level": "error",
    "message": "charge declined",
    "labels": {"namespace": "payments", "severity": "critical"},
    "tags": {"tenant": "acme", "attempt": "3"},
    "exceptions": [
        {
            "type": "GatewayError",
            "frames": [
                {"filename": "src/payments/charge.py", "lineno": 42},
                {"filename": "src/http/client.py", "lineno": 7},
            ],
        }
    ],
    "request": {"url": "https://shop.test/checkout"},
}


# reaching into the payload


def test_a_plain_path_reaches_one_value():
    """Should read a scalar the way anyone would write the path."""
    result = conditions.resolve("labels.namespace", DOCUMENT)
    expected = ["payments"]

    assert result == expected


def test_a_wildcard_reaches_every_element():
    """Should be how a rule asks about any frame rather than the first."""
    result = conditions.resolve("exceptions.*.frames.*.filename", DOCUMENT)
    expected = ["src/payments/charge.py", "src/http/client.py"]

    assert result == expected


def test_a_missing_path_reaches_nothing():
    """Should return no values rather than raising on an absent key."""
    result = conditions.resolve("labels.nothing", DOCUMENT)
    expected = []

    assert result == expected


def test_a_path_through_a_list_without_a_wildcard_still_works():
    """Should be forgiving about the wildcard people forget to type."""
    result = conditions.resolve("exceptions.type", DOCUMENT)
    expected = ["GatewayError"]

    assert result == expected


def test_a_wildcard_over_a_mapping_reaches_its_values():
    """Should let a rule ask about any tag without naming the key."""
    result = sorted(conditions.resolve("tags.*", DOCUMENT))
    expected = ["3", "acme"]

    assert result == expected


def test_a_wildcard_over_a_scalar_reaches_nothing():
    """Should not explode a string into characters."""
    result = conditions.resolve("message.*", DOCUMENT)
    expected = []

    assert result == expected


# the operators


@pytest.mark.parametrize(
    ("node", "expected"),
    [
        ({"path": "level", "op": "eq", "value": "error"}, True),
        ({"path": "level", "op": "eq", "value": "warning"}, False),
        ({"path": "level", "op": "ne", "value": "warning"}, True),
        ({"path": "message", "op": "contains", "value": "declined"}, True),
        ({"path": "message", "op": "not_contains", "value": "accepted"}, True),
        ({"path": "message", "op": "startswith", "value": "charge"}, True),
        ({"path": "message", "op": "endswith", "value": "declined"}, True),
        ({"path": "message", "op": "regex_match", "value": "^charge"}, True),
        ({"path": "message", "op": "regex_not_match", "value": "^refund"}, True),
        ({"path": "tags.attempt", "op": "gt", "value": 2}, True),
        ({"path": "tags.attempt", "op": "gte", "value": 3}, True),
        ({"path": "tags.attempt", "op": "lt", "value": 4}, True),
        ({"path": "tags.attempt", "op": "lte", "value": 3}, True),
        ({"path": "request.url", "op": "exists"}, True),
        ({"path": "request.body", "op": "not_exists"}, True),
    ],
)
def test_each_operator_answers_the_question_it_names(node, expected):
    """Should cover the vocabulary a routing rule is written in."""
    result = conditions.matches(node, DOCUMENT)

    assert result == expected


def test_eq_defaults_to_the_operator_people_forget_to_type():
    """Should let the commonest condition be the shortest to write."""
    result = conditions.matches({"path": "level", "value": "error"}, DOCUMENT)

    assert result is True


def test_a_wildcard_condition_matches_when_any_element_does():
    """Should be an existence question over the list, which is the useful one."""
    node = {
        "path": "exceptions.*.frames.*.filename",
        "op": "startswith",
        "value": "src/payments/",
    }

    assert conditions.matches(node, DOCUMENT) is True


def test_a_negative_wildcard_condition_needs_every_element_to_agree():
    """Should mean 'no frame is in payments', not 'some frame is not'."""
    node = {
        "path": "exceptions.*.frames.*.filename",
        "op": "not_contains",
        "value": "payments",
    }

    assert conditions.matches(node, DOCUMENT) is False


def test_a_numeric_comparison_against_a_word_is_false():
    """Should refuse rather than guess when the value is not a number."""
    node = {"path": "message", "op": "gt", "value": 3}

    assert conditions.matches(node, DOCUMENT) is False


def test_a_boolean_is_compared_as_a_word():
    """Should let a rule match `true` without knowing how JSON spelled it."""
    node = {"path": "flag", "op": "eq", "value": "true"}

    assert conditions.matches(node, {"flag": True}) is True


# the tree


def test_all_needs_every_child():
    """Should be the and everyone expects."""
    node = {
        "all": [
            {"path": "level", "value": "error"},
            {"path": "labels.namespace", "value": "payments"},
        ]
    }

    assert conditions.matches(node, DOCUMENT) is True


def test_all_fails_on_one_dissenting_child():
    """Should not quietly become an or."""
    node = {
        "all": [
            {"path": "level", "value": "error"},
            {"path": "labels.namespace", "value": "search"},
        ]
    }

    assert conditions.matches(node, DOCUMENT) is False


def test_any_needs_one_child():
    """Should let a rule cover two shapes of the same fault."""
    node = {
        "any": [
            {"path": "labels.namespace", "value": "search"},
            {"path": "labels.namespace", "value": "payments"},
        ]
    }

    assert conditions.matches(node, DOCUMENT) is True


def test_none_refuses_every_child():
    """Should be the exclusion that keeps a broad rule from over-reaching."""
    node = {
        "none": [
            {"path": "labels.namespace", "value": "search"},
        ]
    }

    assert conditions.matches(node, DOCUMENT) is True


def test_branches_nest():
    """Should be a tree, which is the whole reason it is not a flat list."""
    node = {
        "all": [
            {"path": "level", "value": "error"},
            {
                "any": [
                    {"path": "labels.namespace", "value": "search"},
                    {"path": "request.url", "op": "contains", "value": "checkout"},
                ]
            },
        ]
    }

    assert conditions.matches(node, DOCUMENT) is True


# what is refused


def test_a_condition_that_is_not_an_object_is_refused():
    """Should fail on the file rather than at three in the morning."""
    with pytest.raises(conditions.ConditionError):
        conditions.matches(["level"], DOCUMENT)


def test_an_unknown_operator_is_refused():
    """Should catch the typo instead of matching nothing forever."""
    with pytest.raises(conditions.ConditionError):
        conditions.matches({"path": "level", "op": "sounds_like"}, DOCUMENT)


def test_a_condition_with_no_path_is_refused():
    """Should not accept a condition that can never be evaluated."""
    with pytest.raises(conditions.ConditionError):
        conditions.matches({"op": "exists"}, DOCUMENT)


def test_a_branch_that_is_not_a_list_is_refused():
    """Should be specific about the shape it wanted."""
    with pytest.raises(conditions.ConditionError):
        conditions.matches({"all": {"path": "level"}}, DOCUMENT)


def test_validity_is_checkable_without_a_document():
    """Should let the admin and the config file refuse a bad rule on save."""
    result = conditions.valid({"all": [{"path": "level", "value": "error"}]})

    assert result is True


def test_an_invalid_regular_expression_is_caught_at_check_time():
    """Should not wait for an event to discover the pattern does not compile."""
    result = conditions.valid({"path": "message", "op": "regex_match", "value": "("})

    assert result is False


def test_an_invalid_regular_expression_never_matches_at_run_time():
    """Should not take ingest down if one was stored before the check existed."""
    node = {"path": "message", "op": "regex_match", "value": "("}

    assert conditions.matches(node, DOCUMENT) is False


def test_an_invalid_negative_regular_expression_matches_everything():
    """Should fail open on the negative form, which is the safe direction."""
    node = {"path": "message", "op": "regex_not_match", "value": "("}

    assert conditions.matches(node, DOCUMENT) is True


def test_check_names_what_is_wrong():
    """Should tell an operator which part of the tree to fix."""
    with pytest.raises(conditions.ConditionError, match="not an operator"):
        conditions.check({"path": "level", "op": "sounds_like"})


# the checker, on its own


def test_check_refuses_something_that_is_not_an_object():
    """Should say so before an event ever reaches the rule."""
    with pytest.raises(conditions.ConditionError, match="must be an object"):
        conditions.check(["level"])


def test_check_refuses_a_branch_that_is_not_a_list():
    """Should name the branch that was written wrong."""
    with pytest.raises(conditions.ConditionError, match="takes a list"):
        conditions.check({"any": {"path": "level"}})


def test_check_walks_into_the_branches():
    """Should catch a bad leaf however deeply it is nested."""
    with pytest.raises(conditions.ConditionError, match="not an operator"):
        conditions.check({"all": [{"any": [{"path": "x", "op": "nope"}]}]})


def test_check_accepts_a_whole_valid_tree():
    """Should not refuse the shape the docs tell people to write."""
    conditions.check(
        {
            "all": [
                {"path": "level", "value": "error"},
                {"any": [{"path": "tags.tenant", "op": "exists"}]},
            ]
        }
    )


def test_check_refuses_a_condition_with_no_path():
    """Should be specific about the missing half."""
    with pytest.raises(conditions.ConditionError, match="needs a path"):
        conditions.check({"op": "exists"})


def test_a_null_compares_as_an_empty_string():
    """Should let a rule ask whether a nullable field is blank."""
    result = conditions.matches({"path": "note", "value": ""}, {"note": None})

    assert result is True


def test_a_float_compares_numerically():
    """Should not turn 1.5 into a string before comparing it."""
    result = conditions.matches(
        {"path": "ratio", "op": "gt", "value": 1}, {"ratio": 1.5}
    )

    assert result is True


def test_a_boolean_is_never_a_number():
    """Should refuse True > 0 rather than answering it."""
    result = conditions.matches(
        {"path": "flag", "op": "gt", "value": 0}, {"flag": True}
    )

    assert result is False
