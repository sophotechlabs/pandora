from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.utils import timezone
from prometheus_client import Counter, Gauge

from pandora.am import client as am_client
from pandora.core.models import IngestToken, Project, TokenScope, TokenSource
from pandora.ingest.models import EnvelopeState, RawEnvelope
from pandora.ingest.queue import get_queue
from pandora.issues.models import Episode

WATCHDOG_ALERTNAME = "Watchdog"
ALERTNAME_LABEL = "alertname"
CLOSE_AFTER_MISSES = 3
PAYLOAD_VERSION = "4"
RECEIVER = "pandora-reconcile"
STATUS_FIRING = "firing"
STATUS_RESOLVED = "resolved"

WATCHDOG_SEEN = Gauge(
    "pandora_watchdog_last_seen_timestamp",
    "Unix time reconcile last saw the Watchdog alert in Alertmanager",
)
CYCLES = Counter(
    "pandora_reconcile_cycles_total",
    "Reconcile cycles by result",
    ["result"],
)
ACTIONS = Counter(
    "pandora_reconcile_actions_total",
    "Episode corrections reconcile handed to the ingest consumer",
    ["action"],
)

log = logging.getLogger(__name__)


class ScopeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Scope:
    project: Project
    environment: str


@dataclass
class ReconcileReport:
    alerts: int = 0
    open_episodes: int = 0
    opened: int = 0
    closed: int = 0
    missing: int = 0
    watchdog: bool = False
    error: str = ""


def resolve_scope(project_slug: str = "", environment: str = "") -> Scope:
    tokens = IngestToken.objects.filter(
        source=TokenSource.AM,
        scope=TokenScope.INGEST,
        active=True,
    ).select_related("project")
    if project_slug:
        tokens = tokens.filter(project__slug=project_slug)
    if environment:
        tokens = tokens.filter(environment=environment)

    found: dict[tuple[str, str], Scope] = {}
    for token in tokens.order_by("pk"):
        key = (token.project.slug, token.environment)
        found[key] = Scope(project=token.project, environment=token.environment)

    if not found:
        raise ScopeError(
            "no active Alertmanager ingest token matches — reconcile takes its"
            " project and environment from the token Alertmanager posts with"
        )
    if len(found) > 1:
        names = ", ".join(f"{slug}/{env or '-'}" for slug, env in sorted(found))
        raise ScopeError(
            f"several Alertmanager scopes match ({names})"
            " — narrow with --project and --environment"
        )
    return next(iter(found.values()))


