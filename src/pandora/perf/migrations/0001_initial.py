import django.db.models.deletion
import pandora.perf.models
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("core", "0008_project_auto_resolve"),
    ]

    operations = [
        migrations.CreateModel(
            name="TransactionBucket",
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
                ("transaction", models.CharField(max_length=200)),
                (
                    "environment",
                    models.CharField(blank=True, default="", max_length=100),
                ),
                ("release", models.CharField(blank=True, default="", max_length=250)),
                ("hour", models.DateTimeField()),
                ("count", models.PositiveBigIntegerField(default=0)),
                ("failures", models.PositiveBigIntegerField(default=0)),
                ("duration_sum", models.FloatField(default=0)),
                ("duration_max", models.FloatField(default=0)),
                (
                    "histogram",
                    models.JSONField(
                        blank=True, default=pandora.perf.models.empty_histogram
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="transaction_buckets",
                        to="core.project",
                    ),
                ),
            ],
            options={
                "ordering": ("-hour", "transaction"),
            },
        ),
        migrations.CreateModel(
            name="SpanSummary",
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
                ("op", models.CharField(max_length=64)),
                ("count", models.PositiveBigIntegerField(default=0)),
                ("duration_sum", models.FloatField(default=0)),
                (
                    "bucket",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="spans",
                        to="perf.transactionbucket",
                    ),
                ),
            ],
            options={
                "ordering": ("-duration_sum", "op"),
            },
        ),
        migrations.AddIndex(
            model_name="transactionbucket",
            index=models.Index(fields=["project", "-hour"], name="perf_bucket_hour"),
        ),
        migrations.AddIndex(
            model_name="transactionbucket",
            index=models.Index(
                fields=["project", "transaction", "-hour"], name="perf_bucket_name_hour"
            ),
        ),
        migrations.AddConstraint(
            model_name="transactionbucket",
            constraint=models.UniqueConstraint(
                fields=("project", "transaction", "environment", "release", "hour"),
                name="perf_transaction_bucket_uq",
            ),
        ),
        migrations.AddConstraint(
            model_name="spansummary",
            constraint=models.UniqueConstraint(
                fields=("bucket", "op"), name="perf_span_summary_uq"
            ),
        ),
    ]
