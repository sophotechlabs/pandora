from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

log = logging.getLogger(__name__)

ALL = "all"
ANY = "any"
NONE = "none"
BRANCHES = (ALL, ANY, NONE)

EQ = "eq"
NE = "ne"
CONTAINS = "contains"
NOT_CONTAINS = "not_contains"
STARTSWITH = "startswith"
ENDSWITH = "endswith"
REGEX = "regex_match"
NOT_REGEX = "regex_not_match"
GT = "gt"
GTE = "gte"
LT = "lt"
LTE = "lte"
EXISTS = "exists"
NOT_EXISTS = "not_exists"

OPERATORS = (
    EQ,
    NE,
    CONTAINS,
    NOT_CONTAINS,
    STARTSWITH,
    ENDSWITH,
    REGEX,
    NOT_REGEX,
    GT,
    GTE,
    LT,
    LTE,
    EXISTS,
    NOT_EXISTS,
)

WILDCARD = "*"


class ConditionError(ValueError):
    pass


def resolve(path: str, document: Any) -> list[Any]:
    """Every value a dot path reaches, with `*` standing for any list element.

    `exceptions.*.frames.*.filename` reaches every frame of every exception, so a
    condition over it asks whether *any* frame matches — which is the question
    someone writing a routing rule is actually asking.
    """
    found: list[Any] = [document]
    for segment in path.split("."):
        step: list[Any] = []
        for value in found:
            step.extend(_descend(value, segment))
        found = step
    return found


def matches(node: Any, document: Any) -> bool:
    if not isinstance(node, Mapping):
        raise ConditionError("a condition must be an object")
    for branch in BRANCHES:
        if branch in node:
            return _branch(branch, node[branch], document)
    return _leaf(node, document)


def valid(node: Any) -> bool:
    try:
        _check(node)
    except ConditionError:
        return False
    return True


def check(node: Any) -> None:
    _check(node)


def _check(node: Any) -> None:
    if not isinstance(node, Mapping):
        raise ConditionError("a condition must be an object")
    for branch in BRANCHES:
        if branch in node:
            children = node[branch]
            if not isinstance(children, Sequence) or isinstance(children, str):
                raise ConditionError(f"{branch} takes a list of conditions")
            for child in children:
                _check(child)
            return
    operator = node.get("op", EQ)
    if operator not in OPERATORS:
        raise ConditionError(f"{operator!r} is not an operator")
    if not node.get("path"):
        raise ConditionError("a condition needs a path")
    if operator in (REGEX, NOT_REGEX):
        try:
            re.compile(str(node.get("value", "")))
        except re.error as error:
            raise ConditionError(f"invalid regular expression: {error}") from error


def _branch(branch: str, children: Any, document: Any) -> bool:
    if not isinstance(children, Sequence) or isinstance(children, str):
        raise ConditionError(f"{branch} takes a list of conditions")
    results = [matches(child, document) for child in children]
    if branch == ALL:
        return all(results)
    if branch == ANY:
        return any(results)
    return not any(results)


def _leaf(node: Mapping[str, Any], document: Any) -> bool:
    path = str(node.get("path", ""))
    if not path:
        raise ConditionError("a condition needs a path")
    operator = str(node.get("op", EQ))
    if operator not in OPERATORS:
        raise ConditionError(f"{operator!r} is not an operator")
    wanted = node.get("value")
    found = resolve(path, document)
    if operator == EXISTS:
        return bool(found)
    if operator == NOT_EXISTS:
        return not found
    if operator in (NE, NOT_CONTAINS, NOT_REGEX):
        return all(_compare(operator, value, wanted) for value in found)
    return any(_compare(operator, value, wanted) for value in found)


def _descend(value: Any, segment: str) -> list[Any]:
    if segment == WILDCARD:
        if isinstance(value, Sequence) and not isinstance(value, str):
            return list(value)
        if isinstance(value, Mapping):
            return list(value.values())
        return []
    if isinstance(value, Mapping) and segment in value:
        return [value[segment]]
    if isinstance(value, Sequence) and not isinstance(value, str):
        return [item[segment] for item in value if _has(item, segment)]
    return []


def _has(item: Any, segment: str) -> bool:
    return isinstance(item, Mapping) and segment in item


def _compare(operator: str, value: Any, wanted: Any) -> bool:
    if operator == EQ:
        return _text(value) == _text(wanted)
    if operator == NE:
        return _text(value) != _text(wanted)
    if operator == CONTAINS:
        return _text(wanted) in _text(value)
    if operator == NOT_CONTAINS:
        return _text(wanted) not in _text(value)
    if operator == STARTSWITH:
        return _text(value).startswith(_text(wanted))
    if operator == ENDSWITH:
        return _text(value).endswith(_text(wanted))
    if operator in (REGEX, NOT_REGEX):
        return _regex(operator, value, wanted)
    return _numeric(operator, value, wanted)


def _regex(operator: str, value: Any, wanted: Any) -> bool:
    try:
        hit = re.search(str(wanted), _text(value)) is not None
    except re.error:
        log.warning("condition holds an invalid regular expression %r", wanted)
        return operator == NOT_REGEX
    if operator == REGEX:
        return hit
    return not hit


def _numeric(operator: str, value: Any, wanted: Any) -> bool:
    left = _number(value)
    right = _number(wanted)
    if left is None or right is None:
        return False
    if operator == GT:
        return left > right
    if operator == GTE:
        return left >= right
    if operator == LT:
        return left < right
    return left <= right


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)
