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
    management.call_command("deploy", stdout=out, **options)
    return out.getvalue()


def test_a_deploy_is_recorded(project):
    """Should be the optional CI marker, beside the revisions processes report."""
    run(project="infrastructure", release="1.2.3", environment="p-mk1")

    deploy = release_models.Deploy.objects.get()
    result = (deploy.release.version, deploy.environment, deploy.state)
    expected = ("1.2.3", "p-mk1", release_models.DeployState.SUCCEEDED)

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


def test_a_started_deploy_has_no_finish_time(project):
    """Should model the state Rollbar models, not a single instant."""
    run(project="infrastructure", release="1.2.3", state="started")

    result = release_models.Deploy.objects.get().finished_at

    assert result is None


def test_the_deploy_is_recorded_in_the_history(project):
    """Should show on /history/ like everything else that changed data."""
    run(project="infrastructure", release="1.2.3")

    result = AuditEntry.objects.filter(action="release.deploy").count()
    expected = 1

    assert result == expected


def test_resolve_on_deploy_is_off_by_default(project, issue):
    """Should never wipe the board unless a project asked for it."""
    run(project="infrastructure", release="1.2.3")

    result = issue_models.Issue.objects.get(pk=issue.pk).triage_state
    expected = issue_models.TriageState.NEW

    assert result == expected


def test_resolve_on_deploy_clears_what_is_open(project, issue):
    """Should be the opinionated option — wipe it, and re-notify on what returns."""
    project.resolve_on_deploy = True
    project.save(update_fields=["resolve_on_deploy"])

    run(project="infrastructure", release="1.2.3")

    result = issue_models.Issue.objects.get(pk=issue.pk).triage_state
    expected = issue_models.TriageState.RESOLVED

    assert result == expected


def test_resolve_on_deploy_records_the_release_boundary(project, issue):
    """Should mean the resolve holds until something newer than this arrives."""
    project.resolve_on_deploy = True
    project.save(update_fields=["resolve_on_deploy"])

    run(project="infrastructure", release="1.2.3")

    result = release_models.Resolution.objects.get().release.version
    expected = "1.2.3"

    assert result == expected


def test_resolve_on_deploy_says_how_many_it_closed(project, issue):
    """Should report the size of what it just did."""
    project.resolve_on_deploy = True
    project.save(update_fields=["resolve_on_deploy"])

    output = run(project="infrastructure", release="1.2.3")

    assert "resolved 1 open issue" in output


def test_resolve_on_deploy_is_scoped_to_the_environment(project, issue):
    """Should not close production because staging was deployed."""
    project.resolve_on_deploy = True
    project.save(update_fields=["resolve_on_deploy"])

    run(project="infrastructure", release="1.2.3", environment="staging")

    result = issue_models.Issue.objects.get(pk=issue.pk).triage_state
    expected = issue_models.TriageState.NEW

    assert result == expected
