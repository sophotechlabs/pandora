compose_local := "docker compose -f docker-compose.yml -f docker-compose.local.yml"
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

# Start full docker stack (rebuilds image; entrypoint runs migrations)
up:
    {{compose_local}} up -d --build

# Start stack without rebuilding (faster; use if image is current)
up-nobuild:
    {{compose_local}} up -d

# Start stack in foreground (useful for logs)
up-fg:
    {{compose_local}} up --build

# First-run setup: build + start; web's entrypoint runs migrations, --wait blocks until healthy
bootstrap:
    #!/usr/bin/env bash
    set -euo pipefail
    {{compose_local}} up -d --wait --build
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
    docker compose exec -T db pg_restore -U pandora -d pandora --clean --if-exists < {{file}}
    @echo "Restored from {{file}}"

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
    ruff check src tests

# Auto-format with ruff locally (no docker)
format:
    ruff check --fix src tests
    ruff format src tests

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
    {{ci_compose_run}} --entrypoint ruff web check src tests

# ruff format --check — in docker
ci-format-check:
    {{ci_compose_run}} --entrypoint ruff web format --check src tests

# mypy with django-stubs — in docker
ci-typecheck:
    {{ci_compose_run}} --entrypoint mypy web --config-file pyproject.toml

# djlint Django templates — in docker (no-op if no templates)
ci-djlint:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -n "$(find src -name '*.html' -print -quit)" ]; then
        {{ci_compose_run}} --entrypoint djlint web src --check
    else
        echo "ci-djlint: no .html templates in src/ — skipping"
    fi

# django-migration-linter — safe migrations gate (config in pyproject.toml)
ci-migration-lint:
    {{ci_compose_run_deps}} --entrypoint python web manage.py makemigrations --check --dry-run
    {{ci_compose_run_deps}} --entrypoint python web manage.py lintmigrations

# pytest against SQLite (the default backend, no services needed)
ci-test:
    {{ci_compose_run}} --entrypoint pytest web

# pytest against postgres — runs both event stores, so this is the run that gates coverage
ci-test-pg:
    {{ci_compose_run_deps}} -e TEST_DATABASE_URL={{pg_test_url}} --entrypoint pytest web --cov --cov-report=term-missing --cov-report=xml

# pip-audit dependency CVE scan (installed env; skip editable self)
ci-security:
    {{ci_compose_run}} --entrypoint pip-audit web --skip-editable

# hadolint the Dockerfile — fail only on error-level findings
ci-docker-lint:
    docker run --rm -i {{hadolint_image}} hadolint --failure-threshold error - < Dockerfile

# Build image tagged pandora-web:<checkout> for scanning
ci-image-build:
    docker build -t pandora-web:{{image_tag}} .

# Scan built image for CVEs (high/critical, fixable only)
ci-docker-scan: ci-image-build
    docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
        {{trivy_image}} image {{trivy_common}} pandora-web:{{image_tag}}

# Scan filesystem for CVEs, secrets, IaC misconfigs
ci-fs-scan:
    docker run --rm -v "$PWD":/src {{trivy_image}} \
        fs {{trivy_common}} --scanners vuln,secret,misconfig /src

# django-upgrade dry-run (informational — shows modernization opportunities)
ci-upgrade-check:
    {{ci_compose_run}} --entrypoint sh web -c 'django-upgrade --target-version 6.0 $(find src -name "*.py")'

# Auto-fix everything auto-fixable (ruff, djlint, django-upgrade)
ci-fix:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ci_compose_run}} --entrypoint ruff web check --fix src tests
    {{ci_compose_run}} --entrypoint ruff web format src tests
    {{ci_compose_run}} --entrypoint sh web -c 'django-upgrade --target-version 6.0 $(find src -name "*.py")'
    if [ -n "$(find src -name '*.html' -print -quit)" ]; then
        {{ci_compose_run}} --entrypoint djlint web src --reformat
    fi

# vulture — dead code detection (optional extra, not in default ci)
ci-deadcode:
    {{ci_compose_run}} --entrypoint vulture web src --min-confidence 80

# xenon — fail on cyclomatic complexity regressions (optional extra)
ci-complexity:
    {{ci_compose_run}} --entrypoint xenon web --max-absolute B --max-modules A --max-average A src
