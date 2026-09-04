from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0006_token_scopes")]

    operations = [
        migrations.AlterField(
            model_name="ingesttoken",
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
                default="am",
                max_length=8,
            ),
        ),
    ]
