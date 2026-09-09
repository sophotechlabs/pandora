from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_project_auto_resolve"),
        ("issues", "0017_issue_users"),
    ]

    operations = [
        migrations.AddField(
            model_name="issue",
            name="needs_review",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="issue",
            name="priority",
            field=models.CharField(
                choices=[("high", "High"), ("medium", "Medium"), ("low", "Low")],
                default="medium",
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name="issue",
            name="priority_locked",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="issueactivity",
            name="kind",
            field=models.CharField(
                choices=[
                    ("created", "Created"),
                    ("snoozed", "Snoozed"),
                    ("unsnoozed", "Woke up"),
                    ("regression", "Regression"),
                    ("acknowledged", "Acknowledged"),
                    ("resolved", "Resolved"),
                    ("ignored", "Ignored"),
                    ("reopened", "Reopened"),
                    ("merged", "Merged"),
                    ("unmerged", "Unmerged"),
                    ("silenced", "Silenced"),
                    ("unsilenced", "Unsilenced"),
                    ("regrouped", "Regrouped"),
                    ("escalated", "Escalated"),
                    ("auto_resolved", "Resolved by age"),
                    ("reviewed", "Reviewed"),
                    ("reprioritised", "Priority changed"),
                    ("commented", "Commented"),
                ],
                max_length=32,
            ),
        ),
        migrations.AddIndex(
            model_name="issue",
            index=models.Index(
                fields=["project", "priority", "-last_seen"],
                name="issues_issue_priority",
            ),
        ),
        migrations.AddIndex(
            model_name="issue",
            index=models.Index(
                condition=models.Q(("needs_review", True)),
                fields=["project", "needs_review"],
                name="issues_issue_review",
            ),
        ),
    ]
