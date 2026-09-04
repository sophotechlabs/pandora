from __future__ import annotations

import hashlib
import json
import secrets
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from pandora.core.models import (
    DsnKey,
    IngestToken,
    Project,
    TokenScope,
    TokenSource,
)
from pandora.events.store import get_store
from pandora.events.types import Event, new_event_id
from pandora.ingest import demo as sdk_demo
from pandora.ingest.models import RawEnvelope
from pandora.issues.models import (
    ActivityKind,
    Episode,
    HourlyStat,
    Issue,
    IssueActivity,
    Level,
    SourceState,
    TagStat,
    TriageState,
)

WINDOW_MINUTES = 7 * 24 * 60
GENERATOR_URL = "https://prometheus.demo.invalid/graph"


@dataclass(frozen=True)
class IssueSpec:
    project: str
    alertname: str
    title: str
    culprit: str
    level: str
    triage_state: str
    shape: str
    open_tail: int
    grouping_labels: dict[str, str]
    instances: list[dict[str, str]]
    annotations: dict[str, str] = field(default_factory=dict)
    regression: bool = False


DEMO_PROJECTS = (
    ("demo-infra", "Demo infrastructure", "p-mk1"),
    ("demo-apps", "Demo applications", "p-mk2"),
)

SPECS = (
    IssueSpec(
        project="demo-infra",
        alertname="KubePodCrashLooping",
        title="KubePodCrashLooping: pod is restarting repeatedly",
        culprit="alertname=KubePodCrashLooping namespace=payments",
        level=Level.ERROR,
        triage_state=TriageState.NEW,
        shape="spike",
        open_tail=2,
        grouping_labels={"alertname": "KubePodCrashLooping", "namespace": "payments"},
        instances=[
            {
                "alertname": "KubePodCrashLooping",
                "namespace": "payments",
                "pod": "ledger-7d9f4c8b6d-hk2mp",
                "severity": "critical",
                "cluster": "p-mk1",
            },
            {
                "alertname": "KubePodCrashLooping",
                "namespace": "payments",
                "pod": "ledger-7d9f4c8b6d-x4rtq",
                "severity": "critical",
                "cluster": "p-mk1",
            },
        ],
        annotations={"summary": "Pod payments/ledger is in CrashLoopBackOff"},
    ),
    IssueSpec(
        project="demo-infra",
        alertname="TargetDown",
        title="TargetDown: scrape target unreachable",
        culprit="alertname=TargetDown namespace=monitoring",
        level=Level.WARNING,
        triage_state=TriageState.ACKNOWLEDGED,
        shape="steady",
        open_tail=1,
        grouping_labels={"alertname": "TargetDown", "namespace": "monitoring"},
        instances=[
            {
                "alertname": "TargetDown",
                "namespace": "monitoring",
                "job": "node-exporter",
                "severity": "warning",
                "cluster": "p-mk1",
            },
        ],
        annotations={"summary": "1 of 4 node-exporter targets is down"},
    ),
    IssueSpec(
        project="demo-infra",
        alertname="NodeFilesystemAlmostOutOfSpace",
        title="NodeFilesystemAlmostOutOfSpace: less than 10% free",
        culprit="alertname=NodeFilesystemAlmostOutOfSpace mountpoint=/var",
        level=Level.WARNING,
        triage_state=TriageState.NEW,
        shape="flap",
        open_tail=0,
        grouping_labels={
            "alertname": "NodeFilesystemAlmostOutOfSpace",
            "mountpoint": "/var",
        },
        instances=[
            {
                "alertname": "NodeFilesystemAlmostOutOfSpace",
                "mountpoint": "/var",
                "instance": "p-mk1-node-01:9100",
                "severity": "warning",
                "cluster": "p-mk1",
            },
        ],
        annotations={"summary": "Filesystem /var has 8.4% space left"},
    ),
    IssueSpec(
        project="demo-infra",
        alertname="CertManagerCertExpiringSoon",
        title="CertManagerCertExpiringSoon: certificate expires in under 21 days",
        culprit="alertname=CertManagerCertExpiringSoon namespace=traefik",
        level=Level.INFO,
        triage_state=TriageState.RESOLVED,
        shape="steady",
        open_tail=0,
        grouping_labels={
            "alertname": "CertManagerCertExpiringSoon",
            "namespace": "traefik",
        },
        instances=[
            {
                "alertname": "CertManagerCertExpiringSoon",
                "namespace": "traefik",
                "name": "wildcard-sopho-tech",
                "severity": "info",
                "cluster": "p-mk1",
            },
        ],
        annotations={"summary": "Certificate wildcard-sopho-tech expires in 19 days"},
    ),
    IssueSpec(
        project="demo-infra",
        alertname="Watchdog",
        title="Watchdog: alerting pipeline is alive",
        culprit="alertname=Watchdog",
        level=Level.INFO,
        triage_state=TriageState.IGNORED,
        shape="steady",
        open_tail=1,
        grouping_labels={"alertname": "Watchdog"},
        instances=[
            {
                "alertname": "Watchdog",
                "severity": "none",
                "cluster": "p-mk1",
            },
        ],
        annotations={"summary": "This alert always fires; its absence is the signal"},
    ),
    IssueSpec(
        project="demo-apps",
        alertname="KubeDeploymentReplicasMismatch",
        title="KubeDeploymentReplicasMismatch: desired replicas not available",
        culprit="alertname=KubeDeploymentReplicasMismatch namespace=storefront",
        level=Level.ERROR,
        triage_state=TriageState.NEW,
        shape="flap",
        open_tail=3,
        grouping_labels={
            "alertname": "KubeDeploymentReplicasMismatch",
            "namespace": "storefront",
        },
        instances=[
            {
                "alertname": "KubeDeploymentReplicasMismatch",
                "namespace": "storefront",
                "deployment": "web",
                "severity": "critical",
                "cluster": "p-mk2",
            },
            {
                "alertname": "KubeDeploymentReplicasMismatch",
                "namespace": "storefront",
                "deployment": "checkout",
                "severity": "critical",
                "cluster": "p-mk2",
            },
        ],
        annotations={"summary": "Deployment storefront/web has 1/3 replicas available"},
        regression=True,
    ),
)


