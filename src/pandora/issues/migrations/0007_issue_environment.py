import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


def backfill_environments(apps, schema_editor):
    Issue = apps.get_model("issues", "Issue")
    IssueEnvironment = apps.get_model("issues", "IssueEnvironment")
    rows = [
        IssueEnvironment(
            issue_id=issue.pk,
            name=issue.environment,
            first_seen=issue.first_seen,
            last_seen=issue.last_seen,
            event_count=issue.event_count,
        )
        for issue in Issue.objects.all().iterator()
    ]
    IssueEnvironment.objects.bulk_create(rows, batch_size=500)


def drop_environments(apps, schema_editor):
    IssueEnvironment = apps.get_model("issues", "IssueEnvironment")
    IssueEnvironment.objects.all().delete()


def fold_duplicates(apps, schema_editor):
    from pandora.issues import merge

    merge.run()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_alter_ingesttoken_scope"),
        ("notify", "0001_initial"),
        ("people", "0001_initial"),
        ("issues", "0006_issue_snoozed_past_count_issue_snoozed_until_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="IssueEnvironment",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(blank=True, default="", max_length=100)),
                ("first_seen", models.DateTimeField(default=django.utils.timezone.now)),
                ("last_seen", models.DateTimeField(default=django.utils.timezone.now)),
                ("event_count", models.PositiveBigIntegerField(default=0)),
            ],
            options={
                "ordering": ("name",),
            },
        ),
        migrations.AddField(
            model_name="issueenvironment",
            name="issue",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="environments",
                to="issues.issue",
            ),
        ),
        migrations.RunPython(backfill_environments, drop_environments),
        migrations.RunPython(fold_duplicates, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="issue",
            name="issues_issue_fingerprint_uq",
        ),
        migrations.AddConstraint(
            model_name="issue",
            constraint=models.UniqueConstraint(
                fields=("project", "fingerprint_hash"),
                name="issues_issue_fingerprint_uq",
            ),
        ),
        migrations.AddIndex(
            model_name="issueenvironment",
            index=models.Index(fields=["name"], name="issues_issue_env_name"),
        ),
        migrations.AddConstraint(
            model_name="issueenvironment",
            constraint=models.UniqueConstraint(
                fields=("issue", "name"), name="issues_issue_environment_uq"
            ),
        ),
    ]
