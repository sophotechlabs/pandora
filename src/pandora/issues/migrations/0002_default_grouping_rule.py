from django.db import migrations

DEFAULT_PRIORITY = 1000
DEFAULT_DENYLIST = [
    "pod",
    "instance",
    "container",
    "endpoint",
    "replicaset",
    "uid",
    "node",
]


def seed_default_rule(apps, schema_editor):
    GroupingRule = apps.get_model("issues", "GroupingRule")
    GroupingRule.objects.create(
        priority=DEFAULT_PRIORITY,
        project=None,
        alertname_regex="",
        mode="denylist",
        labels=DEFAULT_DENYLIST,
        active=True,
    )


def drop_default_rule(apps, schema_editor):
    GroupingRule = apps.get_model("issues", "GroupingRule")
    GroupingRule.objects.filter(
        priority=DEFAULT_PRIORITY,
        project=None,
        alertname_regex="",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("issues", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_default_rule, drop_default_rule),
    ]
