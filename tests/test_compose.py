import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = ROOT / "docker-compose.yml"
LOCAL = ROOT / "docker-compose.local.yml"
E2E = ROOT / "docker-compose.e2e.yml"
JUSTFILE = ROOT / "justfile"

CREATING_RECIPES = ("up", "up-nobuild", "up-fg", "bootstrap")
PUBLISHED = re.compile(r'^\s*-\s*"?\d[\d.]*:\d+:\d+', re.MULTILINE)


def justfile_text():
    return JUSTFILE.read_text(encoding="utf-8")


def recipe_body(name):
    text = justfile_text()
    match = re.search(
        rf"^{re.escape(name)}(?:\s+[^:\n]+)?:.*?$(.*?)(?=^\S|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"no `{name}` recipe in the justfile"
    return match.group(1)


# the base file publishes nothing


def test_the_base_compose_publishes_no_host_port():
    """Should keep every CI recipe free of a host binding — a fixed port is a collision."""
    result = PUBLISHED.findall(BASE.read_text(encoding="utf-8"))
    expected = []

    assert result == expected, (
        f"docker-compose.yml publishes {result} — two checkouts running "
        "`just ci-test-pg` at once would fight over it. Publish from "
        "docker-compose.local.yml instead."
    )


def test_the_base_compose_declares_no_ports_at_all():
    """Should leave the whole key out, so a later service cannot inherit the habit."""
    result = "ports:" in BASE.read_text(encoding="utf-8")

    assert result is False


# the override publishes without pinning


@pytest.mark.parametrize("service", ["db", "web"])
def test_the_override_publishes_the_service(service):
    """Should let a human reach both containers from the host."""
    text = LOCAL.read_text(encoding="utf-8")

    result = re.search(rf"^  {service}:$", text, re.MULTILINE) is not None

    assert result is True


def test_the_override_pins_no_port_number():
    """Should default to an empty host port so docker picks a free one per checkout."""
    result = PUBLISHED.findall(LOCAL.read_text(encoding="utf-8"))
    expected = []

    assert result == expected


@pytest.mark.parametrize("variable", ["PANDORA_WEB_PORT", "PANDORA_DB_PORT"])
def test_the_override_reads_a_port_variable(variable):
    """Should let one checkout pin a predictable address without touching the file."""
    result = f"${{{variable}:-}}" in LOCAL.read_text(encoding="utf-8")

    assert result is True


@pytest.mark.parametrize("variable", ["PANDORA_WEB_PORT", "PANDORA_DB_PORT"])
def test_the_port_variables_are_documented(variable):
    """Should name them where someone copying .env.example will see them."""
    result = variable in (ROOT / ".env.example").read_text(encoding="utf-8")

    assert result is True


# the justfile wires the two files together


@pytest.mark.parametrize("recipe", CREATING_RECIPES)
def test_container_creating_recipes_use_the_override(recipe):
    """Should publish for a human — `up` without it leaves no way into the UI."""
    result = "compose_local" in recipe_body(recipe)

    assert result is True, (
        f"`just {recipe}` creates containers with plain docker compose, so the "
        "stack would come up with no published port at all"
    )


def test_the_ci_recipes_do_not_use_the_override():
    """Should keep the gate unable to bind a host port, whatever the override says."""
    text = justfile_text()
    ci_lines = [
        line
        for line in text.splitlines()
        if line.strip().startswith("{{ci_compose_run")
    ]

    result = [line for line in ci_lines if "compose_local" in line]
    expected = []

    assert result == expected


def test_the_scan_image_tag_is_not_hardcoded():
    """Should tag per checkout — a shared tag means scanning someone else's image."""
    text = justfile_text()

    result = "pandora-web:ci" in text

    assert result is False


def test_the_scan_image_tag_defaults_to_the_checkout():
    """Should give each worktree its own tag with no configuration."""
    result = (
        'image_tag := env_var_or_default("PANDORA_IMAGE_TAG", file_name(justfile_directory()))'
        in justfile_text()
    )

    assert result is True


def test_the_base_compose_mounts_no_host_directory():
    """Should run CI against the code baked into the image — a bind mount writes as the container's uid, which is not the uid that owns a checkout on a build box."""
    result = "- .:/app" in BASE.read_text(encoding="utf-8")

    assert result is False


def test_the_override_mounts_the_source_for_a_live_reload():
    """Should keep `just up` editing without a rebuild, which is the reason the mount exists."""
    result = "- .:/app" in LOCAL.read_text(encoding="utf-8")

    assert result is True


def test_the_image_builds_on_the_host_network():
    """Should let apt resolve on a box whose forward chain drops docker's default bridge — the same reason the forgejo image workflow passes --network host."""
    result = "network: host" in BASE.read_text(encoding="utf-8")

    assert result is True


# the e2e stack


def test_the_e2e_override_publishes_no_host_port():
    """Should let the browser suite run beside every other checkout."""
    result = PUBLISHED.findall(E2E.read_text(encoding="utf-8"))
    expected = []

    assert result == expected


def test_the_e2e_service_waits_for_the_stack_to_be_healthy():
    """Should not open a browser at a server that is still migrating."""
    result = "condition: service_healthy" in E2E.read_text(encoding="utf-8")

    assert result is True


def test_the_e2e_service_is_told_where_the_stack_is():
    """Should reach the web container by name, not by a host port."""
    result = "http://web:8000" in E2E.read_text(encoding="utf-8")

    assert result is True


def test_the_e2e_service_runs_against_the_real_settings():
    """Should exercise the settings a deployment uses, not the test ones."""
    result = "pandora.web.settings" in E2E.read_text(encoding="utf-8")

    assert result is True


def test_the_e2e_recipe_uses_the_e2e_override():
    """Should compose the two files — the base alone has no browser."""
    result = "compose_e2e" in recipe_body("ci-e2e")

    assert result is True


@pytest.mark.parametrize("recipe", ("ci-test", "ci-test-pg-focus", "ci-e2e"))
def test_focused_test_recipes_accept_pytest_arguments(recipe):
    result = re.search(rf"^{re.escape(recipe)} \*args:", justfile_text(), re.MULTILINE)

    assert result is not None


def test_the_default_gate_leaves_the_browser_suite_out():
    """Should keep the fast gate fast — the browser image is a gigabyte."""
    match = re.search(r"^ci:(.*)$", justfile_text(), re.MULTILINE)

    result = "ci-e2e" in match.group(1)

    assert result is False


def test_the_browser_suite_is_not_collected_by_the_unit_run():
    """Should not try to open a browser during `just ci-test`."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    result = 'testpaths = ["tests"]' in text

    assert result is True
