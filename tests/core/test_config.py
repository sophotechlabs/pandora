import io
import textwrap

import pytest
from django.core import management
from django.core.management.base import CommandError

from pandora.core import config, models
from pandora.issues import models as issue_models

pytestmark = pytest.mark.django_db


@pytest.fixture
def write(tmp_path):
    def _write(body):
        path = tmp_path / "pandora.yaml"
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        return str(path)

    return _write


def run(path, *args):
    out = io.StringIO()
    management.call_command("apply_config", "--path", path, *args, stdout=out)
    return out.getvalue()


BASE = """
    projects:
      - slug: infrastructure
        name: Infrastructure
    """


# reading the file


def test_an_empty_file_is_not_an_error(write):
    """Should let an operator start from nothing without special-casing the first run."""
    result = config.load(write(""))
    expected = {}

    assert result == expected


def test_a_file_that_is_not_a_mapping_is_rejected(write):
    """Should name the shape rather than failing somewhere deeper with a type error."""
    with pytest.raises(config.ConfigError, match="mapping of sections"):
        config.load(write("- one\n- two\n"))


def test_an_unknown_section_is_rejected(write):
    """Should catch a typo in a section name — silently ignoring it means the config lies."""
    with pytest.raises(config.ConfigError, match="grouping_rulez"):
        config.load(write("grouping_rulez: []\n"))


def test_a_section_that_is_not_a_list_is_rejected(write):
    """Should refuse a mapping where entries belong."""
    with pytest.raises(config.ConfigError, match="projects must be a list"):
        config.apply(config.load(write("projects:\n  slug: one\n")))


def test_an_entry_that_is_not_a_mapping_is_rejected(write):
    """Should refuse a bare string where a record belongs."""
    with pytest.raises(config.ConfigError, match="must be a mapping"):
        config.apply(config.load(write("projects:\n  - infrastructure\n")))


def test_a_missing_file_reports_the_path(write, tmp_path):
    """Should say which file it could not read."""
    with pytest.raises(CommandError, match="cannot read"):
        run(str(tmp_path / "absent.yaml"))


def test_the_command_needs_a_path(settings):
    """Should not silently do nothing when neither the flag nor the setting is set."""
    settings.PANDORA_CONFIG = ""
    with pytest.raises(CommandError, match="pass --path"):
        management.call_command("apply_config")


def test_the_setting_supplies_the_path(write, settings):
    """Should let a deployment point at a mounted file once rather than on every call."""
    settings.PANDORA_CONFIG = write(BASE)

    management.call_command("apply_config")

    result = models.Project.objects.filter(slug="infrastructure").exists()

    assert result is True


# projects


def test_a_project_is_created(write):
    """Should stand up the object every other section refers to."""
    run(write(BASE))

    result = models.Project.objects.get(slug="infrastructure").name
    expected = "Infrastructure"

    assert result == expected


def test_a_renamed_project_is_updated_not_duplicated(write):
    """Should treat the slug as the identity, because everything else keys on it."""
    run(write(BASE))
    run(write(BASE.replace("name: Infrastructure", "name: Platform")))

    result = [(row.slug, row.name) for row in models.Project.objects.all()]
    expected = [("infrastructure", "Platform")]

    assert result == expected


def test_applying_twice_changes_nothing_the_second_time(write):
    """Should be safe to run on every boot, which is the point of a declarative file."""
    path = write(BASE)
    run(path)

    output = run(path)

    result = "0 created, 0 updated, 0 deactivated" in output

    assert result is True


# secrets by reference


def test_a_token_reads_its_value_from_the_environment(write, monkeypatch):
    """Should keep the file committable — the secret lives where secrets live."""
    monkeypatch.setenv("PANDORA_TOKEN_AM", "s3cret-token-value")
    run(
        write(
            BASE
            + """
    tokens:
      - name: alertmanager
        project: infrastructure
        token_env: PANDORA_TOKEN_AM
        environment: p-mk1
    """
        )
    )

    token = models.IngestToken.objects.get(name="alertmanager")

    result = (token.token, token.environment, token.active)
    expected = ("s3cret-token-value", "p-mk1", True)

    assert result == expected


def test_an_unset_variable_is_an_error(write):
    """Should fail loudly rather than writing an empty token nothing can authenticate with."""
    body = (
        BASE
        + """
    tokens:
      - name: alertmanager
        project: infrastructure
        token_env: PANDORA_TOKEN_ABSENT
    """
    )
    with pytest.raises(CommandError, match="PANDORA_TOKEN_ABSENT is empty or unset"):
        run(write(body))


def test_setting_both_the_literal_and_the_reference_is_an_error(write):
    """Should refuse an ambiguous entry rather than picking one."""
    body = (
        BASE
        + """
    tokens:
      - name: alertmanager
        project: infrastructure
        token: literal
        token_env: PANDORA_TOKEN_AM
    """
    )
    with pytest.raises(CommandError, match="sets both token and token_env"):
        run(write(body))


def test_a_token_with_neither_is_an_error(write):
    """Should say what is missing instead of creating a token with an empty value."""
    body = (
        BASE
        + """
    tokens:
      - name: alertmanager
        project: infrastructure
    """
    )
    with pytest.raises(CommandError, match="needs token or token_env"):
        run(write(body))


def test_a_token_for_an_unknown_project_is_an_error(write):
    """Should catch a typo in a project slug at apply time, not at ingest time."""
    body = (
        BASE
        + """
    tokens:
      - name: alertmanager
        project: typo
        token: abc
    """
    )
    with pytest.raises(CommandError, match="unknown project 'typo'"):
        run(write(body))


