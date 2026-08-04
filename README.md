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

`just ci-test` runs the suite on SQLite; `just ci-test-pg` runs the same suite against the compose Postgres. Both run in CI — portability is enforced, not trusted.

## Endpoints

| Path | What | Status |
|---|---|---|
| `/admin/` | the UI | live |
| `/health/` | liveness/readiness | live |
| `/metrics` | Prometheus | live |
| `/ingest/am/` | Alertmanager webhook receiver (Bearer token) | route frozen, answers 501 |
| `/api/<project_id>/envelope/` | Sentry SDK envelope endpoint | route frozen, answers 501 |
| `/api/v1/` | read-only JSON API | not built |

The two ingest routes exist from the first commit and answer 501 until their phase lands — the URL and auth scheme are what SDKs and Alertmanager configs hard-code, so they are pinned before anything is written behind them.

Pandora reimplements the Sentry ingest wire format from public protocol documentation so unmodified MIT-licensed Sentry SDKs can point at it. No Sentry server code is used. "Sentry-compatible" is a statement about the wire format, nothing more.
