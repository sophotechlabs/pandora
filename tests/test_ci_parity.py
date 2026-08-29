import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
JUSTFILE = ROOT / "justfile"
WORKFLOW_DIR = ROOT / ".forgejo" / "workflows"
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


def local_gate():
    match = re.search(r"^ci:(.*)$", JUSTFILE.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, "no `ci:` recipe in the justfile"
    return match.group(1).split()


def workflow_text():
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(WORKFLOW_DIR.glob("*.yaml"))
    )


# gate mapping


def test_every_local_gate_is_mapped():
    """Should map every `just ci` step to the command CI must run."""
    result = [name for name in local_gate() if name not in RECIPE_COMMANDS]
    expected = []

    assert result == expected, (
        f"`just ci` gained {result} with no entry in RECIPE_COMMANDS — "
        "map it to the command CI must run, or to '' if it needs none"
    )


def test_the_mapping_has_no_stale_entries():
    """Should drop mappings for recipes `just ci` no longer runs."""
    result = [name for name in RECIPE_COMMANDS if name not in local_gate()]
    expected = []

    assert result == expected


@pytest.mark.parametrize("recipe", sorted(RECIPE_COMMANDS))
def test_mapped_gates_run_in_ci(recipe):
    """Should run every local gate in a Forgejo workflow too."""
    command = RECIPE_COMMANDS[recipe]
    if not command:
        pytest.skip(f"{recipe} has no CI counterpart by design")

    assert command in workflow_text(), (
        f"`just {recipe}` runs {command!r} locally but no workflow in "
        f"{WORKFLOW_DIR.name} does — CI would silently skip this gate"
    )


# the host toolchain


def test_the_host_tools_the_gate_needs_are_pinned():
    """Should let the gate run somewhere other than one laptop.

    A runner that resolves tools through mise finds nothing to activate without
    this file, and `just` fails to exec before any recipe starts.
    """
    text = MISEFILE.read_text(encoding="utf-8")

    result = [tool for tool in ("just", "uv") if f"\n{tool} = " not in text]
    expected = []

    assert result == expected


# both-backend coverage


def test_the_suite_runs_on_both_backends_locally():
    """Should run pytest twice in `just ci` — once per database backend."""
    result = [name for name in local_gate() if name.startswith("ci-test")]
    expected = ["ci-test", "ci-test-pg"]

    assert result == expected


def test_the_suite_runs_on_both_backends_in_ci():
    """Should run pytest twice in the workflow — once per database backend."""
    result = len(re.findall(r"uv run pytest", workflow_text()))
    expected = 2

    assert result == expected
