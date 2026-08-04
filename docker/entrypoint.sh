#!/usr/bin/env bash
set -euo pipefail

if [ "${PANDORA_RUN_MIGRATIONS:-}" = "1" ]; then
    python manage.py migrate --noinput
    python manage.py ensure_superuser
fi

exec "$@"
