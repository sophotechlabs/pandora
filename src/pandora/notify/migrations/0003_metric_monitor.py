import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_project_auto_resolve"),
        ("notify", "0002_alter_delivery_state"),
    ]

    operations = [
        migrations.CreateModel(
            name="MetricMonitor",
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
                ("name", models.CharField(max_length=100)),
                (
                    "dataset",
                    models.CharField(
                        choices=[
                            ("events", "Events"),
                            ("issues", "Issues seen"),
                            ("new_issues", "Issues first seen"),
                            ("users", "People first affected"),
                            ("crash_free", "Crash-free sessions (%)"),
                        ],
                        default="events",
                        max_length=16,
                    ),
                ),
                ("query", models.CharField(blank=True, default="", max_length=500)),
                (
                    "environment",
                    models.CharField(blank=True, default="", max_length=100),
                ),
                ("window_minutes", models.PositiveIntegerField(default=60)),
                (
                    "comparison",
                    models.CharField(
                        choices=[("above", "Above"), ("below", "Below")],
                        default="above",
                        max_length=8,
                    ),
                ),
                ("threshold", models.FloatField(default=0)),
                ("comparison_delta_minutes", models.PositiveIntegerField(default=0)),
                (
                    "severity",
                    models.CharField(
                        choices=[
                            ("debug", "Debug"),
                            ("info", "Info"),
                            ("warning", "Warning"),
                            ("error", "Error"),
                            ("fatal", "Fatal"),
                        ],
                        default="warning",
                        max_length=16,
                    ),
                ),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("ok", "OK"),
                            ("firing", "Firing"),
                            ("no_data", "No data"),
                        ],
                        default="ok",
                        max_length=8,
                    ),
                ),
                ("last_value", models.FloatField(blank=True, null=True)),
                ("last_evaluated_at", models.DateTimeField(blank=True, null=True)),
                ("last_triggered_at", models.DateTimeField(blank=True, null=True)),
                ("active", models.BooleanField(default=True)),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="metric_monitors",
                        to="core.project",
                    ),
                ),
            ],
            options={
                "ordering": ("project__slug", "name"),
            },
        ),
        migrations.CreateModel(
            name="MetricRun",
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
                ("at", models.DateTimeField()),
                ("value", models.FloatField()),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("ok", "OK"),
                            ("firing", "Firing"),
                            ("no_data", "No data"),
                        ],
                        max_length=8,
                    ),
                ),
                (
                    "monitor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="runs",
                        to="notify.metricmonitor",
                    ),
                ),
            ],
            options={
                "ordering": ("-at", "-pk"),
            },
        ),
        migrations.AddIndex(
            model_name="metricmonitor",
            index=models.Index(fields=["active"], name="notify_metric_active"),
        ),
        migrations.AddConstraint(
            model_name="metricmonitor",
            constraint=models.UniqueConstraint(
                fields=("project", "name"), name="notify_metric_monitor_uq"
            ),
        ),
        migrations.AddIndex(
            model_name="metricrun",
            index=models.Index(fields=["monitor", "-at"], name="notify_metric_run_at"),
        ),
    ]
