import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_project_auto_resolve"),
        ("scrub", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="InboundFilter",
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
                    "kind",
                    models.CharField(
                        choices=[
                            ("browser_extension", "Browser extension"),
                            ("legacy_browser", "Legacy browser"),
                            ("localhost", "Localhost"),
                            ("web_crawler", "Web crawler"),
                            ("health_check", "Health check"),
                            ("chunk_load", "Chunk load"),
                            ("allowed_domains", "Allowed domains"),
                        ],
                        max_length=32,
                    ),
                ),
                ("options", models.JSONField(blank=True, default=dict)),
                ("active", models.BooleanField(default=True)),
                ("dropped", models.PositiveBigIntegerField(default=0)),
                (
                    "project",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="inbound_filters",
                        to="core.project",
                    ),
                ),
            ],
            options={
                "ordering": ("kind",),
                "indexes": [
                    models.Index(fields=["active"], name="scrub_inbound_active")
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("project", "kind"), name="scrub_inbound_project_kind_uq"
                    )
                ],
            },
        ),
    ]
