import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_token_sources"),
        ("ingest", "0005_processed_event_issue"),
    ]

    operations = [
        migrations.CreateModel(
            name="ClientDiscard",
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
                ("hour", models.DateTimeField()),
                ("category", models.CharField(max_length=64)),
                ("reason", models.CharField(max_length=64)),
                ("quantity", models.PositiveBigIntegerField(default=0)),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="client_discards",
                        to="core.project",
                    ),
                ),
            ],
            options={
                "ordering": ("-hour", "project_id", "category", "reason"),
                "indexes": [
                    models.Index(
                        fields=["-hour"], name="ingest_client_discard_hour"
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("project", "hour", "category", "reason"),
                        name="ingest_client_discard_uq",
                    )
                ],
            },
        ),
    ]
