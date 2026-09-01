from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import pathlib
import subprocess
import time
import zipfile

import requests
import sentry_sdk

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTEXT = os.environ["PANDORA_KIND_CONTEXT"]
NAMESPACE = os.environ["PANDORA_KIND_NAMESPACE"]
RELEASE = os.environ["PANDORA_KIND_RELEASE"]
IMAGE = os.environ["PANDORA_KIND_IMAGE"]
PORT = int(os.environ.get("PANDORA_KIND_PORT", "18080"))
DEPLOYMENT = f"{RELEASE}-pandora"
MESSAGE = "Pandora kind lifecycle probe"
UPLOAD_TOKEN = "pandora-kind-upload-token"
DEBUG_ID = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"


def command(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def kubectl(*args: str) -> subprocess.CompletedProcess[str]:
    return command("kubectl", "--context", CONTEXT, "-n", NAMESPACE, *args)


def shell(code: str) -> str:
    result = kubectl(
        "exec",
        f"deployment/{DEPLOYMENT}",
        "--",
        "python",
        "manage.py",
        "shell",
        "--no-imports",
        "-c",
        code,
    )
    lines = result.stdout.strip().splitlines()
    if not lines:
        raise RuntimeError("Pandora shell returned no value")
    return lines[-1]


def rollout() -> None:
    kubectl("rollout", "status", f"deployment/{DEPLOYMENT}", "--timeout=5m")


@contextlib.contextmanager
def endpoint():
    process = subprocess.Popen(
        [
            "kubectl",
            "--context",
            CONTEXT,
            "-n",
            NAMESPACE,
            "port-forward",
            f"service/{DEPLOYMENT}",
            f"{PORT}:8000",
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    base_url = f"http://localhost:{PORT}"
    try:
        for _ in range(60):
            if process.poll() is not None:
                detail = ""
                if process.stderr is not None:
                    detail = process.stderr.read()
                raise RuntimeError(f"kubectl port-forward stopped: {detail}")
            try:
                response = requests.get(f"{base_url}/ready/", timeout=1)
                if response.status_code == requests.codes.ok:
                    break
            except requests.RequestException:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("Pandora did not become reachable through port-forward")
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def credentials() -> tuple[int, str]:
    value = shell(
        "from pandora.core.models import DsnKey, IngestToken, Project, TokenScope, TokenSource; "
        "p, _ = Project.objects.get_or_create(slug='kind', defaults={'name': 'Kind'}); "
        "k, _ = DsnKey.objects.get_or_create(public_key='pandorakinddsn00000000000000000001', defaults={'project': p}); "
        "IngestToken.objects.update_or_create(token='pandora-kind-upload-token', defaults={'project': p, 'name': 'kind upload', 'source': TokenSource.SDK, 'scope': TokenScope.INGEST, 'active': True}); "
        "print(f'{p.pk}:{k.public_key}')"
    )
    project_id, public_key = value.split(":", maxsplit=1)
    return int(project_id), public_key


def send_event(base_url: str, project_id: int, public_key: str) -> None:
    sentry_sdk.init(
        dsn=f"http://{public_key}@localhost:{PORT}/{project_id}",
        traces_sample_rate=0,
    )
    sentry_sdk.capture_message(MESSAGE, level="error")
    result = sentry_sdk.flush(timeout=10)
    if result is False:
        raise RuntimeError("Sentry SDK did not flush the lifecycle event")


def bundle() -> bytes:
    document = {
        "version": 3,
        "file": "app.js",
        "sources": ["src/kind.js"],
        "names": [],
        "mappings": "AAAA",
        "sourcesContent": ["throw new Error('kind')"],
        "debug_id": DEBUG_ID,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("app.js.map", json.dumps(document))
    return buffer.getvalue()


def upload_bundle(base_url: str) -> None:
    payload = bundle()
    checksum = hashlib.sha1(payload, usedforsecurity=False).hexdigest()
    headers = {"Authorization": f"Bearer {UPLOAD_TOKEN}"}
    response = requests.post(
        f"{base_url}/api/0/organizations/pandora/chunk-upload/",
        files={checksum: (checksum, payload, "application/octet-stream")},
        headers=headers,
        timeout=10,
    )
    response.raise_for_status()
    response = requests.post(
        f"{base_url}/api/0/organizations/pandora/artifactbundle/assemble/",
        json={"checksum": checksum, "chunks": [checksum], "projects": ["kind"]},
        headers=headers,
        timeout=10,
    )
    response.raise_for_status()
    if response.json()["state"] != "ok":
        raise RuntimeError(response.text)


def signed_in_session(base_url: str) -> requests.Session:
    session = requests.Session()
    response = session.get(f"{base_url}/login/", timeout=10)
    response.raise_for_status()
    csrf = session.cookies.get("csrftoken")
    if csrf is None:
        raise RuntimeError("login page did not set a CSRF token")
    response = session.post(
        f"{base_url}/login/",
        data={
            "username": "admin",
            "password": "pandora-kind-password",
            "csrfmiddlewaretoken": csrf,
        },
        headers={"Referer": f"{base_url}/login/"},
        timeout=10,
    )
    response.raise_for_status()
    if response.url.endswith("/login/"):
        raise RuntimeError("Pandora kind login did not authenticate")
    return session


def verify_page(base_url: str) -> None:
    session = signed_in_session(base_url)
    for _ in range(30):
        response = session.get(f"{base_url}/", timeout=10)
        response.raise_for_status()
        if MESSAGE in response.text:
            return
        time.sleep(1)
    raise RuntimeError("the lifecycle event did not appear on the rendered stream")


def verify_artifact() -> None:
    result = shell(
        "from pandora.artifacts.models import BundleFile; "
        f"f = BundleFile.objects.get(bundle__debug_id='{DEBUG_ID}'); "
        "print(f.blob.storage.exists(f.blob.name))"
    )
    if result != "True":
        raise RuntimeError("the source-map artifact did not survive the lifecycle")


def recreate_pod() -> None:
    kubectl("delete", "pod", "-l", "app.kubernetes.io/component=web", "--wait=false")
    rollout()


def upgrade() -> None:
    command(
        "helm",
        "upgrade",
        RELEASE,
        str(ROOT / "deploy/helm/pandora"),
        "--kube-context",
        CONTEXT,
        "--namespace",
        NAMESPACE,
        "--wait",
        "--timeout",
        "5m",
        "--set",
        f"image.repository={IMAGE.split(':', maxsplit=1)[0]}",
        "--set",
        f"image.tag={IMAGE.split(':', maxsplit=1)[1]}",
        "--set",
        "image.pullPolicy=Never",
        "--set",
        "host=localhost",
        "--set",
        "persistence.size=1Gi",
        "--set",
        "persistence.storageClass=pandora-kind",
        "--set",
        "settings.secureCookies=false",
        "--set",
        "settings.retentionDays=31",
        "--set",
        "secrets.secretKey=pandora-kind-secret-key-that-is-only-for-tests",
        "--set",
        "superuser.password=pandora-kind-password",
    )


def run_job(name: str) -> None:
    job = f"pandora-kind-{name}"
    kubectl("delete", "job", job, "--ignore-not-found=true")
    kubectl("create", "job", job, f"--from=cronjob/{DEPLOYMENT}-{name}")
    kubectl("wait", "--for=condition=complete", f"job/{job}", "--timeout=5m")


def full_cases() -> None:
    shell(
        "from datetime import timedelta; "
        "from django.utils import timezone; "
        "from pandora.artifacts.service import store_chunk; "
        "from pandora.core.models import Project; "
        "p = Project.objects.get(slug='kind'); "
        "store_chunk(p, b'stale-kind-chunk', timezone.now() - timedelta(hours=7)); "
        "print('created')"
    )
    shell(
        "from pandora.ingest.models import EnvelopeState, RawEnvelope; "
        "r = RawEnvelope.objects.filter(state=EnvelopeState.DONE).first(); "
        "assert r is not None; "
        "RawEnvelope.objects.create(project=r.project, source=r.source, environment=r.environment, payload=r.payload, state=EnvelopeState.FAILED, error='kind replay'); "
        "print('created')"
    )
    run_job("prune")
    run_job("replay")
    result = shell(
        "from pandora.artifacts.models import UploadChunk; "
        "from pandora.ingest.models import EnvelopeState, RawEnvelope; "
        "print(f'{UploadChunk.objects.count()}:{RawEnvelope.objects.filter(state=EnvelopeState.FAILED).count()}')"
    )
    if result != "0:0":
        raise RuntimeError(f"maintenance jobs left stale state: {result}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tier", choices=("smoke", "full"))
    arguments = parser.parse_args()

    rollout()
    project_id, public_key = credentials()
    with endpoint() as base_url:
        send_event(base_url, project_id, public_key)
        upload_bundle(base_url)
        verify_page(base_url)
    recreate_pod()
    with endpoint() as base_url:
        verify_page(base_url)
    verify_artifact()
    upgrade()
    with endpoint() as base_url:
        verify_page(base_url)
    verify_artifact()
    if arguments.tier == "full":
        full_cases()


if __name__ == "__main__":
    main()
