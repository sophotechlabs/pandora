compose_local := "docker compose -f docker-compose.yml -f docker-compose.local.yml"
compose_e2e := "docker compose -f docker-compose.yml -f docker-compose.e2e.yml"
compose_live := "docker compose -f docker-compose.yml -f docker-compose.live.yml"
quickstart_name := "pandora-quickstart"
quickstart_image := "pandora:quickstart"
quickstart_volume := "pandora-quickstart-data"
chart := "deploy/helm/pandora"
kind_cluster := env_var_or_default("PANDORA_KIND_CLUSTER", env_var_or_default("SPINOZA_KIND_CLUSTER", "pandora-ci"))
kind_context := "kind-" + kind_cluster
kind_namespace := env_var_or_default("PANDORA_KIND_NAMESPACE", "pandora-kind")
kind_release := env_var_or_default("PANDORA_KIND_RELEASE", "pandora")
kind_repository := env_var_or_default("PANDORA_KIND_REPOSITORY", "pandora")
kind_image := kind_repository + ":kind"
image_tag := env_var_or_default("PANDORA_IMAGE_TAG", file_name(justfile_directory()))
ci_compose_run := "docker compose run --rm --no-deps"
ci_compose_run_deps := "docker compose run --rm"
hadolint_image := "hadolint/hadolint:latest-debian"
trivy_image := "aquasec/trivy:latest"
trivy_common := "--exit-code 1 --severity HIGH,CRITICAL --ignore-unfixed"
pg_test_url := "postgres://pandora:pandora@db:5432/pandora"

default:
    @just --list

# Install pandora with web + dev extras into active venv
install:
    uv pip install -e ".[web,dev]"

# One container, sqlite on a volume, no compose and no .env — the fastest look
quickstart:
    #!/usr/bin/env bash
    set -euo pipefail
    port="${PANDORA_WEB_PORT:-8000}"
    password="${PANDORA_QUICKSTART_PASSWORD:-$(head -c 12 /dev/urandom | base64 | tr -d '/+=')}"
    docker build -q --target prod -t {{ quickstart_image }} .
    docker rm -f {{ quickstart_name }} >/dev/null 2>&1 || true
    docker volume create {{ quickstart_volume }} >/dev/null
    docker run -d --name {{ quickstart_name }} \
        -p "127.0.0.1:$port:8000" \
        -v {{ quickstart_volume }}:/data \
        -e DJANGO_DEBUG=False \
        -e DJANGO_SECURE_COOKIES=0 \
        -e DJANGO_SECRET_KEY="$(head -c 32 /dev/urandom | base64)" \
        -e DJANGO_ALLOWED_HOSTS="localhost,127.0.0.1" \
        -e DJANGO_CSRF_TRUSTED_ORIGINS="http://localhost:$port,http://127.0.0.1:$port" \
        -e DATABASE_URL=sqlite:////data/pandora.sqlite3 \
        -e PANDORA_RUN_MIGRATIONS=1 \
        -e DJANGO_SUPERUSER_USERNAME=admin \
        -e DJANGO_SUPERUSER_EMAIL=admin@example.test \
        -e DJANGO_SUPERUSER_PASSWORD="$password" \
        {{ quickstart_image }} >/dev/null
    echo ""
    echo "Pandora is starting on http://127.0.0.1:$port/"
    echo "Sign in as admin / $password"
    echo "Stop it with: just quickstart-down"

# Serve the read-only MCP tools over stdio (needs the mcp extra)
mcp:
    python manage.py mcp

# Remove the quickstart container and its volume
quickstart-down:
    -docker rm -f {{ quickstart_name }}
    -docker volume rm {{ quickstart_volume }}

# Start full docker stack (rebuilds image; entrypoint runs migrations)
up:
    {{ compose_local }} up -d --build

# Start stack without rebuilding (faster; use if image is current)
up-nobuild:
    {{ compose_local }} up -d

# Start stack in foreground (useful for logs)
up-fg:
    {{ compose_local }} up --build

# First-run setup: build + start; web's entrypoint runs migrations, --wait blocks until healthy
bootstrap:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ compose_local }} up -d --wait --build
    echo ""
    echo "Stack healthy. Open $(just url)"
    echo "If you need a superuser: just superuser"

# Print the URL the web container is published on
url:
    #!/usr/bin/env bash
    set -euo pipefail
    address=$(docker compose port web 8000 2>/dev/null || true)
    if [ -z "$address" ]; then
        echo "web is not running — just up" >&2
        exit 1
    fi
    echo "http://$address/"

