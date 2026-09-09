from __future__ import annotations

from django.db import models

from pandora.core.models import Project

TRANSACTION_MAX = 200
OP_MAX = 64

BOUNDARIES_MS = (
    1.0,
    5.0,
    10.0,
    25.0,
    50.0,
    100.0,
    250.0,
    500.0,
    1000.0,
    2500.0,
    5000.0,
    10000.0,
)


def empty_histogram() -> list[int]:
    return [0] * (len(BOUNDARIES_MS) + 1)


class TransactionBucket(models.Model):
    """One hour of one endpoint, counted rather than recorded.

    A span store is what makes an error tracker need a columnar database, a
    second retention policy and a second set of performance bugs. A fixed
    histogram answers throughput, failure rate and the percentiles anybody
    actually alerts on, in a row per endpoint per hour — so the single
    container survives having performance at all.
    """

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="transaction_buckets",
    )
    transaction = models.CharField(max_length=TRANSACTION_MAX)
    environment = models.CharField(max_length=100, blank=True, default="")
    release = models.CharField(max_length=250, blank=True, default="")
    hour = models.DateTimeField()
    count = models.PositiveBigIntegerField(default=0)
    failures = models.PositiveBigIntegerField(default=0)
    duration_sum = models.FloatField(default=0)
    duration_max = models.FloatField(default=0)
    histogram = models.JSONField(default=empty_histogram, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "transaction", "environment", "release", "hour"],
                name="perf_transaction_bucket_uq",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "-hour"], name="perf_bucket_hour"),
            models.Index(
                fields=["project", "transaction", "-hour"],
                name="perf_bucket_name_hour",
            ),
        ]
        ordering = ("-hour", "transaction")

    def __str__(self) -> str:
        return f"{self.transaction}@{self.hour:%Y-%m-%dT%H}Z x{self.count}"


class SpanSummary(models.Model):
    """Where the time inside an endpoint went, by span operation.

    Not a span: a sum. It answers *is this endpoint slow because of the
    database or because of an outbound call*, which is the only question the
    waterfall gets used for often enough to pay for a span store.
    """

    bucket = models.ForeignKey(
        TransactionBucket,
        on_delete=models.CASCADE,
        related_name="spans",
    )
    op = models.CharField(max_length=OP_MAX)
    count = models.PositiveBigIntegerField(default=0)
    duration_sum = models.FloatField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["bucket", "op"],
                name="perf_span_summary_uq",
            ),
        ]
        ordering = ("-duration_sum", "op")

    def __str__(self) -> str:
        return f"{self.op} x{self.count}"
