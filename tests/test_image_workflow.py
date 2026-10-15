import os
import pathlib
import shutil
import subprocess

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".forgejo" / "workflows" / "image.yaml"
REGISTRY = "zot.c.p-mk1.sopho.tech"
SHA = "a4aba31e38498fba82f244c0b3ee1bc21b51bde2"

STUB = """#!/bin/sh
echo "$(basename "$0") $*" >> "$CALLS"
if [ "$(basename "$0")" = curl ]; then
    printf '%s' "$REGISTRY_STATUS"
fi
"""

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="the workflow step is a bash script"
)


def build_step():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["image"]["steps"]
    return next(step["run"] for step in steps if "run" in step)


@pytest.fixture
def run_step(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("curl", "docker"):
        stub = bin_dir / tool
        stub.write_text(STUB, encoding="utf-8")
        stub.chmod(0o755)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "pandora"\nversion = "0.7.0"\n', encoding="utf-8"
    )
    calls = tmp_path / "calls"

    def run(status):
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "CALLS": str(calls),
            "REGISTRY_STATUS": status,
            "REGISTRY": REGISTRY,
            "GITHUB_SHA": SHA,
        }
        completed = subprocess.run(
            ["bash", "-c", build_step()],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        lines = []
        if calls.exists():
            lines = calls.read_text(encoding="utf-8").splitlines()
        return completed, lines

    return run


def pushed(lines):
    return [line.split()[-1] for line in lines if line.startswith("docker push")]


def built(lines):
    return any(line.startswith("docker build") for line in lines)


# the version tag is written once


def test_a_new_version_pushes_its_tag(run_step):
    """Should publish the version tag the first time that version is built."""
    completed, lines = run_step("404")

    result = (completed.returncode, pushed(lines))
    expected = (
        0,
        [
            f"{REGISTRY}/apps/pandora:0.7.0",
            f"{REGISTRY}/apps/pandora:latest",
            f"{REGISTRY}/apps/pandora:sha-{SHA[:12]}",
        ],
    )

    assert result == expected


def test_a_released_version_is_never_overwritten(run_step):
    """Should leave the released image alone when main moves between releases."""
    completed, lines = run_step("200")

    result = (completed.returncode, pushed(lines))
    expected = (
        0,
        [
            f"{REGISTRY}/apps/pandora:latest",
            f"{REGISTRY}/apps/pandora:sha-{SHA[:12]}",
        ],
    )

    assert result == expected


def test_a_registry_that_cannot_answer_stops_the_build(run_step):
    """Should not guess, in either direction, when the registry gives no clear answer."""
    completed, lines = run_step("503")

    result = (
        completed.returncode,
        built(lines),
        pushed(lines),
        "HTTP 503" in completed.stderr,
    )
    expected = (1, False, [], True)

    assert result == expected


def test_the_probe_asks_for_the_version_manifest(run_step):
    """Should look up the exact tag about to be pushed, not the tag list."""
    _, lines = run_step("404")

    probes = [line for line in lines if line.startswith("curl")]

    result = [probe.split()[-1] for probe in probes]
    expected = [f"https://{REGISTRY}/v2/apps/pandora/manifests/0.7.0"]

    assert result == expected


def test_the_probe_is_a_head_request(run_step):
    """Should ask whether the tag exists without downloading the manifest."""
    _, lines = run_step("404")

    probe = next(line for line in lines if line.startswith("curl"))

    result = " -I " in f" {probe} "

    assert result is True