# Print the postgres URL the db container is published on
dburl:
    #!/usr/bin/env bash
    set -euo pipefail
    address=$(docker compose port db 5432 2>/dev/null || true)
    if [ -z "$address" ]; then
        echo "db is not published — just up, or set PANDORA_DB_PORT" >&2
        exit 1
    fi
    echo "postgres://pandora:pandora@$address/pandora"

# Stop and remove containers (keeps volumes; backs up DB first)
down: backup
    docker compose down

# Stop and remove containers AND volumes (destroys DB; backs up first)
clean-volumes: backup
    docker compose down -v

# Restart the web service
restart:
    docker compose restart web

# Tail logs for all services
logs:
    docker compose logs -f

# Tail logs for web only
logs-web:
    docker compose logs -f web

# Show running containers
ps:
    docker compose ps

# Open a bash shell inside the web container
shell:
    docker compose exec web bash

# Open a Django shell inside the web container
web-shell:
    docker compose exec web python manage.py shell

# Open a psql shell to the db container
dbshell:
    docker compose exec db psql -U pandora -d pandora

# Dump DB to ./backups/pandora-<ts>.dump; skips if db not running; keeps 5 newest
backup:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p backups
    docker compose exec -T db true 2>/dev/null || { echo "db not running — skipping backup"; exit 0; }
    TS=$(date -u +%Y%m%d-%H%M%S)
    docker compose exec -T db pg_dump -U pandora -Fc pandora > "backups/pandora-$TS.dump"
    echo "Wrote backups/pandora-$TS.dump"
    ls -t backups/pandora-*.dump 2>/dev/null | tail -n +6 | xargs -I{} rm -f -- {}

# Restore from backup file (usage: just restore backups/pandora-XXXX.dump)
restore file:
    docker compose exec -T db pg_restore -U pandora -d pandora --clean --if-exists < {{ file }}
    @echo "Restored from {{ file }}"

