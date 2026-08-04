import http.server
import json
import threading
import uuid

POLL_INTERVAL = 0.01
ALERTS_PATH = "/api/v2/alerts"
SILENCES_PATH = "/api/v2/silences"
SILENCE_PREFIX = "/api/v2/silence/"


class Call:
    def __init__(self, method, path, query, body):
        self.method = method
        self.path = path
        self.query = query
        self.body = body

    def __repr__(self):
        return f"Call({self.method} {self.path}?{self.query})"


class FakeAlertmanager:
    def __init__(self):
        self.alerts = []
        self.silences = {}
        self.calls = []
        self.failures = []
        self.delay = 0.0
        self.body_override = None
        self._server = None
        self._thread = None

    # lifecycle

    def start(self):
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.fake = self
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": POLL_INTERVAL},
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    @property
    def url(self):
        return f"http://127.0.0.1:{self._server.server_port}"

    # programming

    def fail_next(self, status, times=1):
        self.failures.extend([status] * times)

    def next_failure(self):
        if not self.failures:
            return None
        return self.failures.pop(0)

    # inspection

    def calls_to(self, method, path):
        return [
            call for call in self.calls if (call.method, call.path) == (method, path)
        ]

    def silence_bodies(self):
        return [call.body for call in self.calls_to("POST", SILENCES_PATH)]

    def deleted_ids(self):
        return [
            call.path[len(SILENCE_PREFIX) :]
            for call in self.calls
            if call.method == "DELETE"
        ]


def alert(
    fingerprint,
    labels,
    *,
    state="active",
    starts_at="2026-08-04T10:00:00Z",
    ends_at="2026-08-04T13:00:00Z",
    annotations=None,
    generator_url="https://p.test/graph",
):
    if annotations is None:
        annotations = {"summary": "scrape target unreachable"}
    return {
        "fingerprint": fingerprint,
        "labels": dict(labels),
        "annotations": dict(annotations),
        "startsAt": starts_at,
        "endsAt": ends_at,
        "updatedAt": starts_at,
        "generatorURL": generator_url,
        "receivers": [{"name": "pandora"}],
        "status": {
            "state": state,
            "silencedBy": [],
            "inhibitedBy": [],
            "mutedBy": [],
        },
    }


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path, query = _split(self.path)
        self._record("GET", path, query, None)
        if self._failed():
            return
        if path != ALERTS_PATH:
            self._send(404, {"detail": "not found"})
            return
        self._send(200, self.server.fake.alerts)

    def do_POST(self):
        path, query = _split(self.path)
        body = self._read()
        self._record("POST", path, query, body)
        if self._failed():
            return
        if path != SILENCES_PATH:
            self._send(404, {"detail": "not found"})
            return
        silence_id = str(uuid.uuid4())
        self.server.fake.silences[silence_id] = body
        self._send(200, {"silenceID": silence_id})

    def do_DELETE(self):
        path, query = _split(self.path)
        self._record("DELETE", path, query, None)
        if self._failed():
            return
        if not path.startswith(SILENCE_PREFIX):
            self._send(404, {"detail": "not found"})
            return
        self.server.fake.silences.pop(path[len(SILENCE_PREFIX) :], None)
        self._send(200, {})

    def log_message(self, fmt, *args):
        return

    def _record(self, method, path, query, body):
        fake = self.server.fake
        fake.calls.append(Call(method, path, query, body))
        if fake.delay > 0:
            threading.Event().wait(fake.delay)

    def _failed(self):
        status = self.server.fake.next_failure()
        if status is None:
            return False
        self._send(status, {"detail": "programmed failure"})
        return True

    def _read(self):
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return None
        return json.loads(self.rfile.read(length))

    def _send(self, status, payload):
        override = self.server.fake.body_override
        if override is None:
            raw = json.dumps(payload).encode()
        else:
            raw = override
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def _split(target):
    if "?" not in target:
        return target, ""
    path, _, query = target.partition("?")
    return path, query