def _digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _start_offsets(shape: str) -> list[int]:
    if shape == "steady":
        return list(range(240, WINDOW_MINUTES + 1, 240))
    if shape == "spike":
        base = list(range(360, WINDOW_MINUTES + 1, 360))
        burst = [1800 + minute for minute in range(12)]
        return sorted(base + burst)
    recent = list(range(60, 2161, 60))
    older = list(range(2400, WINDOW_MINUTES + 1, 480))
    return sorted(recent + older)


def _duration_minutes(shape: str) -> int:
    if shape == "flap":
        return 20
    if shape == "spike":
        return 45
    return 180


@dataclass
class Generated:
    episodes: list[Episode]
    hourly: Counter
    tags: Counter
    first_seen: datetime
    last_seen: datetime
    open_count: int


def _generate(spec: IssueSpec, now: datetime, environment: str) -> Generated:
    offsets = sorted(_start_offsets(spec.shape), reverse=True)
    duration = _duration_minutes(spec.shape)
    open_from = len(offsets) - spec.open_tail
    episodes: list[Episode] = []
    hourly: Counter = Counter()
    tags: Counter = Counter()

    for index, offset in enumerate(offsets):
        labels = spec.instances[index % len(spec.instances)]
        starts_at = now - timedelta(minutes=offset)
        if index >= open_from:
            ends_at = None
        else:
            ends_at = starts_at + timedelta(minutes=duration)
        if ends_at is None:
            last_delivery_at = now
        else:
            last_delivery_at = ends_at
        episodes.append(
            Episode(
                am_fingerprint=_digest(labels)[:16],
                labels=labels,
                environment=environment,
                starts_at=starts_at,
                ends_at=ends_at,
                delivery_count=1 + duration // 240,
                last_delivery_at=last_delivery_at,
            )
        )
        hourly[starts_at.replace(minute=0, second=0, microsecond=0)] += 1
        for key, value in labels.items():
            tags[(key, value)] += 1

    return Generated(
        episodes=episodes,
        hourly=hourly,
        tags=tags,
        first_seen=episodes[0].starts_at,
        last_seen=max(episode.last_delivery_at for episode in episodes),
        open_count=spec.open_tail,
    )


def _build_issue(spec: IssueSpec, project: Project, generated: Generated) -> Issue:
    fingerprint = [
        f"{key}:{value}" for key, value in sorted(spec.grouping_labels.items())
    ]
    if generated.open_count > 0:
        source_state = SourceState.FIRING
    else:
        source_state = SourceState.RESOLVED
    if spec.triage_state == TriageState.RESOLVED:
        last_resolved_at = generated.last_seen
    else:
        last_resolved_at = None
    return Issue(
        project=project,
        fingerprint_hash=_digest(fingerprint),
        fingerprint=fingerprint,
        grouping_labels=spec.grouping_labels,
        title=spec.title,
        culprit=spec.culprit,
        level=spec.level,
        environment=generated.episodes[0].environment,
        first_seen=generated.first_seen,
        last_seen=generated.last_seen,
        event_count=len(generated.episodes),
        open_episode_count=generated.open_count,
        source_state=source_state,
        triage_state=spec.triage_state,
        last_resolved_at=last_resolved_at,
    )