# List backups newest-first
backup-ls:
    @ls -lht backups/*.dump 2>/dev/null || echo "No backups yet"

# Apply Django migrations inside the web container
migrate:
    docker compose exec web python manage.py migrate

# Generate new Django migrations inside the web container
makemigrations:
    docker compose exec web python manage.py makemigrations

# Create a Django superuser inside the web container
superuser:
    docker compose exec web python manage.py createsuperuser

# Replace demo data (projects, issues, episodes, aggregates) inside the web container
seed-demo:
    docker compose exec web python manage.py seed_demo

# Collect static files inside the web container
collectstatic:
    docker compose exec web python manage.py collectstatic --noinput

# Build docker images
build:
    docker compose build

# Rebuild docker images with no cache
rebuild:
    docker compose build --no-cache

# Run Django dev server locally (no docker)
runserver:
    python manage.py runserver

# Run the full pytest suite in an ephemeral container (auto-starts deps)
test:
    docker compose run --rm --entrypoint pytest web

# Run pytest locally (no docker)
test-local:
    pytest

# Run pytest with coverage report to terminal (SQLite only — the gate lives in `just ci`)
coverage:
    pytest --cov --cov-report=term-missing --cov-fail-under=0

# Generate HTML coverage report (SQLite only — the gate lives in `just ci`)
coverage-html:
    pytest --cov --cov-report=html --cov-fail-under=0
    @echo "open htmlcov/index.html"

# Ruff check locally (no docker) — quick dev feedback
lint:
    ruff check src tests e2e

# Auto-format with ruff locally (no docker)
format:
    ruff check --fix src tests e2e
    ruff format src tests e2e

# Remove Python artifacts and caches (not volumes)
clean:
    find . -type d -name __pycache__ -prune -exec rm -rf {} +
    find . -type f -name '*.pyc' -delete
    rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage coverage.xml htmlcov staticfiles src/pandora.egg-info

# Full CI in Docker (no image scan)
ci: ci-image ci-lint ci-format-check ci-typecheck ci-djlint ci-migration-lint ci-security ci-docker-lint ci-test ci-test-pg

# Inner dev loop — lint + types + tests on sqlite
ci-fast: ci-image ci-lint ci-typecheck ci-test

# Full CI + docker image CVE scan (slower)
ci-docker: ci ci-docker-scan

# Ensure the CI image is built (no-op if cached)
ci-image:
    docker compose build web

# ruff check (lint rules) — in docker
ci-lint:
    {{ ci_compose_run }} --entrypoint ruff web check src tests e2e

# ruff format --check — in docker
ci-format-check:
    {{ ci_compose_run }} --entrypoint ruff web format --check src tests e2e

# mypy with django-stubs — in docker
ci-typecheck:
    {{ ci_compose_run }} --entrypoint mypy web --config-file pyproject.toml

# djlint Django templates — in docker (no-op if no templates)
ci-djlint:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -n "$(find src -name '*.html' -print -quit)" ]; then
        {{ ci_compose_run }} --entrypoint djlint web src --check
    else
        echo "ci-djlint: no .html templates in src/ — skipping"
    fi

# django-migration-linter — safe migrations gate (config in pyproject.toml)
ci-migration-lint:
    {{ ci_compose_run_deps }} --entrypoint python web manage.py makemigrations --check --dry-run
    {{ ci_compose_run_deps }} --entrypoint python web manage.py lintmigrations

# pytest against SQLite (the default backend, no services needed)
ci-test *args:
    {{ ci_compose_run }} --entrypoint pytest web {{ args }}

# pytest against postgres — runs both event stores, so this is the run that gates coverage
ci-test-pg:
    {{ ci_compose_run_deps }} -e TEST_DATABASE_URL={{ pg_test_url }} --entrypoint pytest web --cov --cov-report=term-missing --cov-report=xml

ci-test-pg-focus *args:
    {{ ci_compose_run_deps }} -e TEST_DATABASE_URL={{ pg_test_url }} --entrypoint pytest web {{ args }}

# pip-audit dependency CVE scan (installed env; skip editable self)
ci-security:
    #!/usr/bin/env bash
    set -euo pipefail
    docker compose up --no-start --no-deps --no-build web
    image_id="$(docker compose images -q web)"
    trap 'docker compose rm --stop --force web >/dev/null' EXIT
    docker run --rm --network host --entrypoint pip-audit "$image_id" --skip-editable

# hadolint the Dockerfile — fail only on error-level findings
ci-docker-lint:
    docker run --rm -i {{ hadolint_image }} hadolint --failure-threshold error - < Dockerfile

# Build image tagged pandora-web:<checkout> for scanning
ci-image-build:
    docker build --network host -t pandora-web:{{ image_tag }} .

# Scan built image for CVEs (high/critical, fixable only)
ci-docker-scan: ci-image-build
    docker run --rm --network host -v /var/run/docker.sock:/var/run/docker.sock \
        {{ trivy_image }} image {{ trivy_common }} pandora-web:{{ image_tag }}

# Scan filesystem for CVEs, secrets, IaC misconfigs
ci-fs-scan:
    docker run --rm --network host -v "$PWD":/src {{ trivy_image }} \
        fs {{ trivy_common }} --scanners vuln,secret,misconfig /src

# django-upgrade dry-run (informational — shows modernization opportunities)
ci-upgrade-check:
    {{ ci_compose_run }} --entrypoint sh web -c 'django-upgrade --target-version 6.0 $(find src -name "*.py")'

# Auto-fix everything auto-fixable (ruff, djlint, django-upgrade)
ci-fix:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ ci_compose_run }} --entrypoint ruff web check --fix src tests e2e
    {{ ci_compose_run }} --entrypoint ruff web format src tests e2e
    {{ ci_compose_run }} --entrypoint sh web -c 'django-upgrade --target-version 6.0 $(find src -name "*.py")'
    if [ -n "$(find src -name '*.html' -print -quit)" ]; then
        {{ ci_compose_run }} --entrypoint djlint web src --reformat
    fi

# End-to-end: a real browser against the running stack (optional extra, not in default ci)
ci-e2e *args:
    {{ compose_e2e }} build e2e
    {{ compose_e2e }} run --rm e2e {{ args }}

# Tear down the e2e stack and its volumes
ci-e2e-down:
    {{ compose_e2e }} down -v

# Real SDKs, real shippers, a real Alertmanager, read back off the pages
ci-live: ci-live-up ci-live-clients ci-live-verify

# Bring the live stack up on an empty database and seed its keys
ci-live-up:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ compose_live }} down -v --remove-orphans
    {{ compose_live }} build
    {{ compose_live }} up -d --wait db web
    {{ compose_live }} run --rm --no-deps web python manage.py apply_config --path live/config.yaml
    {{ compose_live }} up -d --wait alertmanager
    {{ compose_live }} up -d vector otelcol

# Run every real client against the live stack
ci-live-clients:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ compose_live }} run --rm sdk-python
    {{ compose_live }} run --rm sdk-python-crash
    {{ compose_live }} run --rm sdk-node
    set +e
    {{ compose_live }} run --rm wrap
    status=$?
    set -e
    if [ "$status" -ne 3 ]; then
        echo "pandora-wrap returned $status, expected the wrapped command's 3" >&2
        exit 1
    fi

# Feed the shippers, fire the alerts, then read everything back
ci-live-verify:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ compose_live }} run --rm produce
    {{ compose_live }} run --rm live

# Just the JavaScript client, for iterating on the upload protocol
ci-live-node:
    {{ compose_live }} run --build --rm sdk-node

# Print what a live run stored — pass a title fragment for one event's payload
ci-live-dump fragment="":
    {{ compose_live }} run --build --rm --no-deps --entrypoint python live live/dump.py "{{ fragment }}"

# Tear down the live stack and its volumes
ci-live-down:
    {{ compose_live }} down -v --remove-orphans

# What the live stack logged, for a run that went wrong
ci-live-logs:
    {{ compose_live }} logs --no-color --tail 120

kind-up:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! kind get clusters | grep -qx {{ kind_cluster }}; then
        kind create cluster --name {{ kind_cluster }} --config e2e/kind.yaml --wait 5m
    fi
    kind export kubeconfig --name {{ kind_cluster }}
    docker exec {{ kind_cluster }}-control-plane \
        mkdir -p /var/local/pandora-kind
    docker exec {{ kind_cluster }}-control-plane \
        chown -R 1000:1000 /var/local/pandora-kind
    kubectl --context {{ kind_context }} apply -f e2e/kind-storage.yaml

kind-image: kind-up
    docker build --network host --target prod -t {{ kind_image }} .
    kind load docker-image {{ kind_image }} --name {{ kind_cluster }}

kind-install: kind-image
    helm upgrade --install {{ kind_release }} {{ chart }} \
        --kube-context {{ kind_context }} \
        --namespace {{ kind_namespace }} \
        --create-namespace \
        --timeout 5m \
        --set image.repository={{ kind_repository }} \
        --set image.tag=kind \
        --set image.pullPolicy=Never \
        --set host=localhost \
        --set persistence.size=1Gi \
        --set persistence.storageClass=pandora-kind \
        --set settings.secureCookies=false \
        --set-string podAnnotations.kind-build="$(date +%s)" \
        --set secrets.secretKey=pandora-kind-secret-key-that-is-only-for-tests \
        --set superuser.password=pandora-kind-password
    kubectl --context {{ kind_context }} --namespace {{ kind_namespace }} \
        rollout status deployment/{{ kind_release }}-pandora --timeout=5m

ci-kind-smoke: kind-install
    uv sync --frozen --extra web --extra e2e
    PANDORA_KIND_CONTEXT={{ kind_context }} \
        PANDORA_KIND_NAMESPACE={{ kind_namespace }} \
        PANDORA_KIND_RELEASE={{ kind_release }} \
        PANDORA_KIND_IMAGE={{ kind_image }} \
        uv run python e2e/kind_lifecycle.py smoke

ci-kind-full: kind-install
    uv sync --frozen --extra web --extra e2e
    PANDORA_KIND_CONTEXT={{ kind_context }} \
        PANDORA_KIND_NAMESPACE={{ kind_namespace }} \
        PANDORA_KIND_RELEASE={{ kind_release }} \
        PANDORA_KIND_IMAGE={{ kind_image }} \
        uv run python e2e/kind_lifecycle.py full

ci-kind-logs:
    #!/usr/bin/env bash
    set -euo pipefail
    kubectl --context {{ kind_context }} -n {{ kind_namespace }} get all,pvc -o wide
    kubectl --context {{ kind_context }} get pv -o wide
    kubectl --context {{ kind_context }} -n {{ kind_namespace }} get events --sort-by=.lastTimestamp
    kubectl --context {{ kind_context }} -n {{ kind_namespace }} logs deployment/{{ kind_release }}-pandora --tail=200

ci-kind-down:
    kind delete cluster --name {{ kind_cluster }}

# Print what the stack logged and stop — for a CI runner with no terminal to tail
logs-once:
    {{ compose_e2e }} logs --no-color --tail 200

# Everything a GitHub runner runs, on the host toolchain rather than in compose
gh: gh-lint gh-migrations gh-audit gh-test gh-test-pg chart-lint gh-dockerfile gh-go

# The command wrapper: vet, format check and tests
gh-go:
    gofmt -l cmd
    go vet ./cmd/...
    go test ./cmd/... -cover

# Build the wrapper for the platforms the release ships
wrap-build:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p dist/release
    version="${PANDORA_VERSION:-dev}"
    for target in linux/amd64 linux/arm64 darwin/amd64 darwin/arm64; do
        os="${target%/*}"
        arch="${target#*/}"
        CGO_ENABLED=0 GOOS="$os" GOARCH="$arch" go build \
            -trimpath -ldflags "-s -w" \
            -o "dist/release/pandora-wrap_${version}_${os}_${arch}" ./cmd/pandora-wrap
    done
    ls -1 dist/release/pandora-wrap_*

