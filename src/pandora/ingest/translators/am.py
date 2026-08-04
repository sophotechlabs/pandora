from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from django.utils import timezone
from ulid import ULID

from pandora.core.models import Project
from pandora.issues import grouping, lifecycle
from pandora.issues.models import GroupingRule, Level

PAYLOAD_VERSION = "4"
SEVERITY_LABEL = "severity"
SUMMARY_ANNOTATION = "summary"
DESCRIPTION_ANNOTATION = "description"
DEFAULT_LEVEL = Level.ERROR
SEVERITY_LEVELS = {
    "critical": Level.ERROR,
    "error": Level.ERROR,
    "warning": Level.WARNING,
    "warn": Level.WARNING,
    "info": Level.INFO,
    "none": Level.INFO,
    "debug": Level.DEBUG,
    "fatal": Level.FATAL,
}
EPOCH_YEAR = 1970
ID_TIME_BYTES = 6
ID_RANDOM_BYTES = 10

log = logging.getLogger(__name__)


class PayloadError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedGroup:
    occurrences: list[lifecycle.Occurrence] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)


def validate(payload: Any) -> None:
    if not isinstance(payload, Mapping):
        raise PayloadError("payload is not a JSON object")
    version = str(payload.get("version", ""))
    if version != PAYLOAD_VERSION:
        raise PayloadError(f"unsupported Alertmanager payload version {version!r}")
    if not isinstance(payload.get("alerts"), list):
        raise PayloadError("payload carries no alerts list")


def parse_group(
    payload: Any,
    project: Project,
    *,
    environment: str = "",
    received_at: datetime | None = None,
) -> ParsedGroup:
    validate(payload)
    if received_at is None:
        received_at = timezone.now()
    _log_truncation(payload)
    rules = grouping.load_rules(project)

    occurrences = []
    rejected = []
    for index, alert in enumerate(payload["alerts"]):
        try:
            occurrences.append(
                _occurrence(alert, payload, rules, environment, received_at)
            )
        except PayloadError as error:
            rejected.append(f"alert {index}: {error}")
            log.warning(
                "alertmanager alert %s in group %s was unusable: %s",
                index,
                payload.get("groupKey", ""),
                error,
            )
    return ParsedGroup(occurrences=occurrences, rejected=rejected)


def event_id(project_id: int, occurrence: lifecycle.Occurrence) -> str:
    key = "|".join(
        [
            str(project_id),
            occurrence.source,
            occurrence.am_fingerprint,
            occurrence.starts_at.isoformat(),
            occurrence.status,
        ]
    )
    digest = hashlib.sha256(key.encode()).digest()
    millis = int(occurrence.timestamp.timestamp() * 1000)
    millis = max(millis, 0)
    stamp = millis.to_bytes(ID_TIME_BYTES, "big")
    return str(ULID.from_bytes(stamp + digest[:ID_RANDOM_BYTES]))


def _log_truncation(payload: Mapping[str, Any]) -> None:
    truncated = payload.get("truncatedAlerts", 0)
    if not isinstance(truncated, int):
        return
    if truncated <= 0:
        return
    log.warning(
        "alertmanager dropped %s alerts from group %s before delivery",
        truncated,
        payload.get("groupKey", ""),
    )


def _occurrence(
    alert: Any,
    payload: Mapping[str, Any],
    rules: Sequence[GroupingRule],
    environment: str,
    received_at: datetime,
) -> lifecycle.Occurrence:
    if not isinstance(alert, Mapping):
        raise PayloadError("alert is not a JSON object")

    status = str(alert.get("status", ""))
    if status not in (lifecycle.STATUS_FIRING, lifecycle.STATUS_RESOLVED):
        raise PayloadError(f"unsupported alert status {status!r}")

    am_fingerprint = str(alert.get("fingerprint", ""))
    if not am_fingerprint:
        raise PayloadError("alert carries no fingerprint")

    starts_at = _timestamp(alert.get("startsAt"))
    if starts_at is None:
        raise PayloadError("alert carries no startsAt")

    labels = _strings(alert.get("labels"))
    annotations = _strings(alert.get("annotations"))
    rule = grouping.match_rule(labels.get(grouping.ALERTNAME, ""), rules)
    grouping_labels = grouping.surviving_labels(rule, labels)
    fingerprint = grouping.compute_fingerprint(rule, labels)
    summary = annotations.get(SUMMARY_ANNOTATION, "")
    title = grouping.derive_title(grouping_labels, summary)
    message = annotations.get(DESCRIPTION_ANNOTATION, "")
    if not message:
        message = title

    ends_at = None
    if status == lifecycle.STATUS_RESOLVED:
        ends_at = _resolution(alert, starts_at, received_at)

    return lifecycle.Occurrence(
        fingerprint=fingerprint,
        fingerprint_hash=grouping.fingerprint_hash(fingerprint),
        grouping_labels=grouping_labels,
        am_fingerprint=am_fingerprint,
        labels=labels,
        status=status,
        title=title,
        culprit=grouping.derive_culprit(grouping_labels),
        level=_level(labels),
        message=message,
        starts_at=starts_at,
        ends_at=ends_at,
        timestamp=received_at,
        tags=dict(labels),
        extra={
            "annotations": annotations,
            "generatorURL": str(alert.get("generatorURL", "")),
            "externalURL": str(payload.get("externalURL", "")),
            "groupKey": str(payload.get("groupKey", "")),
        },
        environment=environment,
        source="am",
    )


def _resolution(
    alert: Mapping[str, Any], starts_at: datetime, received_at: datetime
) -> datetime:
    ends_at = _timestamp(alert.get("endsAt"))
    if ends_at is None:
        ends_at = received_at
    ends_at = max(ends_at, starts_at)
    return ends_at


def _level(labels: Mapping[str, str]) -> str:
    severity = labels.get(SEVERITY_LABEL, "").lower()
    return SEVERITY_LEVELS.get(severity, DEFAULT_LEVEL)


def _strings(raw: Any) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def _timestamp(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    if parsed.year < EPOCH_YEAR:
        return None
    return parsed.astimezone(UTC)
