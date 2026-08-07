from django.db import migrations

DEFAULT_PRIORITY = 1000
LABEL = "job_name"


def default_rules(apps, schema_editor):
    GroupingRule = apps.get_model("issues", "GroupingRule")
    return GroupingRule.objects.using(schema_editor.connection.alias).filter(
        priority=DEFAULT_PRIORITY,
        project=None,
        alertname_regex="",
        mode="denylist",
    )


def deny_job_name(apps, schema_editor):
    for rule in default_rules(apps, schema_editor):
        if LABEL in rule.labels:
            continue
        rule.labels = [*rule.labels, LABEL]
        rule.save(update_fields=["labels"])


def allow_job_name(apps, schema_editor):
    for rule in default_rules(apps, schema_editor):
        if LABEL not in rule.labels:
            continue
        rule.labels = [label for label in rule.labels if label != LABEL]
        rule.save(update_fields=["labels"])


class Migration(migrations.Migration):
    dependencies = [
        ("issues", "0004_episode_issues_episode_issue_open"),
    ]

    operations = [
        migrations.RunPython(deny_job_name, allow_job_name),
    ]
