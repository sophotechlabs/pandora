from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0007_alter_ingesttoken_source"),
        ("ingest", "0006_clientdiscard"),
    ]

    operations = [
        migrations.AlterField(
            model_name="rawenvelope",
            name="source",
            field=models.CharField(
                choices=[
                    ("am", "Alertmanager"),
                    ("sdk", "SDK"),
                    ("log", "Log lines"),
                    ("cron", "Cron check-ins"),
                    ("otlp", "OpenTelemetry"),
                    ("ci", "CI"),
                ],
                max_length=8,
            ),
        ),
    ]
