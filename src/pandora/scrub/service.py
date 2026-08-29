from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from django.conf import settings
from django.db import models
from django.db.models import F
from prometheus_client import Counter

from pandora.core.models import Project
from pandora.scrub import paths, rules
from pandora.scrub.models import DropRule, ScrubRule

DROPPED = Counter(
    "pandora_ingest_dropped_total",
    "Payloads a drop rule refused before the durable write",
    ["source", "rule"],
)

DROP_FIELDS: dict[str, tuple[tuple[Any, ...], ...]] = {
    "message": (("logentry", "formatted"), ("commonAnnotations", "summary")),
    "type": (("exception", 0, "type"),),
    "value": (("exception", 0, "value"),),
    "release": (("release",),),
    "environment": (("environment",),),
    "server_name": (("server_name",),),
    "transaction": (("transaction",),),
    "platform": (("platform",),),
    "alertname": (("groupLabels", "alertname"), ("commonLabels", "alertname")),
    "namespace": (("groupLabels", "namespace"), ("commonLabels", "namespace")),
    "severity": (("commonLabels", "severity"),),
}


def keywords() -> tuple[str, ...]:
    extra = tuple(
        part.strip()
        for part in settings.PANDORA_SCRUB_KEYWORDS.split(",")
        if part.strip()
    )
    return rules.KEYWORDS + extra


def safe_keys() -> tuple[str, ...]:
    return tuple(
        part.strip()
        for part in settings.PANDORA_SCRUB_SAFE_KEYS.split(",")
        if part.strip()
    )


def scrub_payload(payload: Any, project: Project | None = None) -> Any:
    if not settings.PANDORA_SCRUB_ENABLED:
        return payload
    scrubbed = rules.scrub(
        payload,
        keywords=keywords(),
        safe_keys=safe_keys(),
        anonymise_ip=settings.PANDORA_SCRUB_ANONYMISE_IP,
    )
    for rule in _rules(project):
        scrubbed = paths.apply(scrubbed, rule.path, rule.action)
    return scrubbed


def _rules(project: Project | None) -> list[ScrubRule]:
    queryset = ScrubRule.objects.filter(active=True)
    if project is None:
        return list(queryset.filter(project=None))
    return list(
        queryset.filter(models.Q(project=None) | models.Q(project_id=project.pk))
    )


def _dig(payload: Any, trail: tuple[Any, ...]) -> Any:
    node = payload
    for step in trail:
        if isinstance(step, int):
            values = node
            if isinstance(node, Mapping):
                values = node.get("values")
            if not isinstance(values, list) or len(values) <= step:
                return None
            node = values[step]
            continue
        if not isinstance(node, Mapping):
            return None
        node = node.get(step)
    return node


def dropped_by(payload: Any, project: Project | None) -> DropRule | None:
    if not isinstance(payload, Mapping):
        return None
    for rule in _drop_rules(project):
        if _rule_matches(rule, payload):
            return rule
    return None


def _rule_matches(rule: DropRule, payload: Mapping[str, Any]) -> bool:
    try:
        pattern = re.compile(rule.pattern)
    except re.error:
        return False
    for trail in DROP_FIELDS.get(rule.field, ()):
        value = _dig(payload, trail)
        if value is None:
            continue
        if pattern.search(str(value)):
            return True
    return False


def _drop_rules(project: Project | None) -> list[DropRule]:
    queryset = DropRule.objects.filter(active=True)
    if project is None:
        return list(queryset.filter(project=None))
    return list(
        queryset.filter(models.Q(project=None) | models.Q(project_id=project.pk))
    )


def scrub_message(text: str) -> str:
    if not settings.PANDORA_SCRUB_ENABLED:
        return text
    return rules.scrub_value(text, anonymise_ip=settings.PANDORA_SCRUB_ANONYMISE_IP)


def record_drop(rule: DropRule, source: str) -> None:
    DROPPED.labels(source=source, rule=rule.name).inc()
    DropRule.objects.filter(pk=rule.pk).update(dropped=F("dropped") + 1)
