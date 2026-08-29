# Changelog

Notable changes per release. Dates are the tag's, and the wording is what the change does for an operator rather than what it did to the code.

## Unreleased

### Added

- **Stack traces.** An SDK event is stored with its interfaces intact — the exception chain, threads, breadcrumbs, user, request, contexts, SDK, modules and debug images — and the issue page renders it: causes under their own headings, frames innermost-first with application frames marked and opened, numbered source context, frame locals, the breadcrumb timeline and one card per context. Every interface is bounded at ingest.
- **The alert–error join.** An issue page lists the other issues that rose above their own seven-day rate inside its window and share infrastructure labels with it, in both directions. Ranked by rate change rather than volume. `PANDORA_CORRELATION_KEYS` and `PANDORA_CORRELATION_WINDOW_MINUTES` configure it.
- **A Helm chart** under `deploy/helm/pandora`: SQLite on a claim by default, an external Postgres when you want one, optional ingress, reconcile loop, cron jobs and ServiceMonitor. `just chart-lint` renders and validates it.
- **`just quickstart`** — one container, SQLite on a volume, no compose and no `.env`, printing the URL and a generated admin password.
- **`DJANGO_SECURE_COOKIES`**, so an instance reached over plain HTTP on a trusted network can sign in. Defaults to secure whenever `DJANGO_DEBUG` is off.
- **A licence.** [FSL-1.1-ALv2](LICENSE), the same terms as Spinoza, converting to Apache 2.0 two years after each release.

### Changed

- **Nothing binds a fixed host port**, so several checkouts can build, test and run at once. Ports moved to `docker-compose.local.yml` with an empty default; `just url` and `just dburl` report what a running stack landed on.
- The image builds on the host network, and the source is mounted only for local work — CI runs against the code in the image.
- `django` is held at `>=6.0.8,<6.1` and `sqlparse` at `>=0.6.0`, clearing five known CVEs the previous lock carried.

## 0.6.0

- Back up SQLite with `VACUUM INTO` while writes continue, name the snapshot when given a directory, move stored events between databases with `transfer_events`, and hand freed pages back on prune.

## 0.5.1

- The bootstrap message points at the operator UI rather than the admin.
- The application is called Pandora everywhere a human reads it.

## 0.5.0

- An operator UI at `/` — issue stream with a query language, issue detail, overview and ingest pages — instead of doing triage in the Django admin.

## 0.4.1

- Reconcile no longer resurrects alerts the Alertmanager route sends to a blackhole receiver.

## 0.4.0

- Regroup SDK issues from retained envelopes and relink stored events by id, so a grouping-rule change rewrites history rather than only the future.
- A failing CronJob no longer mints an issue per run: the run name is dropped from the fingerprint.
- SDK events group on the call site, and an id-shaped tag key no longer crowds out the tag breakdown.
- No default admin password and no guessable tokens.
- Health checks the pooled connection, so a database blink is not a wall of 500s.

## 0.3.0

- Health and metrics report something that can be trusted.
- One bad alert in a group no longer takes the rest of the group down with it.
- A resolved alert that starts firing again is recorded as a regression.
- Alert ingest writes far fewer statements per alert.

## 0.2.0

- The Sentry envelope endpoint: unmodified Sentry SDKs can point at Pandora.

## 0.1.0

- Alertmanager webhooks grouped into issues with episodes, triage state and activity.
- SQLite and Postgres event stores, with a prune command.
- Read-only `/api/v1` for issues, issue detail and events.
- An Alertmanager client with a reconcile loop and silences.
