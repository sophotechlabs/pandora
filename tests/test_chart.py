import pathlib
import shutil
import subprocess

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
CHART = ROOT / "deploy" / "helm" / "pandora"

FULL = (
    "--set",
    "ingress.enabled=true",
    "--set",
    "reconcile.enabled=true",
    "--set",
    "alertmanager.url=https://alertmanager:9093",
    "--set",
    "alertmanager.caBundle.secretName=platform-ca",
    "--set",
    "serviceMonitor.enabled=true",
    "--set",
    "otel.endpoint=http://alloy:4318",
)

needs_helm = pytest.mark.skipif(
    shutil.which("helm") is None,
    reason="helm not available on this host — run `just chart-lint`",
)


def render(*args):
    proc = subprocess.run(
        ["helm", "template", "pandora", str(CHART), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return [doc for doc in yaml.safe_load_all(proc.stdout) if doc]


def render_fails(*args):
    return subprocess.run(
        ["helm", "template", "pandora", str(CHART), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def pod_specs(docs):
    specs = []
    for doc in docs:
        if doc["kind"] == "Deployment":
            specs.append(doc["spec"]["template"]["spec"])
        if doc["kind"] == "CronJob":
            specs.append(doc["spec"]["jobTemplate"]["spec"]["template"]["spec"])
    return specs


def env_of(container):
    return {entry["name"]: entry.get("value") for entry in container.get("env", [])}


# the chart is declared, not implied


def test_the_chart_version_and_app_version_are_pinned():
    """Should name what it installs — an unversioned chart cannot be upgraded or rolled back."""
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text())

    result = (bool(chart["version"]), bool(chart["appVersion"]))
    expected = (True, True)

    assert result == expected


def test_the_app_version_matches_the_package():
    """Should ship the chart alongside the release it deploys, so a bumped image is a bumped chart."""
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
    pyproject = (ROOT / "pyproject.toml").read_text()

    result = f'version = "{chart["appVersion"]}"' in pyproject

    assert result is True


# what a default install produces


@needs_helm
def test_a_default_install_needs_no_values():
    """Should install with nothing set — a chart that requires a values file is a chart nobody tries."""
    kinds = sorted(doc["kind"] for doc in render())
    expected = [
        "CronJob",
        "CronJob",
        "CronJob",
        "CronJob",
        "Deployment",
        "PersistentVolumeClaim",
        "Secret",
        "Service",
    ]

    assert kinds == expected


@needs_helm
def test_the_default_install_creates_no_ingress():
    """Should not guess at an ingress class or a certificate issuer — those are cluster-specific."""
    result = [doc for doc in render() if doc["kind"] == "Ingress"]
    expected = []

    assert result == expected


@needs_helm
def test_the_default_maintenance_jobs_run_their_management_commands():
    result = {
        doc["metadata"]["name"]: doc["spec"]["jobTemplate"]["spec"]["template"]["spec"][
            "containers"
        ][0]["command"]
        for doc in render()
        if doc["kind"] == "CronJob"
    }
    expected = {
        "pandora-pandora-monitors": ["python", "manage.py", "monitors"],
        "pandora-pandora-prune": ["python", "manage.py", "prune"],
        "pandora-pandora-replay": ["python", "manage.py", "replay"],
        "pandora-pandora-rollouts": ["python", "manage.py", "rollouts"],
    }

    assert result == expected


@needs_helm
def test_turning_everything_on_renders():
    """Should hold together with the optional pieces enabled, which is how it runs in a real cluster."""
    kinds = sorted(doc["kind"] for doc in render(*FULL))
    expected = [
        "CronJob",
        "CronJob",
        "CronJob",
        "CronJob",
        "Deployment",
        "Deployment",
        "Ingress",
        "PersistentVolumeClaim",
        "Secret",
        "Service",
        "ServiceMonitor",
    ]

    assert kinds == expected


# every pod, not just the one that is easy to check


@needs_helm
def test_every_pod_runs_as_a_non_root_user():
    """Should apply the security context to the cron jobs and the reconcile loop too, not only the web pod."""
    result = [
        spec["securityContext"]["runAsNonRoot"] for spec in pod_specs(render(*FULL))
    ]

    assert result == [True] * len(result)
    assert len(result) == 6


@needs_helm
def test_every_container_has_a_read_only_root_and_no_capabilities():
    """Should keep the container unable to write outside its volumes — the image already writes only to /tmp and /data."""
    result = []
    for spec in pod_specs(render(*FULL)):
        for container in spec["containers"]:
            security = container["securityContext"]
            result.append(
                (
                    security["readOnlyRootFilesystem"],
                    security["allowPrivilegeEscalation"],
                    security["capabilities"]["drop"],
                )
            )

    assert result == [(True, False, ["ALL"])] * len(result)


@needs_helm
def test_every_container_takes_the_image_from_values():
    """Should let one value move every workload — a hardcoded tag anywhere means a half-upgraded install."""
    docs = render(
        *FULL,
        "--set",
        "image.repository=example.test/pandora",
        "--set",
        "image.tag=9.9.9",
    )
    result = set()
    for spec in pod_specs(docs):
        for container in spec["containers"]:
            result.add(container["image"])
    expected = {"example.test/pandora:9.9.9"}

    assert result == expected


@needs_helm
def test_every_writable_path_is_a_volume():
    """Should mount /tmp and the prometheus directory as their own volumes — with a read-only root the process cannot start without them."""
    result = []
    for spec in pod_specs(render(*FULL)):
        for container in spec["containers"]:
            mounts = {mount["mountPath"] for mount in container["volumeMounts"]}
            result.append({"/tmp", "/tmp/prometheus", "/data"} <= mounts)

    assert result == [True] * len(result)


@needs_helm
def test_the_ingest_byte_limit_is_rendered_as_an_integer():
    result = []
    for spec in pod_specs(render(*FULL)):
        for container in spec["containers"]:
            result.append(env_of(container)["PANDORA_INGEST_MAX_BYTES"])

    assert result == ["1048576"] * len(result)


# migrations, which two writers must not race


@needs_helm
def test_only_the_web_deployment_runs_migrations():
    """Should migrate from one place — the reconcile loop and the cron jobs starting a migration at the same moment is a race on one sqlite file."""
    result = []
    for doc in render(*FULL):
        for spec in pod_specs([doc]):
            for container in spec["containers"]:
                if env_of(container).get("PANDORA_RUN_MIGRATIONS") == "1":
                    result.append(container["name"])
    expected = ["web"]

    assert result == expected


# the database


@needs_helm
def test_sqlite_is_the_default_and_lands_on_the_volume():
    """Should keep the single-container promise — one file on one claim, no second service to run."""
    docs = render()
    container = [doc for doc in docs if doc["kind"] == "Deployment"][0]["spec"][
        "template"
    ]["spec"]["containers"][0]

    result = env_of(container)["DATABASE_URL"]
    expected = "sqlite:////data/pandora.sqlite3"

    assert result == expected


@needs_helm
def test_an_external_database_replaces_the_claim():
    """Should let postgres take over without leaving an unused volume behind."""
    docs = render(
        "--set",
        "database.url=postgres://pandora:pandora@db:5432/pandora",
        "--set",
        "persistence.enabled=false",
    )
    container = [doc for doc in docs if doc["kind"] == "Deployment"][0]["spec"][
        "template"
    ]["spec"]["containers"][0]

    result = (
        env_of(container)["DATABASE_URL"],
        [doc for doc in docs if doc["kind"] == "PersistentVolumeClaim"],
    )
    expected = ("postgres://pandora:pandora@db:5432/pandora", [])

    assert result == expected


@needs_helm
def test_disabling_persistence_requires_an_external_database():
    result = render_fails("--set", "persistence.enabled=false")

    assert result.returncode != 0
    assert "requires database.url" in result.stderr


@needs_helm
def test_sqlite_refuses_multiple_replicas():
    result = render_fails("--set", "replicaCount=2")

    assert result.returncode != 0
    assert "replicaCount=1" in result.stderr


@needs_helm
def test_postgres_allows_multiple_replicas():
    docs = render(
        "--set",
        "database.url=postgres://pandora:pandora@db:5432/pandora",
        "--set",
        "persistence.enabled=false",
        "--set",
        "replicaCount=2",
    )
    deployment = [doc for doc in docs if doc["kind"] == "Deployment"][0]

    assert deployment["spec"]["replicas"] == 2


@needs_helm
def test_bundles_land_on_the_persistent_volume():
    """Should keep uploaded source maps across a restart, not in the container."""
    docs = render()
    container = [doc for doc in docs if doc["kind"] == "Deployment"][0]["spec"][
        "template"
    ]["spec"]["containers"][0]

    result = env_of(container)["PANDORA_ARTIFACT_DIR"]
    expected = "/data/artifacts"

    assert result == expected


@needs_helm
def test_no_artifact_dir_is_set_without_a_volume():
    """Should not point at a path nothing is mounted on."""
    docs = render(
        "--set",
        "database.url=postgres://pandora:pandora@db:5432/pandora",
        "--set",
        "persistence.enabled=false",
    )
    container = [doc for doc in docs if doc["kind"] == "Deployment"][0]["spec"][
        "template"
    ]["spec"]["containers"][0]

    result = "PANDORA_ARTIFACT_DIR" in env_of(container)
    expected = False

    assert result is expected


# secrets


@needs_helm
def test_a_secret_key_is_generated_when_none_is_given():
    """Should never ship a known key — the setting is required in production and a default would be a shared one."""
    secret = [doc for doc in render() if doc["kind"] == "Secret"][0]

    result = len(secret["stringData"]["DJANGO_SECRET_KEY"]) >= 32

    assert result is True


@needs_helm
def test_an_existing_secret_replaces_the_generated_one():
    """Should let an operator keep the key in their own secret store."""
    docs = render("--set", "secrets.existingSecret=pandora-own")

    result = [doc for doc in docs if doc["kind"] == "Secret"]
    assert result == []

    container = [doc for doc in docs if doc["kind"] == "Deployment"][0]["spec"][
        "template"
    ]["spec"]["containers"][0]
    assert container["envFrom"][0]["secretRef"]["name"] == "pandora-own"


# probes and hosts


@needs_helm
def test_the_probes_use_the_endpoints_the_app_serves():
    """Should probe /health/ and /ready/ with the Host header the app allows, or every probe 400s on an allowed-hosts mismatch."""
    container = [doc for doc in render() if doc["kind"] == "Deployment"][0]["spec"][
        "template"
    ]["spec"]["containers"][0]

    result = (
        container["livenessProbe"]["httpGet"]["path"],
        container["readinessProbe"]["httpGet"]["path"],
        container["livenessProbe"]["httpGet"]["httpHeaders"][0]["value"],
    )
    expected = ("/health/", "/ready/", "pandora.example.com")

    assert result == expected


@needs_helm
def test_the_host_value_reaches_every_setting_that_needs_it():
    """Should set allowed hosts, the CSRF origin and the base URL from one value — three places to change is three places to get wrong."""
    docs = render("--set", "host=pandora.example.test")
    container = [doc for doc in docs if doc["kind"] == "Deployment"][0]["spec"][
        "template"
    ]["spec"]["containers"][0]
    env = env_of(container)

    result = (
        "pandora.example.test" in env["DJANGO_ALLOWED_HOSTS"],
        env["DJANGO_CSRF_TRUSTED_ORIGINS"],
        env["PANDORA_BASE_URL"],
    )
    expected = (True, "https://pandora.example.test", "https://pandora.example.test")

    assert result == expected


@needs_helm
def test_the_pod_ip_is_allowed_so_a_probe_by_ip_is_not_rejected():
    """Should accept the kubelet's probe, which addresses the pod by IP rather than by host."""
    container = [doc for doc in render() if doc["kind"] == "Deployment"][0]["spec"][
        "template"
    ]["spec"]["containers"][0]

    result = "$(POD_IP)" in env_of(container)["DJANGO_ALLOWED_HOSTS"]

    assert result is True


# cron jobs run commands that exist


@needs_helm
def test_the_cron_jobs_call_real_management_commands():
    """Should schedule commands the image actually has — a typo here fails silently every night."""
    commands = pathlib.Path(ROOT / "src" / "pandora")
    available = {
        path.stem
        for path in commands.rglob("management/commands/*.py")
        if not path.stem.startswith("_")
    }

    result = []
    for doc in render(*FULL):
        if doc["kind"] != "CronJob":
            continue
        container = doc["spec"]["jobTemplate"]["spec"]["template"]["spec"][
            "containers"
        ][0]
        result.append(container["command"][-1])

    assert result
    assert set(result) <= available, (
        f"{set(result) - available} is not a management command"
    )


@needs_helm
def test_the_reconcile_loop_runs_as_a_loop_not_a_one_shot():
    """Should pass --loop — the missed-delivery counter lives in the process, so a one-shot run can never reach a third consecutive miss."""
    docs = render(*FULL)
    reconcile = [doc for doc in docs if doc["metadata"]["name"].endswith("-reconcile")][
        0
    ]
    command = reconcile["spec"]["template"]["spec"]["containers"][0]["command"]

    result = "--loop" in command

    assert result is True


def test_the_chart_is_covered_by_a_recipe():
    """Should be lintable without remembering the incantation."""
    result = "chart-lint:" in (ROOT / "justfile").read_text()

    assert result is True


# single sign-on


@needs_helm
def test_a_default_install_carries_no_sso_settings():
    """Should leave the login page as it was until an operator asks for a provider."""
    docs = render()
    container = pod_specs(docs)[0]["containers"][0]

    result = [name for name in env_of(container) if name.startswith("PANDORA_OIDC")]
    expected = []

    assert result == expected


@needs_helm
def test_naming_an_issuer_configures_the_provider():
    """Should need one value to switch single sign-on on."""
    docs = render("--set", "oidc.issuer=https://keycloak.test/realms/pandora")
    container = pod_specs(docs)[0]["containers"][0]

    result = env_of(container)["PANDORA_OIDC_ISSUER"]
    expected = "https://keycloak.test/realms/pandora"

    assert result == expected


@needs_helm
def test_the_group_mapping_reaches_the_container():
    """Should let the provider decide the role, which is the point of mapping groups."""
    docs = render(
        "--set",
        "oidc.issuer=https://keycloak.test/realms/pandora",
        "--set",
        "oidc.ownerGroup=platform",
    )
    container = pod_specs(docs)[0]["containers"][0]

    result = env_of(container)["PANDORA_OIDC_OWNER_GROUP"]
    expected = "platform"

    assert result == expected


@needs_helm
def test_the_client_secret_goes_into_the_secret_not_the_pod_spec():
    """Should never render a client secret into a manifest anyone can read."""
    docs = render(
        "--set",
        "oidc.issuer=https://keycloak.test/realms/pandora",
        "--set",
        "oidc.clientSecret=shh",
    )
    container = pod_specs(docs)[0]["containers"][0]
    secrets = [doc for doc in docs if doc["kind"] == "Secret"]

    result = (
        "PANDORA_OIDC_CLIENT_SECRET" in env_of(container),
        secrets[0]["stringData"]["PANDORA_OIDC_CLIENT_SECRET"],
    )
    expected = (False, "shh")

    assert result == expected
