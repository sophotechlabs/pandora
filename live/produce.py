"""Feed the two log doors and the real Alertmanager, then wait for arrival.

The shippers are the real ones — Vector tailing a file, the OpenTelemetry
collector reading another — so the only thing this has to do is write lines and
fire alerts. Waiting is done by polling pandora rather than sleeping, because a
sleep long enough to be safe is long enough to be annoying.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time

import requests

LOG_DIR = pathlib.Path("/var/log/live")
BASE = os.environ["PANDORA_LIVE_URL"].rstrip("/")
ALERTMANAGER = os.environ["ALERTMANAGER_URL"].rstrip("/")
DEADLINE = 120

TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "/srv/shipper/pipeline.py", line 88, in flush\n'
    "    self.sink.write(batch)\n"
    '  File "/srv/shipper/sink.py", line 31, in write\n'
    "    raise UpstreamRefused(response.status_code)\n"
    "shipper.errors.UpstreamRefused: 503"
)

VECTOR_LINES = [
    {
        "message": "queue depth over the watermark",
        "level": "error",
        "service": "shipper",
        "host": "live-node-1",
        "error.kind": "QueueOverflow",
    },
    {
        "message": "flush failed",
        "level": "error",
        "service": "shipper",
        "host": "live-node-1",
        "stack": TRACEBACK,
    },
    {
        "message": "retrying in 5s",
        "level": "warning",
        "service": "shipper",
        "host": "live-node-1",
    },
]

OTEL_LINES = [
    {
        "message": "cache eviction storm",
        "level": "error",
        "service.name": "cache",
        "error.kind": "EvictionStorm",
    },
]

ALERTS = [
    {
        "labels": {
            "alertname": "LiveTargetDown",
            "namespace": "payments",
            "service": "checkout",
            "severity": "critical",
            "pod": f"checkout-7d9f-{index}",
        },
        "annotations": {
            "summary": "checkout is not being scraped",
            "description": f"replica {index} of 6 down for 5m",
        },
    }
    for index in range(6)
]


def write_lines(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def fire_alerts() -> None:
    response = requests.post(f"{ALERTMANAGER}/api/v2/alerts", json=ALERTS, timeout=10)
    response.raise_for_status()


def resolve_alerts() -> None:
    resolved = [dict(alert, endsAt=_now()) for alert in ALERTS]
    response = requests.post(f"{ALERTMANAGER}/api/v2/alerts", json=resolved, timeout=10)
    response.raise_for_status()


def _now() -> str:
    import datetime

    return datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")


def titles() -> list[str]:
    response = requests.get(
        f"{BASE}/api/v1/issues",
        headers={"Authorization": "Bearer live-read-token"},
        params={"limit": 100},
        timeout=10,
    )
    response.raise_for_status()
    return [row["title"] for row in response.json()["results"]]


def wait_for(expected: set[str], what: str) -> None:
    started = time.monotonic()
    seen: list[str] = []
    while time.monotonic() - started < DEADLINE:
        seen = titles()
        missing = [
            fragment
            for fragment in expected
            if not any(fragment in title for title in seen)
        ]
        if not missing:
            print(f"{what}: arrived after {time.monotonic() - started:.0f}s")
            return
        time.sleep(2)
    print(f"{what}: MISSING {sorted(missing)}", file=sys.stderr)
    print(f"{what}: pandora holds {sorted(seen)}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    write_lines(LOG_DIR / "app.log", VECTOR_LINES)
    write_lines(LOG_DIR / "otel.log", OTEL_LINES)
    fire_alerts()
    wait_for(
        {"QueueOverflow", "UpstreamRefused", "EvictionStorm", "LiveTargetDown"},
        "logs and alerts",
    )
    resolve_alerts()
    time.sleep(8)
    print("producer done")


if __name__ == "__main__":
    main()
