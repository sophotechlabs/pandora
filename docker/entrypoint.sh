#!/usr/bin/env bash
set -euo pipefail

if [ -n "${PROMETHEUS_MULTIPROC_DIR:-}" ]; then
    mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
    find "$PROMETHEUS_MULTIPROC_DIR" -type f -name '*.db' -delete
fi

if [ "${PANDORA_RUN_MIGRATIONS:-}" = "1" ]; then
    python manage.py migrate --noinput
    python manage.py ensure_superuser
fi

exec "$@"