def _activities(
    spec: IssueSpec, issue: Issue, generated: Generated
) -> list[IssueActivity]:
    records = [
        IssueActivity(
            issue=issue,
            kind=ActivityKind.CREATED,
            actor="",
            at=generated.first_seen,
            data={"annotations": spec.annotations},
        )
    ]
    if spec.regression:
        records.append(
            IssueActivity(
                issue=issue,
                kind=ActivityKind.REGRESSION,
                actor="",
                at=generated.last_seen - timedelta(hours=2),
                data={"previous_triage_state": TriageState.RESOLVED},
            )
        )
    if spec.triage_state == TriageState.ACKNOWLEDGED:
        records.append(
            IssueActivity(
                issue=issue,
                kind=ActivityKind.ACKNOWLEDGED,
                actor="admin",
                at=generated.last_seen,
            )
        )
    if spec.triage_state == TriageState.RESOLVED:
        records.append(
            IssueActivity(
                issue=issue,
                kind=ActivityKind.RESOLVED,
                actor="admin",
                at=generated.last_seen,
            )
        )
    if spec.triage_state == TriageState.IGNORED:
        records.append(
            IssueActivity(
                issue=issue,
                kind=ActivityKind.IGNORED,
                actor="admin",
                at=generated.last_seen,
            )
        )
    return records


def _events(spec: IssueSpec, issue: Issue) -> list[Event]:
    message = spec.annotations.get("summary", spec.title)
    return [
        Event(
            id=new_event_id(),
            project_id=issue.project_id,
            timestamp=episode.starts_at,
            level=spec.level,
            message=message,
            issue_id=issue.pk,
            episode_id=str(episode.pk),
            fingerprint=list(issue.fingerprint),
            tags=dict(episode.labels),
            extra={"generatorURL": f"{GENERATOR_URL}?alertname={spec.alertname}"},
            environment=episode.environment,
        )
        for episode in issue.episodes.order_by("starts_at")
    ]


def _seed_projects(now: datetime) -> dict[str, Project]:
    projects = {}
    for slug, name, environment in DEMO_PROJECTS:
        project = Project.objects.create(slug=slug, name=name, created_at=now)
        alert_token = IngestToken.objects.create(
            project=project,
            name=f"{environment} alertmanager",
            token=f"demo-am-{slug}-{secrets.token_urlsafe(24)}",
            source=TokenSource.AM,
            environment=environment,
            created_at=now,
        )
        alert_token.set_scopes((TokenScope.INGEST,))
        read_token = IngestToken.objects.create(
            project=project,
            name=f"{environment} api reader",
            token=f"demo-read-{slug}-{secrets.token_urlsafe(24)}",
            source=TokenSource.SDK,
            environment=environment,
            created_at=now,
        )
        read_token.set_scopes((TokenScope.READ,))
        DsnKey.objects.create(
            project=project,
            public_key=_digest(slug)[:32],
            created_at=now,
        )
        projects[slug] = project
    return projects


DEMO_SLUGS = [slug for slug, _, _ in DEMO_PROJECTS]


def _real_data_exists() -> bool:
    if Project.objects.exclude(slug__in=DEMO_SLUGS).exists():
        return True
    return RawEnvelope.objects.exclude(project__slug__in=DEMO_SLUGS).exists()


class Command(BaseCommand):
    help = "Replace the demo projects with a deterministic set of issues and episodes"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="seed even though the database already holds real data",
        )

    def _guard(self, force: bool) -> None:
        if force:
            return
        if not _real_data_exists():
            return
        raise CommandError(
            "this database already holds real projects or ingested envelopes —"
            " seed_demo replaces the demo projects and is meant for a scratch"
            " database. Pass --force if you are certain."
        )

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        self._guard(options["force"])
        now = timezone.now().replace(minute=0, second=0, microsecond=0)
        Project.objects.filter(slug__in=[slug for slug, _, _ in DEMO_PROJECTS]).delete()
        projects = _seed_projects(now)
        environments = {slug: env for slug, _, env in DEMO_PROJECTS}
        store = get_store()
        store.ensure_partitions()

        issue_count = 0
        episode_count = 0
        event_count = 0
        for spec in SPECS:
            project = projects[spec.project]
            generated = _generate(spec, now, environments[spec.project])
            issue = _build_issue(spec, project, generated)
            issue.save()
            for episode in generated.episodes:
                episode.project = project
                episode.issue = issue
            Episode.objects.bulk_create(generated.episodes)
            HourlyStat.objects.bulk_create(
                HourlyStat(issue=issue, hour=hour, count=count)
                for hour, count in sorted(generated.hourly.items())
            )
            TagStat.objects.bulk_create(
                TagStat(issue=issue, key=key, value=value, count=count)
                for (key, value), count in sorted(generated.tags.items())
            )
            IssueActivity.objects.bulk_create(_activities(spec, issue, generated))
            events = _events(spec, issue)
            store.insert(events)
            issue_count += 1
            episode_count += len(generated.episodes)
            event_count += len(events)

        sdk_project = projects[DEMO_PROJECTS[1][0]]
        sdk_count = sdk_demo.seed(
            sdk_project,
            environments[sdk_project.slug],
            now,
        )

        self.stdout.write(
            f"seed_demo: {len(projects)} projects, {issue_count} issues, "
            f"{episode_count} episodes, {event_count} events, "
            f"{sdk_count} sdk events"
        )
