from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from pandora.core.models import Project
from pandora.people import audit
from pandora.releases import service
from pandora.releases.models import DeployState


class Command(BaseCommand):
    help = "Mark a deploy, optionally resolving everything currently open"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--project", required=True, help="project slug")
        parser.add_argument("--release", required=True, help="the release deployed")
        parser.add_argument("--deploy-id", required=True, help="stable CI deploy id")
        parser.add_argument("--dist", default="", help="build variant, if any")
        parser.add_argument("--environment", default="", help="where it went")
        parser.add_argument("--name", default="", help="a name for the deploy")
        parser.add_argument("--url", default="", help="a link to the CI run")
        parser.add_argument(
            "--state",
            default=DeployState.STARTED,
            choices=DeployState.values,
            help="started, succeeded, failed or timed_out",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        project = Project.objects.filter(slug=options["project"]).first()
        if project is None:
            raise CommandError(f"no project called {options['project']}")

        now = timezone.now()
        version = options["release"].strip()
        try:
            with transaction.atomic():
                release = service.ensure_release(
                    project,
                    version,
                    options["dist"],
                    now,
                )
                deploy, changed = service.transition_deploy(
                    project,
                    release,
                    identifier=options["deploy_id"],
                    environment=options["environment"],
                    state=options["state"],
                    at=now,
                    name=options["name"],
                    url=options["url"],
                )
        except service.DeployConflict as error:
            raise CommandError(str(error)) from error
        if not changed:
            self.stdout.write(f"deploy: unchanged {deploy}")
            return
        audit.record(
            "",
            audit.DEPLOY,
            str(release),
            {"environment": deploy.environment, "state": deploy.state},
            project_ids=[project.pk],
        )
        self.stdout.write(f"deploy: {deploy}")

        resolved = 0
        if deploy.state == DeployState.SUCCEEDED:
            resolved = service.resolve_on_deploy(
                project, release, deploy.environment, now
            )
        if resolved:
            self.stdout.write(f"deploy: resolved {resolved} open issue(s)")
