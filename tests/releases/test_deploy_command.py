import io

import pytest
from django.core import management
from django.core.management.base import CommandError

from pandora.issues import models as issue_models
from pandora.people.models import AuditEntry
from pandora.releases import models as release_models

pytestmark = pytest.mark.django_db


def run(**options):
    out = io.StringIO()
    options.setdefault("deploy_id", "pipeline-42")
    management.call_command("deploy", stdout=out, **options)
    return out.getvalue()


def complete(**options):
    run(state="started", **options)
    return run(state="succeeded", **options)


def test_a_deploy_is_recorded(project):
    """Should be the optional CI marker, beside the revisions processes report."""
    run(project="infrastructure", release="1.2.3", environment="p-mk1")

    deploy = release_models.Deploy.objects.get()
    result = (deploy.release.version, deploy.environment, deploy.state)
    expected = ("1.2.3", "p-mk1", release_models.DeployState.STARTED)

    assert result == expected


def test_the_release_is_created_if_nothing_reported_it_yet(project):
    """Should let CI mark a deploy before the first event arrives."""
    run(project="infrastructure", release="1.2.3")

    result = release_models.Release.objects.get().version
    expected = "1.2.3"

    assert result == expected


def test_an_unknown_project_is_refused(project):
    """Should fail on the argument rather than create a project by accident."""
    with pytest.raises(CommandError, match="no project called"):
        run(project="nothing", release="1.2.3")


def test_a_deploy_identifier_is_required(project):
    with pytest.raises(CommandError, match="deploy-id"):
        management.call_command(
            "deploy",
            project="infrastructure",
            release="1.2.3",
        )


def test_a_deploy_identifier_is_not_silently_truncated(project):
    with pytest.raises(CommandError, match="identifier is too long"):
        run(
            project="infrastructure",
            release="1.2.3",
            deploy_id="x" * 129,
        )

    assert release_models.Release.objects.exists() is False


def test_a_started_deploy_has_no_finish_time(project):
    """Should model the state Rollbar models, not a single instant."""
    run(project="infrastructure", release="1.2.3", state="started")

    result = release_models.Deploy.objects.get().finished_at

    assert result is None


def test_completion_updates_the_started_deploy(project):
    run(project="infrastructure", release="1.2.3", state="started")

    run(project="infrastructure", release="1.2.3", state="succeeded")

    deploy = release_models.Deploy.objects.get()
    assert deploy.state == release_models.DeployState.SUCCEEDED
    assert deploy.finished_at is not None


def test_a_started_retry_is_idempotent(project):
    run(project="infrastructure", release="1.2.3", state="started")

    output = run(project="infrastructure", release="1.2.3", state="started")

    assert release_models.Deploy.objects.count() == 1
    assert "unchanged" in output


def test_completion_without_a_start_is_refused(project):
    with pytest.raises(CommandError, match="must be started"):
        run(project="infrastructure", release="1.2.3", state="failed")

    assert release_models.Release.objects.exists() is False


def test_a_deploy_identifier_cannot_change_release(project):
    run(project="infrastructure", release="1.2.3", state="started")

    with pytest.raises(CommandError, match="another release"):
        run(project="infrastructure", release="1.2.4", state="started")


def test_a_late_success_replaces_an_inferred_timeout(project):
    run(project="infrastructure", release="1.2.3", state="started")
    run(project="infrastructure", release="1.2.3", state="timed_out")

    run(project="infrastructure", release="1.2.3", state="succeeded")

    assert release_models.Deploy.objects.get().state == "succeeded"


def test_conflicting_terminal_states_are_refused(project):
    complete(project="infrastructure", release="1.2.3")

    with pytest.raises(CommandError, match="already succeeded"):
        run(project="infrastructure", release="1.2.3", state="failed")


def test_the_deploy_is_recorded_in_the_history(project):
    """Should show on /history/ like everything else that changed data."""
    run(project="infrastructure", release="1.2.3")

    result = AuditEntry.objects.filter(action="release.deploy").count()
    expected = 1

    assert result == expected


def test_resolve_on_deploy_is_off_by_default(project, issue):
    """Should never wipe the board unless a project asked for it."""
    complete(project="infrastructure", release="1.2.3")

    result = issue_models.Issue.objects.get(pk=issue.pk).triage_state
    expected = issue_models.TriageState.NEW

    assert result == expected


def test_resolve_on_deploy_clears_what_is_open(project, issue):
    """Should be the opinionated option — wipe it, and re-notify on what returns."""
    project.resolve_on_deploy = True
    project.save(update_fields=["resolve_on_deploy"])

    complete(project="infrastructure", release="1.2.3")

    result = issue_models.Issue.objects.get(pk=issue.pk).triage_state
    expected = issue_models.TriageState.RESOLVED

    assert result == expected


def test_resolve_on_deploy_records_the_release_boundary(project, issue):
    """Should mean the resolve holds until something newer than this arrives."""
    project.resolve_on_deploy = True
    project.save(update_fields=["resolve_on_deploy"])

    complete(project="infrastructure", release="1.2.3")

    result = release_models.Resolution.objects.get().release.version
    expected = "1.2.3"

    assert result == expected


def test_resolve_on_deploy_says_how_many_it_closed(project, issue):
    """Should report the size of what it just did."""
    project.resolve_on_deploy = True
    project.save(update_fields=["resolve_on_deploy"])

    output = complete(project="infrastructure", release="1.2.3")

    assert "resolved 1 open issue" in output


def test_resolve_on_deploy_is_scoped_to_the_environment(project, issue):
    """Should not close production because staging was deployed."""
    project.resolve_on_deploy = True
    project.save(update_fields=["resolve_on_deploy"])

    complete(project="infrastructure", release="1.2.3", environment="staging")

    result = issue_models.Issue.objects.get(pk=issue.pk).triage_state
    expected = issue_models.TriageState.NEW

    assert result == expected
