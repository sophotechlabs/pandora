import datetime

from django.db import migrations

SQLITE_TABLE = """
CREATE TABLE events_event (
    id text NOT NULL PRIMARY KEY,
    project_id integer NOT NULL,
    issue_id integer NULL,
    episode_id text NULL,
    fingerprint text NOT NULL DEFAULT '[]',
    "timestamp" text NOT NULL,
    level text NOT NULL,
    message text NOT NULL,
    tags text NOT NULL DEFAULT '{}',
    extra text NOT NULL DEFAULT '{}',
    source text NOT NULL,
    environment text NOT NULL DEFAULT ''
)
"""

POSTGRES_TABLE = """
CREATE TABLE events_event (
    id text NOT NULL,
    project_id bigint NOT NULL,
    issue_id bigint NULL,
    episode_id text NULL,
    fingerprint jsonb NOT NULL DEFAULT '[]'::jsonb,
    "timestamp" timestamptz NOT NULL,
    level text NOT NULL,
    message text NOT NULL,
    tags jsonb NOT NULL DEFAULT '{}'::jsonb,
    extra jsonb NOT NULL DEFAULT '{}'::jsonb,
    source text NOT NULL,
    environment text NOT NULL DEFAULT '',
    PRIMARY KEY (id, "timestamp")
) PARTITION BY RANGE ("timestamp")
"""

INDEXES = (
    'CREATE INDEX events_event_proj_issue ON events_event '
    '(project_id, issue_id, "timestamp" DESC)',
    'CREATE INDEX events_event_proj_ts ON events_event '
    '(project_id, "timestamp" DESC)',
    "CREATE INDEX events_event_episode ON events_event (episode_id)",
)

PARTITION = (
    "CREATE TABLE IF NOT EXISTS events_event_{suffix} "
    "PARTITION OF events_event FOR VALUES FROM ('{start}') TO ('{end}')"
)

MONTHS_BEHIND = 1
MONTHS_AHEAD = 2


def month_start(moment, offset):
    month = moment.month - 1 + offset
    year = moment.year + month // 12
    return datetime.date(year, month % 12 + 1, 1)


def create_events_table(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor == "postgresql":
        schema_editor.execute(POSTGRES_TABLE)
        today = datetime.date.today()
        for offset in range(-MONTHS_BEHIND, MONTHS_AHEAD + 1):
            start = month_start(today, offset)
            end = month_start(today, offset + 1)
            schema_editor.execute(
                PARTITION.format(
                    suffix=f"{start.year}_{start.month:02d}",
                    start=start.isoformat(),
                    end=end.isoformat(),
                )
            )
    else:
        schema_editor.execute(SQLITE_TABLE)
    for statement in INDEXES:
        schema_editor.execute(statement)


def drop_events_table(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("DROP TABLE IF EXISTS events_event CASCADE")
    else:
        schema_editor.execute("DROP TABLE IF EXISTS events_event")


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.RunPython(create_events_table, drop_events_table),
    ]