class Reconciler:
    def __init__(
        self,
        scope: Scope,
        client: am_client.AlertmanagerClient,
        *,
        close_after: int = CLOSE_AFTER_MISSES,
    ) -> None:
        self.scope = scope
        self.client = client
        self.close_after = close_after
        self.misses: dict[int, int] = {}

    def cycle(self, now: datetime | None = None) -> ReconcileReport:
        try:
            report = self.run_once(now)
        except am_client.AlertmanagerError as error:
            CYCLES.labels(result="error").inc()
            log.warning("reconcile could not read alertmanager: %s", error)
            return ReconcileReport(error=str(error))
        CYCLES.labels(result="ok").inc()
        return report

    def run_once(self, now: datetime | None = None) -> ReconcileReport:
        if now is None:
            now = timezone.now()

        alerts = self.client.alerts()
        report = ReconcileReport(alerts=len(alerts))
        report.watchdog = self._watchdog(alerts, now)

        present = _by_fingerprint(alerts)
        episodes = self._open_episodes()
        report.open_episodes = len(episodes)

        closing = self._count_misses(episodes, present, report)
        opening = _to_open(present, {episode.am_fingerprint for episode in episodes})

        occurrences = [_firing_alert(alert) for alert in opening]
        occurrences.extend(_resolved_alert(episode, now) for episode in closing)
        if occurrences:
            self._publish(occurrences, now)

        report.opened = len(opening)
        report.closed = len(closing)
        ACTIONS.labels(action="opened").inc(report.opened)
        ACTIONS.labels(action="closed").inc(report.closed)
        ACTIONS.labels(action="missing").inc(report.missing)
        log.info(
            "reconcile: %s alerts, %s open episodes, %s opened, %s closed, %s missing",
            report.alerts,
            report.open_episodes,
            report.opened,
            report.closed,
            report.missing,
        )
        return report

    def _open_episodes(self) -> list[Episode]:
        return list(
            Episode.objects.filter(
                project=self.scope.project,
                environment=self.scope.environment,
                ends_at__isnull=True,
            ).order_by("pk")
        )

    def _count_misses(
        self,
        episodes: Sequence[Episode],
        present: Mapping[str, Mapping[str, Any]],
        report: ReconcileReport,
    ) -> list[Episode]:
        misses: dict[int, int] = {}
        closing: list[Episode] = []
        for episode in episodes:
            if episode.am_fingerprint in present:
                continue
            report.missing += 1
            seen = self.misses.get(episode.pk, 0) + 1
            if seen >= self.close_after:
                closing.append(episode)
                continue
            misses[episode.pk] = seen
        self.misses = misses
        return closing

    def _watchdog(self, alerts: Sequence[Mapping[str, Any]], now: datetime) -> bool:
        for alert in alerts:
            labels = alert.get("labels")
            if not isinstance(labels, Mapping):
                continue
            if labels.get(ALERTNAME_LABEL) != WATCHDOG_ALERTNAME:
                continue
            WATCHDOG_SEEN.set(now.timestamp())
            return True
        return False

    def _publish(
        self, alerts: Sequence[Mapping[str, Any]], now: datetime
    ) -> RawEnvelope:
        envelope = RawEnvelope.objects.create(
            project=self.scope.project,
            source=TokenSource.AM,
            environment=self.scope.environment,
            payload=_payload(alerts, self.client.base_url),
            received_at=now,
        )
        get_queue().publish(envelope.pk)
        envelope.refresh_from_db()
        if envelope.state == EnvelopeState.FAILED:
            log.error(
                "reconcile envelope %s did not apply: %s",
                envelope.pk,
                envelope.error,
            )
        return envelope


def _by_fingerprint(
    alerts: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    found: dict[str, Mapping[str, Any]] = {}
    for alert in alerts:
        fingerprint = str(alert.get("fingerprint", ""))
        if not fingerprint:
            log.warning("alertmanager returned an alert with no fingerprint")
            continue
        found[fingerprint] = alert
    return found


def _to_open(
    present: Mapping[str, Mapping[str, Any]], covered: set[str]
) -> list[Mapping[str, Any]]:
    opening: list[Mapping[str, Any]] = []
    for fingerprint, alert in sorted(present.items()):
        if fingerprint in covered:
            continue
        if not str(alert.get("startsAt", "")):
            log.warning(
                "alertmanager alert %s carries no startsAt — no episode to catch up",
                fingerprint,
            )
            continue
        opening.append(alert)
    return opening


def _payload(alerts: Sequence[Mapping[str, Any]], external_url: str) -> dict[str, Any]:
    status = STATUS_RESOLVED
    if any(alert["status"] == STATUS_FIRING for alert in alerts):
        status = STATUS_FIRING
    return {
        "version": PAYLOAD_VERSION,
        "groupKey": RECEIVER,
        "truncatedAlerts": 0,
        "status": status,
        "receiver": RECEIVER,
        "groupLabels": {},
        "commonLabels": {},
        "commonAnnotations": {},
        "externalURL": external_url,
        "alerts": [dict(alert) for alert in alerts],
    }


def _firing_alert(alert: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": STATUS_FIRING,
        "labels": _strings(alert.get("labels")),
        "annotations": _strings(alert.get("annotations")),
        "startsAt": str(alert.get("startsAt", "")),
        "generatorURL": str(alert.get("generatorURL", "")),
        "fingerprint": str(alert.get("fingerprint", "")),
    }


def _resolved_alert(episode: Episode, now: datetime) -> dict[str, Any]:
    return {
        "status": STATUS_RESOLVED,
        "labels": _strings(episode.labels),
        "annotations": {},
        "startsAt": episode.starts_at.isoformat(),
        "endsAt": now.isoformat(),
        "generatorURL": "",
        "fingerprint": episode.am_fingerprint,
    }


def _strings(raw: Any) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    return {str(key): str(value) for key, value in raw.items()}
