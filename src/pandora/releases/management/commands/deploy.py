from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from pandora.core.models import Project
from pandora.people import audit
from pandora.releases import service
from pandora.releases.models import Deploy, DeployState, Release
from pandora.releases.versions import is_parsed, sort_key


class Command(BaseCommand):
    help = "Mark a deploy, optionally resolving everything currently open"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--project", required=True, help="project slug")
        parser.add_argument("--release", required=True, help="the release deployed")
        parser.add_argument("--dist", default="", help="build variant, if any")
        parser.add_argument("--environment", default="", help="where it went")
        parser.add_argument("--name", default="", help="a name for the deploy")
        parser.add_argument("--url", default="", help="a link to the CI run")
        parser.add_argument(
            "--state",
            default=DeployState.SUCCEEDED,
            choices=DeployState.values,
            help="started, succeeded, failed or timed_out",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        project = Project.objects.filter(slug=options["project"]).first()
        if project is None:
            raise CommandError(f"no project called {options['project']}")

        now = timezone.now()
        version = options["release"].strip()
        release, _ = _release(project, version, options["dist"].strip(), now)
        finished = None
        if options["state"] != DeployState.STARTED:
            finished = now
        deploy = Deploy.objects.create(
            release=release,
            environment=options["environment"].strip(),
            state=options["state"],
            started_at=now,
            finished_at=finished,
            name=options["name"][:200],
            url=options["url"][:500],
        )
        audit.record(
            "",
            audit.DEPLOY,
            str(release),
            {"environment": deploy.environment, "state": deploy.state},
        )
        self.stdout.write(f"deploy: {deploy}")

        resolved = service.resolve_on_deploy(project, release, deploy.environment, now)
        if resolved:
            self.stdout.write(f"deploy: resolved {resolved} open issue(s)")


def _release(project: Project, version: str, dist: str, now: Any) -> tuple[Any, bool]:
    return Release.objects.get_or_create(
        project=project,
        version=version[:250],
        dist=dist[:100],
        defaults={
            "sort_key": sort_key(version),
            "parsed": is_parsed(version),
            "first_seen": now,
            "last_seen": now,
        },
    )
