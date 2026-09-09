import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("issues", "0016_user_report"),
    ]

    operations = [
        migrations.AddField(
            model_name="issue",
            name="user_count",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="issue",
            name="users_capped",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="IssueUser",
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
                ("key", models.CharField(max_length=200)),
                ("first_seen", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "issue",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="affected_users",
                        to="issues.issue",
                    ),
                ),
            ],
            options={
                "ordering": ("key",),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("issue", "key"), name="issues_issue_user_uq"
                    )
                ],
            },
        ),
    ]
