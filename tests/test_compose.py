import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = ROOT / "docker-compose.yml"
LOCAL = ROOT / "docker-compose.local.yml"
JUSTFILE = ROOT / "justfile"

CREATING_RECIPES = ("up", "up-nobuild", "up-fg", "bootstrap")
PUBLISHED = re.compile(r'^\s*-\s*"?\d[\d.]*:\d+:\d+', re.MULTILINE)


def justfile_text():
    return JUSTFILE.read_text(encoding="utf-8")


def recipe_body(name):
    text = justfile_text()
    match = re.search(
        rf"^{re.escape(name)}:.*?$(.*?)(?=^\S|\Z)", text, re.MULTILINE | re.DOTALL
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
