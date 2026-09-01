import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
JUSTFILE = ROOT / "justfile"
DOCKERFILE = ROOT / "Dockerfile"
LIVE = ROOT / "docker-compose.live.yml"
E2E_WORKFLOW = ROOT / ".github/workflows/e2e.yaml"
SCHEDULED_WORKFLOW = ROOT / ".github/workflows/scheduled.yaml"
PUBLISHED = re.compile(r'^\s*-\s*"?\d[\d.]*:\d+:\d+', re.MULTILINE)


def recipe_body(name):
    text = JUSTFILE.read_text(encoding="utf-8")
    match = re.search(
        rf"^{re.escape(name)}:.*?$(.*?)(?=^\S|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def workflow(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_the_live_stack_publishes_no_host_ports():
    assert PUBLISHED.findall(LIVE.read_text(encoding="utf-8")) == []


def test_the_live_stack_uses_every_real_client_boundary():
    services = yaml.safe_load(LIVE.read_text(encoding="utf-8"))["services"]
    expected = {
        "alertmanager",
        "vector",
        "otelcol",
        "sdk-python",
        "sdk-python-crash",
        "sdk-node",
        "wrap",
        "produce",
        "live",
    }

    assert expected <= set(services)


def test_live_pytest_uses_its_plugin_independent_config():
    service = yaml.safe_load(LIVE.read_text(encoding="utf-8"))["services"]["live"]

    assert service["entrypoint"][:4] == ["pytest", "-c", "live/pytest.ini", "live"]


def test_the_wrapper_recipe_requires_the_expected_exit_code():
    body = recipe_body("ci-live-clients")

    assert 'if [ "$status" -ne 3 ]' in body
    assert "|| true" not in body


def test_the_scheduled_workflow_runs_and_cleans_the_live_suite():
    job = workflow(SCHEDULED_WORKFLOW)["jobs"]["live"]
    runs = [step.get("run") for step in job["steps"]]
    cleanup = [step for step in job["steps"] if step.get("run") == "just ci-live-down"]

    assert "just ci-live" in runs
    assert cleanup[0]["if"] == "always()"


def test_the_pull_request_workflow_runs_kind_smoke_and_cleans_up():
    job = workflow(E2E_WORKFLOW)["jobs"]["kind"]
    runs = [step.get("run") for step in job["steps"]]
    cleanup = [step for step in job["steps"] if step.get("run") == "just ci-kind-down"]

    assert "just ci-kind-smoke" in runs
    assert cleanup[0]["if"] == "always()"


def test_the_scheduled_workflow_runs_the_full_kind_tier():
    job = workflow(SCHEDULED_WORKFLOW)["jobs"]["kind"]
    runs = [step.get("run") for step in job["steps"]]

    assert "just ci-kind-full" in runs


def test_the_kind_storage_is_bound_to_a_persistent_volume():
    documents = list(
        yaml.safe_load_all((ROOT / "e2e/kind-storage.yaml").read_text(encoding="utf-8"))
    )
    kinds = {document["kind"] for document in documents}
    volume = [
        document for document in documents if document["kind"] == "PersistentVolume"
    ][0]

    assert kinds == {"StorageClass", "PersistentVolume"}
    assert volume["spec"]["persistentVolumeReclaimPolicy"] == "Retain"


def test_kind_prepares_the_host_path_for_the_non_root_container():
    body = recipe_body("kind-up")

    assert "mkdir -p /var/local/pandora-kind" in body
    assert "chown -R 1000:1000 /var/local/pandora-kind" in body


def test_the_kind_lifecycle_covers_both_tiers():
    smoke = recipe_body("ci-kind-smoke")
    full = recipe_body("ci-kind-full")

    assert "kind_lifecycle.py smoke" in smoke
    assert "kind_lifecycle.py full" in full


def test_the_kind_cluster_uses_the_fmctl_session_name():
    text = JUSTFILE.read_text(encoding="utf-8")

    assert 'env_var_or_default("SPINOZA_KIND_CLUSTER", "pandora-ci")' in text


def test_kind_install_rolls_out_every_loaded_image():
    body = recipe_body("kind-install")

    assert "podAnnotations.kind-build" in body
    assert "rollout status" in body


def test_the_production_image_includes_the_advertised_oidc_support():
    builder = DOCKERFILE.read_text(encoding="utf-8").split("FROM builder AS dev")[0]

    assert builder.count("--extra oidc") == 2