# reconciliation, which is the half a create-only loader misses


def test_a_token_dropped_from_the_file_is_deactivated(write):
    """Should stop a removed credential working — a create-only loader leaves it live forever."""
    body = (
        BASE
        + """
    tokens:
      - name: alertmanager
        project: infrastructure
        token: abc123
    """
    )
    run(write(body))

    run(write(BASE))

    result = models.IngestToken.objects.get(name="alertmanager").active
    expected = False

    assert result == expected


def test_a_deactivated_row_is_not_deleted(write):
    """Should keep the history an issue's episodes point at rather than cascading them away."""
    body = (
        BASE
        + """
    tokens:
      - name: alertmanager
        project: infrastructure
        token: abc123
    """
    )
    run(write(body))
    run(write(BASE))

    result = models.IngestToken.objects.count()
    expected = 1

    assert result == expected


def test_a_dropped_service_link_is_deactivated(write):
    """Should reconcile every section, not only the ones with secrets."""
    body = (
        BASE
        + """
    service_links:
      - name: Grafana
        url_template: https://grafana.test/?ns={namespace}
    """
    )
    run(write(body))

    run(write(BASE))

    result = models.ServiceLink.objects.get(name="Grafana").active

    assert result is False


def test_the_seeded_grouping_rule_is_deactivated_when_the_file_omits_it(write):
    """Should mean what it says — a file with no rules is a request for no rules."""
    run(write(BASE))

    result = issue_models.GroupingRule.objects.filter(active=True).count()
    expected = 0

    assert result == expected


# the remaining sections


def test_a_dsn_key_is_created(write):
    """Should let an SDK's DSN be provisioned from the file like everything else."""
    run(
        write(
            BASE
            + """
    dsn_keys:
      - project: infrastructure
        public_key: 0123456789abcdef
    """
        )
    )

    key = models.DsnKey.objects.get()

    result = (key.public_key, key.project.slug, key.active)
    expected = ("0123456789abcdef", "infrastructure", True)

    assert result == expected


def test_a_grouping_rule_is_created_with_its_labels(write):
    """Should carry the denylist, which is the part that stops one label minting an issue per pod."""
    run(
        write(
            BASE
            + """
    grouping_rules:
      - priority: 50
        mode: denylist
        labels: [pod, instance]
        alertname_regex: "Kube.*"
    """
        )
    )

    rule = issue_models.GroupingRule.objects.get(priority=50)

    result = (rule.mode, rule.labels, rule.alertname_regex, rule.active)
    expected = ("denylist", ["pod", "instance"], "Kube.*", True)

    assert result == expected


def test_a_service_link_can_be_scoped_to_a_project(write):
    """Should let one project carry links the others do not."""
    run(
        write(
            BASE
            + """
    service_links:
      - name: Grafana
        project: infrastructure
        url_template: https://grafana.test/?ns={namespace}
        ordering: 10
    """
        )
    )

    link = models.ServiceLink.objects.get(name="Grafana")

    result = (link.project.slug, link.ordering)
    expected = ("infrastructure", 10)

    assert result == expected


# the dry run


def test_a_dry_run_changes_nothing(write):
    """Should let an operator see the diff before an apply touches a live install."""
    run(write(BASE), "--dry-run")

    result = models.Project.objects.count()
    expected = 0

    assert result == expected


def test_a_dry_run_reports_what_it_would_do(write):
    """Should name the changes, or there is nothing to review."""
    output = run(write(BASE), "--dry-run")

    result = ("create project infrastructure" in output, "rolled back" in output)
    expected = (True, True)

    assert result == expected


def test_a_failed_apply_leaves_nothing_behind(write):
    """Should be all or nothing — a half-applied config is worse than none."""
    body = (
        BASE
        + """
    tokens:
      - name: alertmanager
        project: typo
        token: abc
    """
    )
    with pytest.raises(CommandError):
        run(write(body))

    result = models.Project.objects.count()
    expected = 0

    assert result == expected


def test_a_project_without_a_name_is_rejected(write):
    """Should name the missing field rather than creating a project called nothing."""
    with pytest.raises(CommandError, match="projects entry is missing name"):
        run(write("projects:\n  - slug: infrastructure\n"))


def test_an_unknown_section_reaches_the_operator_as_a_command_error(write):
    """Should surface a file-level mistake as a clean message, not a traceback."""
    with pytest.raises(CommandError, match="unknown section"):
        run(write("nonsense: []\n"))


def test_a_grouping_rule_can_be_scoped_to_a_project(write):
    """Should let one project's alerts group differently from another's."""
    run(
        write(
            BASE
            + """
    grouping_rules:
      - priority: 50
        project: infrastructure
        labels: [pod]
    """
        )
    )

    rule = issue_models.GroupingRule.objects.get(priority=50)

    result = rule.project.slug
    expected = "infrastructure"

    assert result == expected


def test_a_dsn_key_reads_its_value_from_the_environment(write, monkeypatch):
    """Should keep the public key out of the file, since it is what a DSN embeds."""
    monkeypatch.setenv("PANDORA_DSN_INFRA", "fedcba9876543210")
    run(
        write(
            BASE
            + """
    dsn_keys:
      - project: infrastructure
        public_key_env: PANDORA_DSN_INFRA
    """
        )
    )

    result = models.DsnKey.objects.get().public_key
    expected = "fedcba9876543210"

    assert result == expected
