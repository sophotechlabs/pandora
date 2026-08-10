# pandora

Self-hosted, k8s-native event tracker on the Sentry model: occurrences grouped into issues with first/last-seen, counts, sparkline and triage state. Two front doors into one core — Alertmanager webhooks and the Sentry envelope protocol — and one Django admin over both. SQLite and Postgres are both first-class.

Alertmanager tells you an alert is firing. It does not tell you when it resolved itself, how often it flaps, or whether the thing you fixed came back. Pandora keeps that record.

## Stack

Django + django-unfold admin · Postgres or SQLite · uv · just. No broker: ingest goes through a replayable envelope table, a reconcile loop and a prune job.

## Quickstart

```sh
cp .env.example .env
just bootstrap
just superuser
just seed-demo
```

Admin at http://localhost:8000/admin/.

## Development

```sh
just install      # editable install with web+dev extras into the active venv
just ci           # full docker-based CI gate chain, both database backends
just test-local   # pytest without docker (SQLite)
```

`just ci-test` runs the suite on SQLite; `just ci-test-pg` runs the same suite against the compose Postgres. Both run in CI — portability is enforced, not trusted. The Postgres run also carries coverage: it is the only run that exercises both `EventStore` implementations, because it adds a second in-memory SQLite connection alongside Postgres.

## Endpoints

| Path | What | Status |
|---|---|---|
| `/admin/` | the UI | live |
| `/health/` | liveness/readiness | live |
| `/metrics` | Prometheus | live |
| `/ingest/am/` | Alertmanager webhook receiver (Bearer token) | live |
| `/api/<project_id>/envelope/` | Sentry SDK envelope endpoint (DSN key) | live |
| `/api/v1/issues` | issue list, filtered and cursor-paged | live |
| `/api/v1/issues/<id>` | one issue with its episodes and tag stats | live |
| `/api/v1/issues/<id>/events` | the stored events of one issue | live |

Both ingest routes existed from the first commit and answered 501 until their phase landed — the URL and auth scheme are what SDKs and Alertmanager configs hard-code, so they were pinned before anything was written behind them. Both doors are open now.

An SDK points at pandora with a DSN of the form `http://<public_key>@<host>/<project_id>`, where the key is a `DsnKey` row. Envelopes arrive gzipped or plain; `event` items become one durable `RawEnvelope` each, and every other item type — transactions, sessions, attachments — is counted, acked with `200` and dropped, so an SDK never retries what pandora will not keep. Retries are free: the Sentry event id is the dedup key, held in `ProcessedEvent`, and an issue's `event_count` moves only when that row is genuinely new. SDK events carry no episode; the firing/resolved column stays null on an issue that only SDKs feed.

Pandora reimplements the Sentry ingest wire format from public protocol documentation so unmodified MIT-licensed Sentry SDKs can point at it. No Sentry server code is used. "Sentry-compatible" is a statement about the wire format, nothing more.

## Grouping

An occurrence becomes an issue through a fingerprint, and neither door puts anything per-instance in one.

Alertmanager alerts group on their labels minus a denylist. The seeded rule drops `pod`, `instance`, `container`, `endpoint`, `replicaset`, `uid`, `node` and `job_name` — the last is the run name kube-state-metrics stamps on `KubeJobFailed`, so without it every failed CronJob run minted its own issue. Rules are editable in the admin: denylist or allowlist, optionally scoped to one project and one `alertname` pattern, lowest priority number first.

SDK events group on the stack signature — the exception's module and class, then the module and function of the culprit frame, the last one marked `in_app`. Two things are deliberately absent. The line number, because it moves with every deploy that touches the file above it. The exception value, because it carries the URL or id that failed and would mint one issue per value. An event with no exception groups on its logger and its **logentry template** (`fetch failed for source %s`), not on the formatted line, which names one source. A client that wants a finer split sets `scope.fingerprint`; `{{ default }}` expands to the derived parts.

Title, culprit and level follow the issue's most recent event. The title is built from the invariants — `HTTPError: listopad.core.transport in get_json` — so it describes the group rather than whichever event opened it. The varying detail stays on the event's `message`, which is what `/issues/<id>/events` returns.

Tag breakdowns are capped per key. A key whose values never repeat — a task id, a request id — collapses to a single `<other>` row once it fills the cap, so it stops crowding out the keys worth reading; the detail response rations rows per key on top of that.

### Regrouping

Changing a rule does not rewrite history on its own:

```sh
python manage.py regroup --dry-run
```

Two passes. The first recomputes Alertmanager issues from the permanent `Episode` history. The second recomputes SDK issues by re-reading `RawEnvelope` payloads through the translator and relinking stored events by id — an SDK event carries no episode, so nothing else can move it. Both carry triage state with an issue that regroups whole, drop issues nothing points at any more, and roll back together under `--dry-run`.

The SDK pass sees only what `PANDORA_ENVELOPE_RETENTION_DAYS` (7) still holds; an event whose envelope has expired keeps the grouping it has, and the run reports how many envelopes it read and how many it could not parse. Nothing goes back through the ingest path, so `ProcessedEvent` is untouched and no event is counted twice.

## Alertmanager

Webhooks are the fast path; they are not the whole truth. A delivery can be lost, and Alertmanager can stop reporting an alert without ever sending a resolve. `reconcile` is the correction:

```sh
python manage.py reconcile --loop 60
```

Each pass reads `GET /api/v2/alerts` (asking for silenced, inhibited and unprocessed alerts as well — a suppressed alert is still firing) and compares it with the episodes pandora holds open:

- an alert with no open episode → the missed webhook is synthesised and replayed through the same envelope inbox and consumer the webhook path uses;
- an open episode whose alert is absent → closed only after **three consecutive** misses, so an Alertmanager restart cannot manufacture a resolve. The counter lives in memory and resets when the process restarts, which errs towards keeping episodes open;
- a `Watchdog` alert → `pandora_watchdog_last_seen_timestamp`, the dead-man's switch that watches the alert path itself.

Scope comes from the Alertmanager ingest token — its project and environment. Pass `--project` and `--environment` when one pandora serves more than one cluster. `--metrics-port` exposes the gauge from the reconcile process, which has no web port of its own.

Without `--loop` it runs a single pass and exits. Deploy it with `--loop`, not as a CronJob: the miss counter lives in the process, so a one-shot run catches up missed webhooks but can never reach a third consecutive miss. A database error ends the process rather than being swallowed — the restart brings a working connection back, which a wedged loop never would.

Reconcile never writes issues directly. Everything it corrects goes through `RawEnvelope` and `process_envelope`, so counters stay exactly-once and a correction is as replayable as a delivery.

`GET /api/v2/alerts` returns every alert Alertmanager holds, including the ones its own routing tree sends to a blackhole receiver — routing decides notification, not membership. Reconcile would otherwise resurrect exactly what the route drops. `PANDORA_RECONCILE_IGNORE` (default `Watchdog,InfoInhibitor`) is the list it will not open an episode for; the `Watchdog` gauge is still stamped before the filter runs, because that reading is the point of the alert. Each pass reports how many it skipped.

### Silences

The issue changelist can silence for 1h, 4h or 1d. Matchers are structured and exact — one `isEqual` matcher per retained grouping label, no regex — so a silence covers the issue and nothing else. An issue that kept no grouping labels is refused rather than turned into a silence that matches everything. The comment links back to the issue; set `PANDORA_BASE_URL` to make that link absolute. Each silence is recorded as a `SilenceLink` and shows in the issue's activity feed; the link list can lift a silence early, and `prune` drops links once they expire.

## JSON API

Read-only and versioned from the first commit — `/api/v1/` is what consumers pin. Paths carry no trailing slash. One header authenticates:

```
Authorization: Bearer <IngestToken with scope=read>
```

A token belongs to one project and every response is scoped to it: another project's issues are absent from the list and answer 404 by id. A token with the wrong scope gets 403, an unknown or deactivated one 401, an unsafe verb 405. Errors are `{"detail": "..."}` with the matching status.

### `GET /api/v1/issues`

Filters: `triage_state` and `source_state` (repeatable — `?triage_state=new&triage_state=ack`), `project` (slug), `environment`, `since` (ISO 8601, compared against `last_seen`). Paging: `limit` (default 50, max 200) and `cursor`.

```json
{
  "results": [
    {
      "id": 12,
      "project": "infrastructure",
      "fingerprint_hash": "9f2c...",
      "title": "TargetDown: scrape target unreachable",
      "culprit": "alertname=TargetDown namespace=monitoring",
      "level": "warning",
      "environment": "p-mk1",
      "source_state": "firing",
      "triage_state": "new",
      "event_count": 3,
      "open_episode_count": 1,
      "grouping_labels": {"alertname": "TargetDown", "namespace": "monitoring"},
      "first_seen": "2026-08-04T06:00:00Z",
      "last_seen": "2026-08-04T12:00:00Z",
      "last_resolved_at": null
    }
  ],
  "next_cursor": "MjAyNi0wOC0wNFQxMjowMDowMCswMDowMHwxMg"
}
```

Echo `next_cursor` back as `?cursor=` until it comes back null. The keyset is `(last_seen, id)` descending, so a page boundary between issues sharing a timestamp neither repeats nor drops a row.

### `GET /api/v1/issues/<id>`

Every list field, plus `fingerprint` (the components behind the hash), `episodes` (newest 20) and `tag_stats` (up to 500 rows, ordered by key then by frequency).

```json
{
  "fingerprint": ["alertname:TargetDown", "namespace:monitoring"],
  "episodes": [
    {
      "id": 41,
      "am_fingerprint": "3c1f6a2b9d4e5087",
      "labels": {"alertname": "TargetDown", "job": "node-exporter"},
      "environment": "p-mk1",
      "starts_at": "2026-08-04T10:00:00Z",
      "ends_at": null,
      "delivery_count": 2,
      "last_delivery_at": "2026-08-04T12:00:00Z"
    }
  ],
  "tag_stats": [{"key": "namespace", "value": "monitoring", "count": 12}]
}
```

### `GET /api/v1/issues/<id>/events`

The event payloads behind an issue, newest first, read through the `EventStore`. Parameters: `limit`, `cursor` (an event id) and `episode`. A store that does not implement `fetch` answers 501, not 500.

```json
{
  "results": [
    {
      "id": "01J8ZQ7X4N0000000000000001",
      "project_id": 1,
      "timestamp": "2026-08-04T12:05:00Z",
      "level": "error",
      "message": "TargetDown: scrape target unreachable",
      "issue_id": 12,
      "episode_id": "41",
      "fingerprint": ["alertname:TargetDown"],
      "tags": {"namespace": "monitoring"},
      "extra": {"generatorURL": "https://prometheus.example/graph"},
      "source": "am",
      "environment": "p-mk1"
    }
  ],
  "next_cursor": "01J8ZQ7X4N0000000000000001"
}
```
