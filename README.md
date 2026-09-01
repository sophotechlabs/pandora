# Pandora

[![python](https://img.shields.io/github/actions/workflow/status/sophotechlabs/pandora/python.yaml?branch=main&label=python)](https://github.com/sophotechlabs/pandora/actions/workflows/python.yaml)
[![e2e](https://img.shields.io/github/actions/workflow/status/sophotechlabs/pandora/e2e.yaml?branch=main&label=e2e)](https://github.com/sophotechlabs/pandora/actions/workflows/e2e.yaml)
[![repo](https://img.shields.io/github/actions/workflow/status/sophotechlabs/pandora/repo.yaml?branch=main&label=repo)](https://github.com/sophotechlabs/pandora/actions/workflows/repo.yaml)
[![license](https://img.shields.io/badge/license-FSL--1.1--ALv2-blue)](LICENSE)

Self-hosted, k8s-native event tracker on the Sentry model: occurrences grouped into issues with first/last-seen, counts, sparkline and triage state. Two front doors into one core — Alertmanager webhooks and the Sentry envelope protocol — and one operator UI over both. SQLite and Postgres are both first-class.

Alertmanager tells you an alert is firing. It does not tell you when it resolved itself, how often it flaps, or whether the thing you fixed came back. Pandora keeps that record.

## Stack

Django · Postgres or SQLite · uv · just. The operator UI is server-rendered templates with one hand-written stylesheet and one script — no build step, no runtime dependency the image does not already carry. Configuration keeps the django-unfold admin. No broker: ingest goes through a replayable envelope table, a reconcile loop and a prune job.

## Quickstart

One container, SQLite on a volume, nothing to configure:

```sh
just quickstart
```

It prints the URL and a generated admin password, and `just quickstart-down` removes it again.

For development, the compose stack gives you Postgres and a live-reloading source mount:

```sh
cp .env.example .env
just bootstrap
just superuser
just seed-demo
```

`bootstrap` prints the URL it came up on; `just url` prints it again, and `just dburl` gives the connection string for a local `psql`. The config admin is `/admin/` on the same address.

## Running more than one checkout

Nothing binds a fixed host port, so several worktrees can build, test and run at once — on a laptop or on one CI box.

`docker-compose.yml` publishes nothing at all. Ports come from `docker-compose.local.yml`, which only the container-creating recipes (`up`, `up-nobuild`, `up-fg`, `bootstrap`) add, and it leaves the host side of both empty so Docker picks a free port per checkout. `just ci-test-pg` starts Postgres on the compose network and reaches it at `db:5432`, so the gate never binds anything and two checkouts can run it simultaneously.

Set `PANDORA_WEB_PORT` or `PANDORA_DB_PORT` in a checkout's `.env` to pin one to a predictable address — worth doing in whichever checkout you keep a browser tab on, and worth leaving empty everywhere else.

The rest is already per-checkout: Compose derives its project name from the directory, so containers, networks and the `postgres_data` volume never overlap; the image `just ci-docker-scan` builds is tagged with the directory name (override with `PANDORA_IMAGE_TAG`); and the fake Alertmanager the tests run binds an ephemeral port.

## Deploying

`deploy/helm/pandora` installs Pandora on Kubernetes. It defaults to SQLite on a persistent claim — one pod, no database to run — and takes a `postgres://` URL when you would rather use one.

```sh
helm install pandora deploy/helm/pandora --set host=pandora.example.com --set ingress.enabled=true
```

The chart pulls `ghcr.io/sophotechlabs/pandora`, published on every release and on every commit to `main` — `image.tag` defaults to the chart's app version, `:main` is the tip and `:sha-<commit>` pins one build.

`host` is the one value that has to be right: it fills allowed hosts, the CSRF origin and the base URL. Turn on `reconcile.enabled` with `alertmanager.url` to close episodes whose webhook never arrived, and `serviceMonitor.enabled` if you run the Prometheus operator. The chart generates a secret key and an admin password on install and keeps them across upgrades; `helm template` shows exactly what it will create, and `just chart-lint` validates it.

## Development

```sh
just install      # editable install with web+dev extras into the active venv
just ci           # full docker-based CI gate chain, both database backends
just test-local   # pytest without docker (SQLite)
```

`just ci-test` runs the suite on SQLite; `just ci-test-pg` runs the same suite against the compose Postgres. Both run in CI — portability is enforced, not trusted. The Postgres run also carries coverage: it is the only run that exercises both `EventStore` implementations, because it adds a second in-memory SQLite connection alongside Postgres.

`just ci-e2e` drives a real browser against the running stack. `just ci-live` goes further and is the one that catches compatibility bugs a unit test cannot: it stands up Pandora with Postgres and gunicorn, then points the **official** `sentry-sdk` for Python, `@sentry/node`, `sentry-cli`, Vector, the OpenTelemetry collector, Alertmanager and the `pandora-wrap` binary at it, and reads the result back off the pages. Nothing in it builds a payload by hand. Three real defects came out of its first run: the source-map upload negotiation was refused by `sentry-cli`, `@sentry/node` sends every envelope chunked and Django reads a chunked body as empty, and a Python traceback whose exception class does not end in `Error` was losing its type.

## Storage

`DATABASE_URL` picks the backend. On SQLite the connection is opened with `auto_vacuum=INCREMENTAL`, WAL and `synchronous=NORMAL`, and every write takes the lock immediately with a 20s busy timeout — so the web workers, the reconcile loop and the cron commands can share one file. The `auto_vacuum` pragma only takes on a database that does not exist yet, and only because it is set before `journal_mode`, which is the first statement that writes to the file.

`PANDORA_RETENTION_DAYS` (30) covers stored events, dedup markers, hourly buckets and activity rows; `PANDORA_ENVELOPE_RETENTION_DAYS` (7) covers the envelope inbox. Episodes and tag stats are never pruned. The floor on retention is the sparkline, which reads a 7-day window. `prune` hands the freed pages back with `PRAGMA incremental_vacuum` and republishes `pandora_database_bytes`, the gauge the readiness probe also keeps fresh.

```sh
python manage.py backup --to /scratch/pandora-$(date +%Y%m%d).sqlite3
```

`VACUUM INTO` — a consistent snapshot without stopping writes, and compact rather than a copy of the high-water mark. It refuses to overwrite, because `VACUUM INTO` cannot merge into an existing file. On Postgres it refuses outright and points at that backend's own path.

```sh
python manage.py transfer_events --from postgres://user:pass@host/pandora
```

Copies stored events out of another pandora database into this one, paging per project through both `EventStore` implementations. Events live in a raw table with no model behind it, so `dumpdata` does not carry them — restore the dump first, then run this. It refuses to start if no project was restored, and `INSERT OR IGNORE` makes a second run safe after an interrupted one.

## Endpoints

| Path | What | Status |
|---|---|---|
| `/` | issue stream | live |
| `/issues/<id>/` | one issue: latest stack trace, occurrences, episodes, tags, activity | live |
| `/overview/` | headline numbers, what is firing, what is new | live |
| `/ingest/` | envelope backlog, failures, replay, tokens | live |
| `/admin/` | configuration: projects, tokens, DSN keys, grouping rules | live |
| `/health/` | liveness/readiness | live |
| `/metrics` | Prometheus | live |
| `/ingest/am/` | Alertmanager webhook receiver (Bearer token) | live |
| `/api/<project_id>/envelope/` | Sentry SDK envelope endpoint (DSN key) | live |
| `/api/<project_id>/store/` | Sentry SDK store endpoint, for the older SDKs (DSN key) | live |
| `/api/<project_id>/logs/` | JSON-lines log shipping (DSN key) | live |
| `/api/<project_id>/integration/otlp/v1/logs` | OTLP/JSON logs (DSN key) | live |
| `/api/<project_id>/cron/<slug>/<key>/` | cron check-in | live |
| `/api/0/organizations/<org>/chunk-upload/` | source-map chunks, `sentry-cli` protocol (Bearer token) | live |
| `/api/0/organizations/<org>/artifactbundle/assemble/` | joins the chunks into a bundle (Bearer token) | live |
| `/api/v1/issues` | issue list, filtered and cursor-paged | live |
| `/api/v1/issues/<id>` | one issue with its episodes and tag stats | live |
| `/api/v1/issues/<id>/events` | the stored events of one issue | live |

## The UI

Everything an operator does lives at `/`; the admin is for configuration. Both surfaces run the same triage code, so a state change made in either leaves the same `IssueActivity` row.

The stream opens on `is:unresolved`. The search box takes a query rather than a filter sidebar:

| Filter | Example | Means |
|---|---|---|
| `is:` | `is:unresolved` `is:new` `is:ack` `is:resolved` `is:ignored` | triage state; `unresolved` is new plus acknowledged |
| `state:` | `state:firing` | what the source last said, not what a human decided |
| `level:` | `level:error` | debug, info, warning, error, fatal |
| `project:` | `project:infrastructure` | project slug |
| `environment:` | `env:p-mk1` | anywhere the issue has been seen; `env:` is the short form |
| `label:` | `label:namespace=payments` | a grouping label the fingerprint kept |
| `tag:` | `tag:pod=ledger-7d9f4c8b6d-hk2mp` | a value from the tag breakdown, including ones grouping dropped |
| `seen:` | `seen:1h` | last seen inside the window — `30m`, `6h`, `7d`, `2w` |
| `is:snoozed` | `is:snoozed` | quiet on purpose, by time or by occurrence count |
| `is:awake` | `is:awake` | everything not currently snoozed |
| `age:` | `age:1d` | first seen inside the window |
| `owner:` | `owner:platform` `owner:me` `owner:none` | the team or person an ownership rule routed it to |

Anything else is matched against the title, the culprit, the newest message and the frame paths it came from, and a bare hash prefix matches the fingerprint. Repeating a key widens it (`level:error level:fatal`); different keys narrow together. A term Pandora does not understand is named back above the table rather than silently returning nothing.

Selecting rows raises an action bar: acknowledge, resolve, ignore, snooze, merge, or silence in Alertmanager for 1h, 4h or 1d. `/` focuses the search box, `j` and `k` move through the rows, `x` selects one, `Enter` opens it.

Triage needs the `issues.change_issue` permission and replay needs `ingest.change_rawenvelope`, the same permissions the admin checks — a staff account without them gets a read-only UI. A team role grants the same permissions without touching the admin; see [More than one person](#more-than-one-person).

Both ingest routes existed from the first commit and answered 501 until their phase landed — the URL and auth scheme are what SDKs and Alertmanager configs hard-code, so they were pinned before anything was written behind them. Both doors are open now.

An SDK points at Pandora with a DSN of the form `http://<public_key>@<host>/<project_id>`, where the key is a `DsnKey` row. Envelopes arrive gzipped or plain; `event` items become one durable `RawEnvelope` each, and every other item type — transactions, sessions, attachments — is counted, acked with `200` and dropped, so an SDK never retries what Pandora will not keep. Retries are free: the Sentry event id is the dedup key, held in `ProcessedEvent`, and an issue's `event_count` moves only when that row is genuinely new. SDK events carry no episode; the firing/resolved column stays null on an issue that only SDKs feed.

Pandora reimplements the Sentry ingest wire format from public protocol documentation so unmodified MIT-licensed Sentry SDKs can point at it. No Sentry server code is used. "Sentry-compatible" is a statement about the wire format, nothing more.

### Ranking, saved views and what is different

**Sorting.** Last seen, first seen, events, and two more that cost no new storage. **Relevance** weights the last 24 hours four times the rest of the week, so an issue seen four hundred times last month sits below one seen forty times this morning. **Spread** counts the distinct values of `PANDORA_BREADTH_KEYS` (`pod,node,instance,namespace`) — *is it everyone or one node* is the first question in an incident. Last seen stays the default until relevance has been measured against real volume.

**Saved views.** Type a search, name it, and it joins the segment strip. One row holding a query and a sort, visible to everyone on the install — no personal/shared distinction, because on a box with one operator or one team it is not a distinction worth the schema.

**What sets an issue apart.** The issue page reads the tag breakdown already on disk and names the values that characterise *this* issue against the rest of the project — 80% on `node=broken-1` where the project runs at 4%. Sentry shows a tag distribution but never says which distribution is abnormal, which is the entire question. When a key has filled its cardinality cap the panel says the number came from a sample, because that is what makes it worth trusting.

**MTTR, as a gauge.** `/metrics` publishes `pandora_mttr_seconds` and `pandora_resolved_issues`, both labelled by source and computed from the activity trail over 30 days. **Split by source deliberately**: an Alertmanager issue resolves itself when the alert clears, so a combined number describes the monitoring rather than the team. Rollbar prints that caveat and gates the metric behind its second-highest tier; Sentry has neither at any tier. There is no chart UI here — the operator's Grafana already draws better ones.

**CSV export** of whatever the current query selected, up to 10,000 rows, from the button on the stream.

## Stack traces

An SDK event is stored with its interfaces intact, not only the parts grouping reads. The issue page opens on the newest event: the exception chain innermost-first, each `caused by` link under its own heading, the application frames marked and the first one expanded, source context numbered against the file, frame locals, the breadcrumb timeline newest-first, and one card per context — user, request, headers, SDK, runtime, OS, browser, trace.

Every interface is bounded at ingest, because the sender is not trusted: 25 exceptions in a chain, 25 threads, 250 frames per stack (the outermost are dropped and counted in `frames_omitted`), 20 context lines per side, 50 locals per frame, 100 breadcrumbs, 100 keys and 50 items per structure, five levels of nesting, 4096 characters per string. Source lines are the one thing never stripped — the indentation is the code.

A frame with no `context_line` says so rather than rendering an empty block.

Every event keeps its raw form too, behind **Raw payload** on the occurrence.

### Source maps

A minified JavaScript stack is unreadable — `n` at `basket.4c9e10.js:1:8402` names nothing. Upload the maps and the issue page shows `charge` at `src/payments.js:14` with the surrounding lines, resolved when the page is rendered rather than at ingest, so a map uploaded after the crash still fixes the events already stored.

Uploading speaks the `sentry-cli` chunk-upload protocol, so the tool you already have in the build works unchanged:

```sh
sentry-cli --url https://pandora.example.com \
  sourcemaps upload --org pandora --project web dist/
```

Point `SENTRY_AUTH_TOKEN` at an ingest token; `--org` can be anything, the token names the project. The upload is the protocol's own two phases: the tool asks `/api/0/organizations/<org>/artifactbundle/assemble/` which chunks are missing, POSTs those to `/api/0/organizations/<org>/chunk-upload/` (gzipped, addressed by sha1), and asks again. The pairing is by debug id — the uuid the bundler writes into both the minified file and its `.map`, and that the SDK repeats in `debug_meta` — so a filename that changes on every build never has to match.

A frame whose bundle has not been uploaded says which debug id is missing instead of showing a blank panel. Bundles land under `PANDORA_ARTIFACT_DIR` — the chart points it at the persistent volume when `persistence.enabled` is set, and without one they live in the container and go when it does. They expire on time-to-idle, not time-to-live: `manage.py prune` drops one that has symbolicated nothing for ninety days, and keeps one still resolving frames however old it is. A map for a release that is still running must not expire on a calendar.

## What Pandora refuses to keep

**Scrubbing is on by default.** Before an SDK event is written, any key whose name looks like a credential — `password`, `secret`, `token`, `auth`, `api_key`, `cookie`, `session` and the rest of Sentry's own list — is replaced, card numbers are masked wherever they appear (checked against the Luhn digit, so a long request id survives), and client IP addresses lose their last octet. It walks frame locals, request headers, contexts and the tag breakdown, not just the top level.

`PANDORA_SCRUB_KEYWORDS` adds names, `PANDORA_SCRUB_SAFE_KEYS` exempts them, `PANDORA_SCRUB_ANONYMISE_IP=0` keeps the full address, and `PANDORA_SCRUB_ENABLED=0` turns the lot off.

Beyond the defaults, **scrub rules** name a dotted path — `user.email`, `request.headers.*`, `**.vars` — and either remove or mask what they match.

**The fingerprint is computed before scrubbing.** Microsoft's App Center shipped the other order and split every issue in two when a redacted value was part of the grouping key; there is a test that pins ours.

**Retroactive redaction** is the only fix when the leak came from an app version you cannot patch:

```sh
python manage.py redact --project infrastructure --dry-run
```

It re-applies the current keywords and rules to events already stored, rewrites only the rows that actually change, pages through a store larger than memory, and rolls back under `--dry-run`. Sentry has no equivalent.

An occurrence can also be **deleted one at a time** from the occurrences tab, behind the same permission triage needs. Sentry cannot do that either.

**Drop rules** refuse a payload before the durable write, so the saving is disk rather than only noise. Each matches a field — `alertname`, `namespace`, `severity`, `type`, `value`, `message`, `release`, `environment`, `server_name`, `transaction`, `platform` — against a regular expression, counts what it refused, and works on both ingest doors. An invalid pattern never matches rather than taking ingest down.

## Getting told

Pandora had no way to tell anyone anything until now, which means the default could be chosen rather than retrofitted. **Notification is a property of the issue's state machine, not a rules engine.** There are five events and no condition builder:

| | |
|---|---|
| `issue.new` | something broke that was not broken before |
| `issue.regression` | something you resolved came back |
| `issue.unsnoozed` | a snooze expired and the issue is still live |
| `issue.milestone` | the 10th, 100th, 1000th, 10000th occurrence |
| `issue.resolved` | a person closed it |

Nothing fires on the second occurrence of an open issue. That is the behaviour that gets a tool muted.

A **destination** is a row: a webhook, an email list, or a Slack, Discord or Teams incoming-webhook URL. It picks which events it wants, a minimum level, an optional project, and a digest window. Webhooks are signed with HMAC-SHA256 in `X-Pandora-Signature` when a secret is set, so a receiver can prove the call came from this Pandora.

Deliveries are queued to a table, not sent on the ingest request, and a worker drains them:

```sh
python manage.py deliver --loop 15 --metrics-port 9110
```

Failures back off — 30s, 2m, 10m, 1h — and give up after five attempts, with the reason kept on the row. A digest window collects a storm into one message; without one, a pass still batches whatever is already queued for a destination into a single call. `prune` clears sent rows at the normal retention.

**No rules engine, no on-call rotations, no first-party integration catalogue.** Anything else takes the webhook, whose event vocabulary is the table above.

## When one box is not keeping up

`PANDORA_QUEUE` still defaults to `SyncQueue`, which processes an envelope inline on the request. One container with no worker is the right position for a single operator's cluster. Set it to `pandora.ingest.queue.AsyncQueue` and the envelope stays pending instead, for:

```sh
python manage.py consume --loop 5
```

The envelope table has been a durable, replayable queue since the first commit — this is the consumer it never had. A pass claims a batch (`SELECT … FOR UPDATE SKIP LOCKED` on Postgres; the single writer is the same guarantee on SQLite), applies it, and reports what it did. A consumer that dies leaves its batch claimed, and the next pass puts anything claimed for more than fifteen minutes back. **No broker.**

**Per-item size limits.** `PANDORA_INGEST_MAX_BYTES` (1 MiB) remains the cap on a whole envelope — raise it toward the protocol's 200 MiB if you want to. Inside it each item type now gets the limit the protocol names: 1 MiB an event, 100 KiB a check-in or a session batch, 4 KiB a client report. One number for everything let a 1 MiB client report through where the spec says 4 KiB.

**Four content encodings**: gzip, deflate, `br` and `zstd`. Every one is measured after decompression, so a compression bomb is refused on what it becomes rather than what it claims.

### Retention that is not a calendar

`PANDORA_RETENTION_DAYS` throws away the one occurrence of the rare bug at the same rate as the thousandth copy of the noisy one. Turn on `PANDORA_RETENTION_BY_RELEVANCE` and `prune` also thins each issue to a budget that halves with age — `PANDORA_RELEVANCE_BUDGET` (500) copies while it is fresh, half of that after `PANDORA_RELEVANCE_HALF_LIFE_DAYS` (7), and never below one. The rare issue keeps its single occurrence forever; the flood is thinned. It is off until an operator has measured it, and Sentry has nothing like it.

### The export is the backup story

```sh
python manage.py archive --to /var/backups/pandora
```

Gzipped JSON Lines, one object per project per hour, in hive-style paths — `project=1/year=2026/month=08/day=30/hour=14/events.jsonl.gz`. Readable with `zcat` and `jq`, queryable with duckdb, restorable with a path prefix. `PANDORA_ARCHIVE_DIR` gives it a default home; point it at a mounted S3 bucket and short retention stops being frightening. A SaaS is structurally not motivated to build this well.

## More ways in

An SDK is a dependency someone has to add, and there are always services nobody will ever instrument — a third-party chart, an operator, someone's Go binary from 2021. Those are the ones that page you. So the transport is a POST and a page of config, and the parsers are the work.

**Logs.** `POST /api/<project_id>/logs/` takes JSON Lines, one object per line, which Vector, rsyslog, journald and a CloudWatch drain all already produce. `POST /api/<project_id>/integration/otlp/v1/logs` takes the OTLP/JSON shape instead. Both authenticate with the project's DSN key, in `X-Sentry-Auth` or as `?sentry_key=`, so a shipper needs one header and no new credential type. Vector's `http` sink needs `encoding.codec = "json"` with `framing.method = "newline_delimited"` — its default wraps a batch in a JSON array, which is not what one-object-per-line means. The OpenTelemetry collector's `otlphttp` exporter needs `encoding: json`, because its default is protobuf. A line's message, level, logger, service, environment, release and timestamp are read from whichever of the usual key spellings it uses, and everything else becomes a tag.

A line carrying a stack trace becomes an exception with frames, not a wall of text. Four parsers, picked by what the trace looks like: Python tracebacks with the source line under each frame, Java stacks down to the package and line (`java.base/` module prefixes included), Go panics with the file:line under each function, and Node/V8. From there it is an ordinary issue — grouping, triage, tag stats, the whole UI — because the log becomes the same event shape an SDK sends.

**Cron check-ins.** `POST /api/<project_id>/cron/<slug>/<key>/` with `{"status": "in_progress"}` and then `ok` or `error`. The monitor is created by the first check-in, so a job that reports is a job that is watched — there is no configuration step, and no per-monitor cost. `manage.py monitors` sweeps for the ones that did not report inside their interval plus margin, or that ran past their maximum, and opens an issue for each. Sentry charges per active monitor.

`pandora-wrap` is a small Go binary that does the two calls around any command:

```sh
PANDORA_DSN=https://<key>@pandora.example.com/1 \
  pandora-wrap -monitor nightly-backup -- /usr/local/bin/backup.sh
```

It takes the same DSN an SDK does, defaults the monitor slug to the command's own name, reports `in_progress` before and `ok` or `error` after with the exit status and the tail of the output, and it never changes what the command returns or what it printed. `-environment`, `-release`, `-timeout` and `-quiet` are the rest of it. A static binary for linux and darwin on amd64 and arm64 is attached to each release.

**User feedback.** The `user_report` envelope item the SDKs already send is stored against the issue its event created, and the reports show on the issue page. What a person typed is usually worth more than the stack.

## Holding the door

An **ingest quota** caps how much a project may send in a window — a row in the admin naming a limit and a window in seconds, scoped to one project or to the whole install, tightest wins. Past the limit the door answers `429` with `X-Sentry-Rate-Limits` and `Retry-After`, which unmodified Sentry SDKs already honour, so a client backs off instead of retrying into a wall. Both doors hold the same contract.

**Spike protection** is off by default. Turned on, it compares the current hour against the median of the previous 24 and sheds when the ratio passes `PANDORA_SPIKE_FACTOR` (5) and the count passes `PANDORA_SPIKE_FLOOR` (100). The median rather than the mean, so one quiet hour cannot turn normal traffic into a spike.

With no quota configured and spike protection off, the gate does exactly what it did before — a size check and nothing else, with no counter written. Counters live in one small table, bucketed by window, and `prune` drops them after two days.

## More than one person

An install with one operator needs none of this — a staff account sees everything and may do everything, exactly as before. The moment a second person has an account, three things become available.

**Teams and roles.** A team holds people and, optionally, projects. A member of a team scoped to a project sees only that project's issues; a team with no projects named is install-wide. Three roles: a **viewer** reads, a **member** triages, snoozes and silences, an **owner** may also replay the ingest queue. The stream hides the buttons a role cannot press rather than answering `403` on the click, and an issue outside someone's projects answers `404` — the same answer as an issue that does not exist, so the scope leaks nothing.

A superuser is never scoped and never refused.

**Ownership rules** route an issue to a team or a person when it is first seen, matching on the stack-frame path, the request URL, the culprit or a tag:

```yaml
teams:
  - name: platform
    projects: [infrastructure]
    members:
      - user: dev
        role: member
      - user: boss
        role: owner
ownership_rules:
  - name: payments
    pattern: src/payments/*
    team: platform
  - name: checkout-pages
    pattern: https://shop.example.com/checkout*
    field: url
    user: dev
```

`field` is `path` (the default), `url`, `culprit` or `tag`; a tag pattern matches `key=value`. Patterns are shell globs and case-sensitive. **A rule assigns only when it is the only rule that matches** — two rules claiming the same issue assign nobody and the issue page lists both, because a wrongly-routed page is worse than an unrouted one. The stream carries an owner column and `owner:` filters on it: `owner:platform`, `owner:dev`, `owner:me`, `owner:none` for what nothing routed. The notification payload carries the owner too.

**Single sign-on** against any OpenID Connect provider. Set three variables and a Sign in with single sign-on button appears on the login page:

```sh
PANDORA_OIDC_ISSUER=https://keycloak.example.com/realms/pandora
PANDORA_OIDC_CLIENT_ID=pandora
PANDORA_OIDC_CLIENT_SECRET=...
```

The rest is discovered from the issuer. An account is created on first sign-in with no usable password, so the provider stays the only way in. Name a group per role — `PANDORA_OIDC_OWNER_GROUP`, `..._MEMBER_GROUP`, `..._VIEWER_GROUP` — and the role follows the provider on every sign-in, including a revocation; `PANDORA_OIDC_DEFAULT_ROLE` (viewer) covers everyone else. `PANDORA_OIDC_GROUPS_CLAIM` names the claim to read when the provider does not call it `groups`.

The callback is `/sso/callback/`.

## What happened, and who did it

Every action a person takes is recorded and shown at `/history/`: sign-ins and sign-outs with the route taken, triage, snoozes, silences, occurrence deletions, replays, `apply_config` runs and `redact` runs. Each entry names the actor, what it touched and the terms — a snooze records its spec, a replay records how many envelopes it moved. Filter by action or by person. `prune` drops entries past the retention window along with everything else.

Actions that were refused record nothing, so a permission failure cannot fill the log a real one would be found in.

## An agent's view

Pandora ships a read-only MCP server as an optional extra, so an agent can look at issues without being handed a browser session:

```sh
pip install 'pandora[mcp]'
PANDORA_MCP_TOKEN=<a read-scoped ingest token> python manage.py mcp
```

Four tools over stdio: `search_issues` (the same query language the UI uses), `get_issue`, `get_issue_events` — occurrences with their stack traces — and `issue_as_markdown`. Everything is scoped to the token's project, nothing writes, and an ingest-scoped token is refused. The extra is not in the image; the base install does not carry it.

## Configuration as a file

Everything an operator would otherwise click into the admin can live in a file the deployment mounts:

```yaml
projects:
  - slug: infrastructure
    name: Infrastructure
tokens:
  - name: alertmanager-p-mk1
    project: infrastructure
    token_env: PANDORA_TOKEN_AM_PMK1
    environment: p-mk1
dsn_keys:
  - project: infrastructure
    public_key_env: PANDORA_DSN_INFRA
grouping_rules:
  - priority: 100
    mode: denylist
    labels: [pod, instance, container, endpoint, replicaset, uid, node, job_name]
path_rules:
  - name: venv
    pattern: "^.*/(site-packages/)"
    replacement: "<venv>/\\1"
service_links:
  - name: Loki
    url_template: https://grafana.example.com/explore?q={pod}&from={padded_from_iso}&to={padded_to_iso}
```

```sh
python manage.py apply_config --path /etc/pandora/config.yaml --dry-run
```

Two more sections, `teams` and `ownership_rules`, are described under [More than one person](#more-than-one-person). `PANDORA_CONFIG` supplies the path when the flag is absent. Secrets go in by reference — any field takes a `_env` suffix naming the variable to read — so the file itself is committable.

**It reconciles rather than creates.** A token dropped from the file is deactivated, not left live; rows are never deleted, so the episodes and issues pointing at them survive. A run either applies completely or leaves nothing behind, and `--dry-run` prints the diff and rolls back.

## Taking an issue somewhere else

`?format=md` on any issue page renders it as Markdown — title, the facts table, the newest occurrences with their stack traces and breadcrumbs, episode history, tags, what else was firing in the same window, the outbound links and the activity trail. It is the artefact you paste into a chat, a ticket or an agent, and the page carries a button for it.

## Outbound links

An issue is a starting point, not a destination. **Service links** are URL templates in the admin, rendered as buttons on the issue page with the issue's own values already filled in:

```
https://grafana.example.com/d/pods?var-namespace={namespace}&from={padded_from_ms}&to={padded_to_ms}
https://loki.example.com/explore?q={pod}&from={from_iso}&to={to_iso}
```

Available names: every grouping label, every label of the newest episode, the most frequent value of every tag key the breakdown holds, plus `project`, `environment`, `issue`, `fingerprint`, and the window as `from_ms` / `to_ms` / `from_iso` / `to_iso` with `padded_` variants five minutes either side. A template naming something the issue does not have renders no button rather than a broken one. Leave the project blank for a template that applies to every project.

`PANDORA_GRAFANA_URL` and `PANDORA_LOKI_QUERY_URL` still work and read the same names.

## Releases, deploys and regression

**A process is on a release the moment it sends an event tagged with one.** That is the rollout signal, and it beats a marker posted by CI: with more than one replica the marker says the deploy finished while half the pods are still on the old image. Every release keeps its own first-seen, last-seen and count per environment, so a rollout that reached staging and stalled before production is visible rather than asserted.

Versions are **parsed and stored as a sort key**, semver and calendar versions both — `1.9.0` below `1.10.0`, `1.2.3-rc1` below `1.2.3`, `2025.12.1` below `2026.1.1`. Anything else, a git sha or a build id, sorts below every parsed version and alphabetically among its own kind, and the release is marked as unparsed so the UI can say the ordering is not real.

**Resolving is release-aware.** Resolve now, in the next release, in the current release, or in a named one. The choice stores a boundary, and every later event's release is compared to it: an equal or lower version leaves the issue resolved, a higher one reopens it. That is Countly's *reoccurred* semantics, nobody free implements it, and it is what stops a lagging replica from reopening something that is genuinely fixed. An event with no release reopens it, because the event cannot say what it was running.

**Suspect deploy** is the last deploy before the issue was first seen, shown on the issue page. It needs no repository access — suspect *commit* does, and is not built.

`manage.py deploy --project infrastructure --release 1.2.3 --environment p-mk1` marks a deploy from CI when you want one, with a state — started, succeeded, failed, timed out — and a deploy left started for an hour is marked timed out rather than sitting there forever. With `resolve_on_deploy` on for a project, it also resolves everything currently open in that environment against the new release: wipe the board, and let what comes back come back. Honeybadger does this by default; here it is off until a project asks.

### Release health

Pandora accepts the `session` and `sessions` envelope items and counts them into **their own aggregated table** — a session is a counter with a status, not a record, and it never reaches the event store. Statuses are healthy, errored, abnormal and crashed; crash-free is one minus crashed over total, and adoption is the release's share of the last 24 hours.

Sessions bypass the gate and sampling by design and nobody bills for them, which is why crash counts and crashed-session counts legitimately disagree.

## Three kinds of quiet

**Snooze** hides an issue for a while and it comes back on its own: 1 hour, 4 hours, a day, a week, or *the next 100 / 500 / 1000 occurrences*. The count form has no Sentry equivalent and it is the right answer for something that flaps. There is deliberately **no indefinite snooze** — an issue that can be silenced forever is one nobody looks at again.

**Ignore** is the triage state for something you have decided not to act on. **Resolve** says it is fixed, and a further occurrence is a regression.

Snoozing never stops ingest; the counts keep moving and `is:snoozed` lists what is quiet. To stop *recording* something, use a drop rule.

## Grouping

An occurrence becomes an issue through a fingerprint, and neither door puts anything per-instance in one.

**An issue is one fingerprint in one project.** Environment is a filter and a tag, not part of its identity — the same fault in staging and in production is one issue with one triage state, so resolving it resolves it. Every environment it has been seen in is recorded with its own first-seen, last-seen and count; `environment:` matches any of them and the issue page lists them all. An install that predates this had one row per environment; `migrate` folds them together, keeping the most open triage state, summing the counts and spanning the window. `python manage.py merge_issues --dry-run` prints exactly what that will do before it happens.

Alertmanager alerts group on their labels minus a denylist. The seeded rule drops `pod`, `instance`, `container`, `endpoint`, `replicaset`, `uid`, `node` and `job_name` — the last is the run name kube-state-metrics stamps on `KubeJobFailed`, so without it every failed CronJob run minted its own issue. Rules are editable in the admin: denylist or allowlist, optionally scoped to one project and one `alertname` pattern, lowest priority number first.

SDK events group on the stack signature — the exception's module and class, then the module and function of the culprit frame, the last one marked `in_app`. Two things are deliberately absent. The line number, because it moves with every deploy that touches the file above it. The exception value, because it carries the URL or id that failed and would mint one issue per value. An event with no exception groups on its logger and its **logentry template** (`fetch failed for source %s`), not on the formatted line, which names one source. A client that wants a finer split sets `scope.fingerprint`; `{{ default }}` expands to the derived parts.

Title, culprit and level follow the issue's most recent event. The title is built from the invariants — `HTTPError: listopad.core.transport in get_json` — so it describes the group rather than whichever event opened it. The varying detail stays on the event's `message`, which is what `/issues/<id>/events` returns.

**Every issue records why it groups the way it does** — a rule, the built-in denylist, the stack signature, a log template, the message, or a fingerprint the client sent — and the issue page says so beside the fingerprint, linking to the rule when there was one. That is the first thing anyone needs when the grouping is wrong.

### Rules that read the payload

A rule can match on more than an `alertname`. `conditions` is a tree of leaves and `all` / `any` / `none` branches:

```yaml
grouping_rules:
  - priority: 10
    conditions:
      all:
        - path: labels.namespace
          op: eq
          value: payments
        - any:
            - path: exceptions.*.frames.*.filename
              op: startswith
              value: src/payments/
            - path: request.url
              op: contains
              value: /checkout
    fingerprint: ["{{ default }}", "{{ tags.tenant }}"]
    title_template: "checkout broke for {{ tags.tenant }}"
```

`path` is dot notation into the occurrence, with `*` standing for any element — so `exceptions.*.frames.*.filename` asks about any frame of any exception. Fourteen operators: `eq`, `ne`, `contains`, `not_contains`, `startswith`, `endswith`, `regex_match`, `regex_not_match`, `gt`, `gte`, `lt`, `lte`, `exists`, `not_exists`. The negative forms need every value to agree; the rest need one. A condition that cannot be evaluated skips its rule rather than taking ingest down.

**`fingerprint` refines rather than replaces.** `{{ default }}` expands to whatever the built-in algorithm computed, so a rule can say *keep the default, then split by tenant* instead of throwing the algorithm away. Any other part is a template over the same paths. **`title_template`** names the issue in the words a team uses. Both apply to either door; on the SDK door a rule is only considered when it declares one of them or a condition, so an Alertmanager label rule never claims a stack trace.

### Paths that move between machines

A venv, a container layout and an nvm prefix put the same file at three addresses, and grouping on the address splits one issue into three. Path rules rewrite the frame path **before** grouping, in order, with backreferences:

```yaml
path_rules:
  - name: venv
    pattern: "^.*/(lib/python3\\.\\d+/site-packages/)"
    replacement: "<venv>/\\1"
```

The event keeps the real path. Only the key is rewritten. Sentry's stack-trace rules can mark a frame in-app but cannot do this.

### Values that move between occurrences

`PANDORA_GROUPING_NORMALISE` (off) strips what changes between two occurrences of one fault out of the grouping key: UUIDs, URLs, emails, IPv4 and IPv6 addresses, ISO timestamps, hex strings of eight characters or more, and numbers that stand as their own token — `v1` and `http2` are names and survive, the `47` in `retry 47` does not. **It changes every hash**, so it is off until an operator has run `regroup --dry-run` against real data. The values stay on the event.

This is what ML grouping is competing with, and it is deterministic, inspectable and needs no corpus.

### Merging what a rule did not catch

Select two or more issues in the stream and merge them. The oldest survives, the counts and history fold into it, and **the merged fingerprints become aliases** — the next occurrence of any of them lands on the surviving issue instead of minting the old one back. Sentry's merges do not do that, which is why an issue merged there returns on the next event.

Unmerging removes the alias, so the fingerprint opens its own issue again. What was already counted stays where the merge put it: the events behind it may have been pruned, and pretending to un-mix history would be a lie.

**A merge is a labelled example**, so the issue page reads it back: what the merged issues shared, what they differed on, and that a grouping rule denying the difference would have made the merge unnecessary.

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

Each pass reads `GET /api/v2/alerts` (asking for silenced, inhibited and unprocessed alerts as well — a suppressed alert is still firing) and compares it with the episodes Pandora holds open:

- an alert with no open episode → the missed webhook is synthesised and replayed through the same envelope inbox and consumer the webhook path uses;
- an open episode whose alert is absent → closed only after **three consecutive** misses, so an Alertmanager restart cannot manufacture a resolve. The counter lives in memory and resets when the process restarts, which errs towards keeping episodes open;
- a `Watchdog` alert → `pandora_watchdog_last_seen_timestamp`, the dead-man's switch that watches the alert path itself.

Scope comes from the Alertmanager ingest token — its project and environment. Pass `--project` and `--environment` when one Pandora serves more than one cluster. `--metrics-port` exposes the gauge from the reconcile process, which has no web port of its own.

Without `--loop` it runs a single pass and exits. Deploy it with `--loop`, not as a CronJob: the miss counter lives in the process, so a one-shot run catches up missed webhooks but can never reach a third consecutive miss. A database error ends the process rather than being swallowed — the restart brings a working connection back, which a wedged loop never would.

Reconcile never writes issues directly. Everything it corrects goes through `RawEnvelope` and `process_envelope`, so counters stay exactly-once and a correction is as replayable as a delivery.

`GET /api/v2/alerts` returns every alert Alertmanager holds, including the ones its own routing tree sends to a blackhole receiver — routing decides notification, not membership. Reconcile would otherwise resurrect exactly what the route drops. `PANDORA_RECONCILE_IGNORE` (default `Watchdog,InfoInhibitor`) is the list it will not open an episode for; the `Watchdog` gauge is still stamped before the filter runs, because that reading is the point of the alert. Each pass reports how many it skipped.

### Silences

The issue stream and the issue page can silence for 1h, 4h or 1d. Matchers are structured and exact — one `isEqual` matcher per retained grouping label, no regex — so a silence covers the issue and nothing else. An issue that kept no grouping labels is refused rather than turned into a silence that matches everything. The comment links back to the issue; set `PANDORA_BASE_URL` to make that link absolute. Each silence is recorded as a `SilenceLink` and shows in the issue's activity feed; the link list can lift a silence early, and `prune` drops links once they expire.

## JSON API

Read-only and versioned from the first commit — `/api/v1/` is what consumers pin. Paths carry no trailing slash. One header authenticates:

```
Authorization: Bearer <IngestToken with scope=read>
```

A token belongs to one project and every response is scoped to it: another project's issues are absent from the list and answer 404 by id. A token with the wrong scope gets 403, an unknown or deactivated one 401, an unsafe verb 405. Errors are `{"detail": "..."}` with the matching status.

Two read scopes. `read` returns the issue and everything around it with the event `payload` and `extra` blanked; `payload` returns the stored event whole. Reading an issue and reading what an event carried — a request body, a user, frame locals — are different permissions, so a dashboard or an agent can have the first without the second. The MCP tools honour the same split.

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
      "environments": ["p-mk1", "p-mk2"],
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
      "environment": "p-mk1",
      "payload": {}
    }
  ],
  "next_cursor": "01J8ZQ7X4N0000000000000001"
}
```

`payload` holds the normalised Sentry interfaces of an SDK event — `exceptions`, `threads`, `breadcrumbs`, `user`, `request`, `contexts`, `sdk`, `modules`, `logentry`, `debug_images`, `extra`, and the scalars (`release`, `dist`, `server_name`, `transaction`, `platform`). It is `{}` for an Alertmanager occurrence, which has no stack trace, and for anything stored before 0.7.0.

## License

[FSL-1.1-ALv2](LICENSE): use, modify and redistribute it for anything except a commercial product that competes with Pandora. Each release turns into Apache 2.0 two years after it ships.

It comes as is, with no warranty of any kind and no liability on Sophotech s.r.o. for what it does. The image carries a copy at `/app/LICENSE`.

Sentry's own server is source-available under a licence that forbids competing use. Pandora derives nothing from it — the wire format is reimplemented from public protocol documentation, as the ingest section above says.
