import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("issues", "0019_issue_escalated_at"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Subscription",
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
                (
                    "reason",
                    models.CharField(
                        choices=[
                            ("commented", "Commented"),
                            ("assigned", "Assigned"),
                            ("triaged", "Acted on it"),
                            ("manual", "Chose to watch"),
                        ],
                        default="manual",
                        max_length=16,
                    ),
                ),
                ("active", models.BooleanField(default=True)),
                ("at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "issue",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="subscriptions",
                        to="issues.issue",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="subscriptions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("user__username",),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("issue", "user"), name="issues_subscription_uq"
                    )
                ],
            },
        ),
    ]
