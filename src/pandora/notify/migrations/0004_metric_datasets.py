from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notify", "0003_metric_monitor"),
    ]

    operations = [
        migrations.AlterField(
            model_name="metricmonitor",
            name="dataset",
            field=models.CharField(
                choices=[
                    ("events", "Events"),
                    ("issues", "Issues seen"),
                    ("new_issues", "Issues first seen"),
                    ("users", "People first affected"),
                    ("crash_free", "Crash-free sessions (%)"),
                    ("throughput", "Transactions"),
                    ("p50", "Median duration (ms)"),
                    ("p95", "95th percentile duration (ms)"),
                    ("failure_rate", "Failed transactions (%)"),
                ],
                default="events",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="metricmonitor",
            name="query",
            field=models.CharField(
                blank=True,
                default="",
                help_text="An issue-stream query for the issue datasets, or a transaction name (with an optional *) for the performance ones",
                max_length=500,
            ),
        ),
    ]
