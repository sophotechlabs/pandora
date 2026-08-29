from django.db import migrations

SQLITE_ADD = "ALTER TABLE events_event ADD COLUMN payload text NOT NULL DEFAULT '{}'"
POSTGRES_ADD = (
    "ALTER TABLE events_event ADD COLUMN payload jsonb NOT NULL DEFAULT '{}'::jsonb"
)
DROP = "ALTER TABLE events_event DROP COLUMN payload"


def add_payload(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(POSTGRES_ADD)
    else:
        schema_editor.execute(SQLITE_ADD)


def drop_payload(apps, schema_editor):
    schema_editor.execute(DROP)


class Migration(migrations.Migration):
    dependencies = [("events", "0001_initial")]

    operations = [
        migrations.RunPython(add_payload, drop_payload),
    ]
