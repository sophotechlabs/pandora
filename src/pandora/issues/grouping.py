from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable, Mapping, Sequence

from django.db.models import Q

from pandora.core.models import Project
from pandora.issues.models import GroupingMode, GroupingRule

ALERTNAME = "alertname"
DEFAULT_DENY_LABELS = (
    "container",
    "endpoint",
    "instance",
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
    for rule in rules:
        if _matches(rule, alertname):
            return rule
    return default_rule()


def surviving_labels(rule: GroupingRule, labels: Mapping[str, str]) -> dict[str, str]:
    named = {str(name) for name in rule.labels}
    if rule.mode == GroupingMode.ALLOWLIST:
        kept = {key: value for key, value in labels.items() if key in named}
    else:
        kept = {key: value for key, value in labels.items() if key not in named}
    return {str(key): str(value) for key, value in kept.items()}


def compute_fingerprint(rule: GroupingRule, labels: Mapping[str, str]) -> list[str]:
    kept = surviving_labels(rule, labels)
    return [f"{key}:{value}" for key, value in _ordered(kept)]


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
