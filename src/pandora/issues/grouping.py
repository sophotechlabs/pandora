from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from django.db.models import Q

from pandora.core.models import Project
from pandora.issues import conditions, normalise, templates
from pandora.issues.models import GroupingMode, GroupingRule, GroupingSource

ALERTNAME = "alertname"
DEFAULT_DENY_LABELS = (
    "container",
    "endpoint",
    "instance",
    "job_name",
    "node",
    "pod",
    "replicaset",
    "uid",
)
DEFAULT_PRIORITY = 1000
TITLE_MAX = 500
CULPRIT_MAX = 500
UNLABELLED_TITLE = "unlabelled alert"

log = logging.getLogger(__name__)


def default_rule() -> GroupingRule:
    return GroupingRule(
        priority=DEFAULT_PRIORITY,
        mode=GroupingMode.DENYLIST,
        labels=list(DEFAULT_DENY_LABELS),
        active=True,
    )


def source_of(rule: GroupingRule) -> str:
    stored: int | None = rule.pk
    if stored is None:
        return GroupingSource.DEFAULT
    return GroupingSource.RULE


def load_rules(project: Project) -> list[GroupingRule]:
    return list(
        GroupingRule.objects.filter(active=True).filter(
            Q(project__isnull=True) | Q(project=project)
        )
    )


def resolve_rule(
    project: Project,
    alertname: str,
    rules: Sequence[GroupingRule] | None = None,
) -> GroupingRule:
    if rules is None:
        rules = load_rules(project)
    return match_rule(alertname, rules)


def match_rule(alertname: str, rules: Sequence[GroupingRule]) -> GroupingRule:
    return select(rules, alertname=alertname)


def select(
    rules: Sequence[GroupingRule],
    *,
    alertname: str = "",
    document: Any = None,
    require_declaration: bool = False,
) -> GroupingRule:
    """The first rule that applies, or the built-in one.

    `require_declaration` is what keeps an Alertmanager label rule from claiming
    an SDK event it says nothing about: on that door a rule counts only when it
    carries a condition, a fingerprint or a title of its own.
    """
    for rule in rules:
        if require_declaration and not declares(rule):
            continue
        if not _matches(rule, alertname):
            continue
        if not _conditions_hold(rule, document):
            continue
        return rule
    return default_rule()


def declares(rule: GroupingRule) -> bool:
    return bool(rule.conditions or rule.fingerprint or rule.title_template)


def _conditions_hold(rule: GroupingRule, document: Any) -> bool:
    if not rule.conditions:
        return True
    if document is None:
        return False
    try:
        return conditions.matches(rule.conditions, document)
    except conditions.ConditionError as error:
        log.warning("grouping rule %s has an unusable condition: %s", rule.pk, error)
        return False


def fingerprint_for(
    rule: GroupingRule, document: Any, default: Sequence[str]
) -> list[str]:
    """The parts a rule declares, with `{{ default }}` standing for the built-in ones."""
    if not rule.fingerprint:
        return list(default)
    parts: list[str] = []
    for entry in rule.fingerprint:
        text = str(entry)
        if templates.is_default(text):
            parts.extend(default)
            continue
        rendered = templates.render(text, document)
        if rendered:
            parts.append(rendered)
    if not parts:
        return list(default)
    return parts


def title_for(rule: GroupingRule, document: Any, default: str) -> str:
    if not rule.title_template:
        return default
    rendered = templates.render(rule.title_template, document).strip()
    if not rendered:
        return default
    return rendered[:TITLE_MAX]


def surviving_labels(rule: GroupingRule, labels: Mapping[str, str]) -> dict[str, str]:
    named = {str(name) for name in rule.labels}
    if rule.mode == GroupingMode.ALLOWLIST:
        kept = {key: value for key, value in labels.items() if key in named}
    else:
        kept = {key: value for key, value in labels.items() if key not in named}
    return {str(key): str(value) for key, value in kept.items()}


def compute_fingerprint(rule: GroupingRule, labels: Mapping[str, str]) -> list[str]:
    kept = surviving_labels(rule, labels)
    return [f"{key}:{normalise.label(value)}" for key, value in _ordered(kept)]


def fingerprint_hash(fingerprint: Iterable[str]) -> str:
    payload = json.dumps(list(fingerprint), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def derive_title(grouping_labels: Mapping[str, str], summary: str = "") -> str:
    alertname = grouping_labels.get(ALERTNAME, "")
    if alertname and summary:
        title = f"{alertname}: {summary}"
    elif alertname:
        title = alertname
    elif summary:
        title = summary
    else:
        title = derive_culprit(grouping_labels)
    if not title:
        title = UNLABELLED_TITLE
    return title[:TITLE_MAX]


def derive_culprit(grouping_labels: Mapping[str, str]) -> str:
    pairs = [f"{key}={value}" for key, value in _ordered(grouping_labels)]
    return " ".join(pairs)[:CULPRIT_MAX]


def _ordered(labels: Mapping[str, str]) -> list[tuple[str, str]]:
    pairs = []
    alertname = labels.get(ALERTNAME, "")
    if alertname:
        pairs.append((ALERTNAME, alertname))
    for key in sorted(labels):
        if key == ALERTNAME:
            continue
        pairs.append((key, labels[key]))
    return pairs


def _matches(rule: GroupingRule, alertname: str) -> bool:
    if not rule.alertname_regex:
        return True
    try:
        return re.search(rule.alertname_regex, alertname) is not None
    except re.error:
        log.warning(
            "grouping rule %s has an invalid alertname_regex %r — skipped",
            rule.pk,
            rule.alertname_regex,
        )
        return False


REASONS: dict[str, str] = {
    GroupingSource.RULE: "a grouping rule",
    GroupingSource.DEFAULT: "the built-in denylist",
    GroupingSource.STACK: "the exception and the frame it came from",
    GroupingSource.LOGENTRY: "the log message template",
    GroupingSource.MESSAGE: "the message",
    GroupingSource.CLIENT: "a fingerprint the client sent",
}
UNKNOWN_REASON = "an earlier version of pandora, which did not record why"


def reason_for(source: str) -> str:
    return REASONS.get(source, UNKNOWN_REASON)