# Install the project from the lockfile with both extras
deps:
    uv sync --frozen --extra web --extra dev

# ruff, formatting, types and templates on the host
gh-lint: deps
    uv run ruff check src tests e2e
    uv run ruff format --check src tests e2e
    uv run mypy --config-file pyproject.toml
    uv run djlint src --check

# migrations are in step with the models, and safe to apply
gh-migrations: deps
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -z "${DATABASE_URL:-}" ]; then
        echo "gh-migrations needs DATABASE_URL: the linter's rules differ by backend, and sqlite's are not the ones a deployment runs" >&2
        exit 1
    fi
    DJANGO_DEBUG=True uv run python manage.py makemigrations --check --dry-run
    DJANGO_DEBUG=True uv run python manage.py lintmigrations

# dependency CVEs
gh-audit: deps
    uv run pip-audit --skip-editable

# pytest against sqlite, the default backend
gh-test: deps
    uv run pytest

# pytest against postgres — both event stores, so this run gates coverage
gh-test-pg: deps
    uv run pytest --cov --cov-report=term-missing --cov-report=xml

# hadolint on the host
gh-dockerfile:
    hadolint --failure-threshold error Dockerfile

# Conventional Commits on whatever range CI is looking at
commits:
    #!/usr/bin/env bash
    set -euo pipefail
    from=$(node -p "try { const e = require(process.env.GITHUB_EVENT_PATH); (e.pull_request ? e.pull_request.base.sha : e.before) || '' } catch (e) { '' }")
    if [ -z "$from" ] || ! git cat-file -e "$from^{commit}" 2>/dev/null; then
        from=HEAD~1
    fi
    npx --yes --package @commitlint/cli@21.2.2 --package @commitlint/config-conventional@21.2.2 commitlint --from "$from" --to HEAD

