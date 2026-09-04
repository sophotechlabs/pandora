import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [("core", "0006_token_scopes")]

    operations = [
        migrations.CreateModel(
            name="EventAttachment",
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
                ("event_id", models.CharField(max_length=64)),
                ("filename", models.CharField(max_length=255)),
                (
                    "content_type",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                (
                    "attachment_type",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                ("size", models.PositiveBigIntegerField()),
                ("sha256", models.CharField(max_length=64)),
                ("blob", models.FileField(upload_to="attachments/%Y/%m/%d")),
                ("received_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="event_attachments",
                        to="core.project",
                    ),
                ),
            ],
            options={"ordering": ("received_at", "pk")},
        ),
        migrations.AddConstraint(
            model_name="eventattachment",
            constraint=models.UniqueConstraint(
                fields=("project", "event_id", "filename", "sha256"),
                name="attachments_event_file_uq",
            ),
        ),
        migrations.AddIndex(
            model_name="eventattachment",
            index=models.Index(
                fields=["project", "event_id"], name="attachments_event_lookup"
            ),
        ),
        migrations.AddIndex(
            model_name="eventattachment",
            index=models.Index(
                fields=["received_at"], name="attachments_received"
            ),
        ),
    ]
