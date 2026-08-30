import json
import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
JUSTFILE = ROOT / "justfile"
FORGEJO = ROOT / ".forgejo" / "workflows"
GITHUB = ROOT / ".github" / "workflows"
MISEFILE = ROOT / "mise.toml"

RECIPE_COMMANDS = {
    "ci-image": "",
    "ci-lint": "ruff check",
    "ci-format-check": "ruff format --check",
    "ci-typecheck": "mypy",
    "ci-djlint": "djlint",
    "ci-migration-lint": "lintmigrations",
    "ci-security": "pip-audit",
    "ci-docker-lint": "hadolint",
    "ci-test": "pytest",
    "ci-test-pg": "TEST_DATABASE_URL",
}

HOST_TOOLS = (
    "just",
    "uv",
    "go",
    "node",
    "actionlint",
    "editorconfig-checker",
    "gitleaks",
    "grype",
    "hadolint",
    "helm",
    "kubeconform",
    "osv-scanner",
    "shellcheck",
    "syft",
    "trivy",
    "typos",
    "yamllint",
    "zizmor",
)


def justfile_text():
    return JUSTFILE.read_text(encoding="utf-8")


def gate(name):
    match = re.search(rf"^{name}:(.*)$", justfile_text(), re.MULTILINE)
    assert match, f"no `{name}:` recipe in the justfile"
    return match.group(1).split()


def workflow_text(directory):
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(directory.glob("*.yaml"))
    )


def workflows(directory):
    return [
        yaml.safe_load(path.read_text()) for path in sorted(directory.glob("*.yaml"))
    ]


# gate mapping


def test_every_local_gate_is_mapped():
    """Should map every `just ci` step to the command CI must run."""
    result = [name for name in gate("ci") if name not in RECIPE_COMMANDS]
    expected = []

    assert result == expected, (
        f"`just ci` gained {result} with no entry in RECIPE_COMMANDS — "
        "map it to the command CI must run, or to '' if it needs none"
    )


def test_the_mapping_has_no_stale_entries():
    """Should drop mappings for recipes `just ci` no longer runs."""
    result = [name for name in RECIPE_COMMANDS if name not in gate("ci")]
    expected = []

    assert result == expected


@pytest.mark.parametrize("recipe", sorted(RECIPE_COMMANDS))
def test_mapped_gates_run_in_ci(recipe):
    """Should run every local gate in a Forgejo workflow too."""
    command = RECIPE_COMMANDS[recipe]
    if not command:
        pytest.skip(f"{recipe} has no CI counterpart by design")

    assert command in workflow_text(FORGEJO), (
        f"`just {recipe}` runs {command!r} locally but no workflow in "
        f"{FORGEJO.name} does — CI would silently skip this gate"
    )


@pytest.mark.parametrize("recipe", sorted(gate("gh")))
def test_the_host_gate_runs_on_github(recipe):
    """Should run every `just gh` step on GitHub — two gates, one set of checks."""
    assert f"just {recipe}" in workflow_text(GITHUB), (
        f"`just gh` runs {recipe} but no workflow in {GITHUB.parent.name} does"
    )


# the host toolchain


def test_the_host_tools_the_gate_needs_are_pinned():
    """Should let the gate run somewhere other than one laptop.

    A runner that resolves tools through mise finds nothing to activate without
    this file, and `just` fails to exec before any recipe starts.
    """
    text = MISEFILE.read_text(encoding="utf-8")

    result = [tool for tool in HOST_TOOLS if f"\n{tool} = " not in text]
    expected = []

    assert result == expected


def test_the_pinned_tools_have_exact_versions():
    """Should pin what CI installs — a floating tool turns a green gate red overnight."""
    text = MISEFILE.read_text(encoding="utf-8")

    result = re.findall(r'^\S+ = "(latest|\*)"$', text, re.MULTILINE)
    expected = []

    assert result == expected


# both-backend coverage


def test_the_suite_runs_on_both_backends_locally():
    """Should run pytest twice in `just ci` — once per database backend."""
    result = [name for name in gate("ci") if name.startswith("ci-test")]
    expected = ["ci-test", "ci-test-pg"]

    assert result == expected


def test_the_suite_runs_on_both_backends_in_forgejo():
    """Should run pytest twice in the workflow — once per database backend."""
    result = len(re.findall(r"uv run pytest", workflow_text(FORGEJO)))
    expected = 2

    assert result == expected


def test_the_suite_runs_on_both_backends_on_github():
    """Should run pytest twice on GitHub too, one job per backend."""
    result = sorted(name for name in gate("gh") if name in ("gh-test", "gh-test-pg"))
    expected = ["gh-test", "gh-test-pg"]

    assert result == expected


# how the workflows are written


def test_every_github_workflow_declares_least_privilege():
    """Should default to a read-only token, so a job that needs more says so."""
    result = [
        document.get("name")
        for document in workflows(GITHUB)
        if document.get("permissions") != {"contents": "read"}
    ]
    expected = []

    assert result == expected


def test_every_github_action_is_pinned_to_a_commit():
    """Should pin by digest — a moved tag is someone else's code running as us."""
    floating = re.findall(r"uses: (\S+@(?!\b[0-9a-f]{40}\b)\S+)", workflow_text(GITHUB))

    result = sorted(set(floating))
    expected = []

    assert result == expected


def test_no_github_checkout_leaves_credentials_behind():
    """Should not leave a usable token in .git/config for any later step to find."""
    text = workflow_text(GITHUB)

    result = text.count("uses: actions/checkout@") - text.count(
        "persist-credentials: false"
    )
    expected = 0

    assert result == expected


# release-please


def test_the_manifest_matches_the_package_version():
    """Should start release-please from where the package actually is."""
    manifest = json.loads((ROOT / ".release-please-manifest.json").read_text())
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    result = f'version = "{manifest["."]}"' in pyproject

    assert result is True


def test_release_please_bumps_the_chart_as_well():
    """Should not let the chart drift a release behind the app it deploys."""
    config = json.loads((ROOT / "release-please-config.json").read_text())
    extra = config["packages"]["."]["extra-files"]

    result = [entry["path"] for entry in extra]
    expected = ["deploy/helm/pandora/Chart.yaml"]

    assert result == expected


def test_the_chart_carries_the_markers_the_generic_updater_needs():
    """Should annotate both versions — an unmarked line is silently left behind."""
    chart = (ROOT / "deploy" / "helm" / "pandora" / "Chart.yaml").read_text()

    result = chart.count("x-release-please-version")
    expected = 2

    assert result == expected


def test_the_release_type_matches_the_package():
    """Should let release-please find the version it has to write."""
    config = json.loads((ROOT / "release-please-config.json").read_text())

    result = config["packages"]["."]["release-type"]
    expected = "python"

    assert result == expected