# Credentials in the tree and in the history
secrets:
    gitleaks dir . --no-banner --redact -v
    gitleaks git . --no-banner --redact -v

# Static analysis of the application code
sast:
    semgrep scan --config p/python --config p/django --config p/secrets \
        --config .semgrep.yml \
        --exclude-rule python.lang.security.insecure-hash-algorithms.insecure-hash-algorithm-sha1 \
        --error --quiet src

# Known vulnerabilities and misconfiguration in the tree
vulns:
    trivy fs --exit-code 1 --scanners secret,misconfig --ignorefile .trivyignore.yaml .
    osv-scanner scan source --recursive .

# The workflow files themselves
workflows:
    yamllint .forgejo .github
    actionlint -config-file .forgejo/actionlint.yaml .forgejo/workflows/*.yaml
    actionlint .github/workflows/*.yaml
    zizmor --no-online-audits --config .forgejo/zizmor.yml .forgejo/workflows/*.yaml
    zizmor --no-online-audits .github/workflows/*.yaml

# Spelling, whitespace, shell and the justfile's own formatting
hygiene:
    typos
    just editorconfig
    shellcheck docker/entrypoint.sh live/node/run.sh
    just --unstable --fmt --check

# editorconfig-checker ships under two names depending on how it was installed
editorconfig:
    #!/usr/bin/env bash
    set -euo pipefail
    for name in ec editorconfig-checker; do
        if command -v "$name" > /dev/null 2>&1; then
            exec "$name"
        fi
    done
    echo "editorconfig-checker is not installed" >&2
    exit 1

# A bill of materials for the tree, and its known vulnerabilities
sbom:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p dist
    syft scan dir:. --source-name pandora --exclude './.venv/**' --exclude './dist/**' --exclude './htmlcov/**' --output cyclonedx-json=dist/sbom.cdx.json
    grype sbom:dist/sbom.cdx.json --fail-on medium

# helm lint + kubeconform over the chart (optional extra — needs helm and kubeconform)
chart-lint:
    helm lint {{ chart }}
    helm template pandora {{ chart }} | kubeconform -strict -summary -ignore-missing-schemas

# Render the chart with the defaults
chart-template *args:
    helm template pandora {{ chart }} {{ args }}

# vulture — dead code detection (optional extra, not in default ci)
ci-deadcode:
    {{ ci_compose_run }} --entrypoint vulture web src --min-confidence 80 \
        --ignore-names sender,organization,model_admin,credentials

# xenon — fail on cyclomatic complexity regressions (optional extra)
ci-complexity:
    {{ ci_compose_run }} --entrypoint xenon web --max-absolute C --max-modules C --max-average A src
