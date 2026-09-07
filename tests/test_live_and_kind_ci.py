import json
import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
JUSTFILE = ROOT / "justfile"
DOCKERFILE = ROOT / "Dockerfile"
PYPROJECT = ROOT / "pyproject.toml"
LIVE = ROOT / "docker-compose.live.yml"
E2E_WORKFLOW = ROOT / ".github/workflows/e2e.yaml"
SCHEDULED_WORKFLOW = ROOT / ".github/workflows/scheduled.yaml"
CLUSTER_HARNESS = ROOT / "e2e/harness/cluster.ts"
TEST_HARNESS = ROOT / "e2e/harness/test.ts"
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


def test_the_node_client_disables_interactive_update_checks():
    body = recipe_body("ci-live-clients")

    assert "run --rm -T sdk-node" in body


def test_the_live_stack_prepares_its_shared_log_volume():
    body = recipe_body("ci-live-up")

    assert "--user root --entrypoint chown produce" in body
    assert "1000:1000 /var/log/live" in body


def test_the_live_suite_installs_its_browser_test_runtime():
    project = PYPROJECT.read_text(encoding="utf-8")
    live_dependencies = project.split("live = [", 1)[1].split("]", 1)[0]
    live_image = (
        DOCKERFILE.read_text(encoding="utf-8")
        .split(
            "FROM mcr.microsoft.com/playwright/python:v1.56.0-noble AS live",
            1,
        )[1]
        .split("FROM base AS prod", 1)[0]
    )

    assert '"playwright==1.56.0"' in live_dependencies
    assert '"pytest-playwright>=0.7"' in live_dependencies
    assert "--extra live" in live_image
    assert "COPY --chown=1000:1000 . ." in live_image
    assert "chown 1000:1000 /app" in live_image
    assert "USER 1000:1000" in live_image


def test_the_scheduled_workflow_runs_and_cleans_the_live_suite():
    job = workflow(SCHEDULED_WORKFLOW)["jobs"]["live"]
    runs = [step.get("run") for step in job["steps"]]
    cleanup = [step for step in job["steps"] if step.get("run") == "just ci-live-down"]

    assert "just ci-live" in runs
    assert cleanup[0]["if"] == "always()"


def test_the_pull_request_workflow_runs_selected_groups_and_cleans_up():
    job = workflow(E2E_WORKFLOW)["jobs"]["kind"]
    runs = [step.get("run") for step in job["steps"]]
    group_step = [
        step
        for step in job["steps"]
        if step.get("name") == "Run the selected capability group"
    ][0]
    cleanup = [step for step in job["steps"] if step.get("run") == "just ci-kind-down"]

    assert 'just test-e2e-group "$E2E_GROUP"' in runs
    assert group_step["env"] == {"E2E_GROUP": "${{ matrix.group }}"}
    assert cleanup[0]["if"] == "always()"


def test_the_scheduled_workflow_runs_the_full_kind_tier():
    job = workflow(SCHEDULED_WORKFLOW)["jobs"]["kind"]
    runs = [step.get("run") for step in job["steps"]]

    assert "just ci-kind-full" in runs


def test_the_scheduled_kind_job_can_finish_the_longest_group():
    job = workflow(SCHEDULED_WORKFLOW)["jobs"]["kind"]
    suite = json.loads((ROOT / "e2e/suite.json").read_text(encoding="utf-8"))
    longest_group = max(group["timeoutMinutes"] for group in suite["groups"])

    assert job["timeout-minutes"] >= longest_group


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
    text = JUSTFILE.read_text(encoding="utf-8")

    assert re.search(r"^ci-kind-smoke: test-e2e$", text, re.MULTILINE)
    assert re.search(r"^ci-kind-full: test-e2e-full$", text, re.MULTILINE)


def test_the_kind_reset_clears_the_raw_event_store():
    text = CLUSTER_HARNESS.read_text(encoding="utf-8")

    assert "DELETE FROM events_event" in text


def test_every_spec_inherits_the_application_reset_fixture():
    text = TEST_HARNESS.read_text(encoding="utf-8")

    assert "base.extend" in text
    assert "auto: true" in text
    assert ".beforeEach" not in text


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


def test_images_force_the_entrypoint_to_be_executable():
    text = DOCKERFILE.read_text(encoding="utf-8")

    assert text.count("--chmod=755 docker/entrypoint.sh") == 2


def test_the_production_image_makes_root_owned_application_files_readable():
    production = DOCKERFILE.read_text(encoding="utf-8").split("FROM base AS prod")[1]

    assert "chmod -R a+rX /app" in production
