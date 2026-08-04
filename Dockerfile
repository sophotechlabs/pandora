FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 pandora \
    && useradd --uid 1000 --gid pandora --create-home --shell /bin/bash pandora \
    && mkdir -p /app /opt/venv \
    && chown pandora:pandora /app /opt/venv

WORKDIR /app


FROM base AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.33 /uv /usr/local/bin/uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

USER pandora

COPY --chown=pandora:pandora pyproject.toml uv.lock ./
RUN uv sync --frozen --extra web --no-install-project

COPY --chown=pandora:pandora manage.py ./
COPY --chown=pandora:pandora src/ ./src/
RUN uv sync --frozen --extra web


FROM builder AS dev

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
USER pandora

RUN uv sync --frozen --extra web --extra dev

COPY --chown=pandora:pandora . .

EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]


FROM base AS prod

COPY --from=builder --chown=root:root /opt/venv /opt/venv
RUN chown root:root /opt/venv
COPY pyproject.toml manage.py ./
COPY docker/ ./docker/
COPY src/ ./src/

RUN mkdir -p /app/staticfiles \
    && chown pandora:pandora /app/staticfiles \
    && DJANGO_DEBUG=False \
       DJANGO_SECRET_KEY=build-time-only \
       DJANGO_ALLOWED_HOSTS=localhost \
       DATABASE_URL=sqlite:///build.sqlite3 \
       python manage.py collectstatic --noinput \
    && rm -f /app/build.sqlite3

USER pandora

EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "-c", "/app/docker/gunicorn.conf.py", "pandora.web.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "60"]
