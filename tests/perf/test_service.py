import datetime

import pytest

from pandora.perf import models, service

pytestmark = pytest.mark.django_db

NOW = datetime.datetime(2026, 9, 9, 12, 30, tzinfo=datetime.UTC)
HOUR = NOW.replace(minute=0, second=0, microsecond=0)


def at(moment, duration_ms=120):
    started = moment.timestamp()
    return transaction(start_timestamp=started, timestamp=started + duration_ms / 1000)


def transaction(duration_ms=120, **overrides):
    started = NOW.timestamp()
    payload = {
        "type": "transaction",
        "transaction": "GET /checkout",
        "start_timestamp": started,
        "timestamp": started + duration_ms / 1000,
        "contexts": {
            "trace": {"trace_id": "c" * 32, "op": "http.server", "status": "ok"}
        },
    }
    payload.update(overrides)
    return payload


# what is stored


def test_a_transaction_is_counted_into_its_hour(project):
    """Should keep one row per endpoint per hour, not one per request."""
    service.record(project, transaction(), NOW)
    service.record(project, transaction(), NOW)

    bucket = models.TransactionBucket.objects.get()
    result = (bucket.transaction, bucket.count, bucket.hour)
    expected = ("GET /checkout", 2, HOUR)

    assert result == expected


def test_the_duration_is_summed_and_the_slowest_kept(project):
    """Should answer the average and the worst case without storing either request."""
    service.record(project, transaction(duration_ms=100), NOW)
    service.record(project, transaction(duration_ms=300), NOW)

    bucket = models.TransactionBucket.objects.get()
    result = (bucket.duration_sum, bucket.duration_max)
    expected = (400.0, 300.0)

    assert result == expected


def test_a_failed_transaction_is_counted_separately(project):
    """Should be the failure rate people put an alert on."""
    service.record(project, transaction(), NOW)
    service.record(
        project,
        transaction(contexts={"trace": {"status": "internal_error"}}),
        NOW,
    )

    bucket = models.TransactionBucket.objects.get()
    result = (bucket.count, bucket.failures)
    expected = (2, 1)

    assert result == expected


def test_a_transaction_with_no_status_is_not_a_failure(project):
    """Should not read a missing status as an error."""
    service.record(project, transaction(contexts={}), NOW)

    assert models.TransactionBucket.objects.get().failures == 0


def test_environments_are_counted_apart(project):
    """Should keep staging latency out of the production number."""
    service.record(project, transaction(environment="production"), NOW)
    service.record(project, transaction(environment="staging"), NOW)

    assert models.TransactionBucket.objects.count() == 2


def test_releases_are_counted_apart(project):
    """Should let one deploy's latency be compared against another's."""
    service.record(project, transaction(release="1.4.0"), NOW)
    service.record(project, transaction(release="1.4.1"), NOW)

    assert models.TransactionBucket.objects.count() == 2


def test_a_transaction_with_no_name_is_ignored(project):
    """Should not open a bucket nothing can be read from."""
    service.record(project, transaction(transaction=""), NOW)

    assert models.TransactionBucket.objects.count() == 0


def test_a_transaction_with_no_timestamps_is_ignored(project):
    """Should not record a duration it would have to invent."""
    payload = transaction()
    del payload["timestamp"]

    service.record(project, payload, NOW)

    assert models.TransactionBucket.objects.count() == 0


def test_a_negative_duration_is_ignored(project):
    """Should refuse a clock that ran backwards rather than store a negative."""
    started = NOW.timestamp()
    service.record(
        project,
        transaction(start_timestamp=started, timestamp=started - 5),
        NOW,
    )

    assert models.TransactionBucket.objects.count() == 0


def test_an_absurd_duration_is_capped(project):
    """Should bound one bad clock's effect on the average."""
    started = NOW.timestamp()
    service.record(
        project,
        transaction(start_timestamp=started, timestamp=started + 10_000_000),
        NOW,
    )

    assert (
        models.TransactionBucket.objects.get().duration_max == service.MAX_DURATION_MS
    )


def test_a_payload_that_is_not_an_object_is_refused(project):
    """Should name the mistake rather than store nothing quietly."""
    with pytest.raises(service.TransactionError):
        service.record(project, "boom", NOW)


def test_only_transaction_items_are_recognised():
    """Should not treat an error event as a transaction."""
    result = (
        service.is_transaction(transaction()),
        service.is_transaction({"type": "event"}),
        service.is_transaction("boom"),
    )
    expected = (True, False, False)

    assert result == expected


# spans, summed rather than stored


def test_spans_are_summed_by_operation(project):
    """Should answer 'database or network' without keeping a waterfall."""
    started = NOW.timestamp()
    service.record(
        project,
        transaction(
            spans=[
                {"op": "db", "start_timestamp": started, "timestamp": started + 0.05},
                {"op": "db", "start_timestamp": started, "timestamp": started + 0.03},
                {
                    "op": "http.client",
                    "start_timestamp": started,
                    "timestamp": started + 0.02,
                },
            ]
        ),
        NOW,
    )

    rows = {
        row.op: (row.count, row.duration_sum)
        for row in models.SpanSummary.objects.all()
    }
    expected = {"db": (2, 80.0), "http.client": (1, 20.0)}

    assert rows == expected


def test_a_span_with_no_operation_falls_into_other(project):
    """Should still account for the time rather than drop it."""
    started = NOW.timestamp()
    service.record(
        project,
        transaction(spans=[{"start_timestamp": started, "timestamp": started + 0.01}]),
        NOW,
    )

    assert models.SpanSummary.objects.get().op == service.DEFAULT_OP


def test_spans_from_a_second_transaction_add_up(project):
    """Should fold into the same summary rather than mint a second one."""
    started = NOW.timestamp()
    span = {"op": "db", "start_timestamp": started, "timestamp": started + 0.05}
    service.record(project, transaction(spans=[span]), NOW)
    service.record(project, transaction(spans=[span]), NOW)

    summary = models.SpanSummary.objects.get()
    assert summary.count == 2


def test_the_span_list_is_bounded(project):
    """Should not let one transaction with ten thousand spans do ten thousand writes."""
    started = NOW.timestamp()
    spans = [
        {"op": f"op{index}", "start_timestamp": started, "timestamp": started + 0.01}
        for index in range(service.MAX_SPANS + 50)
    ]

    service.record(project, transaction(spans=spans), NOW)

    assert models.SpanSummary.objects.count() == service.MAX_SPANS


# percentiles from the histogram


def test_a_quantile_of_an_empty_histogram_is_zero():
    """Should not report a latency for an endpoint nobody called."""
    assert service.quantile(models.empty_histogram(), 0.95) == 0.0


def test_the_median_lands_in_the_bucket_it_belongs_to():
    """Should be honest to the resolution the buckets actually keep."""
    histogram = models.empty_histogram()
    histogram[service.bucket_index(30)] = 100

    assert service.quantile(histogram, 0.5) == 50.0


def test_the_ninety_fifth_ignores_the_bulk_of_fast_requests():
    """Should be the number that changes when a tail appears."""
    histogram = models.empty_histogram()
    histogram[service.bucket_index(5)] = 95
    histogram[service.bucket_index(2000)] = 5

    assert service.quantile(histogram, 0.95) == 5.0


def test_a_slow_tail_moves_the_ninety_ninth():
    """Should separate the tail from the median, which is the point."""
    histogram = models.empty_histogram()
    histogram[service.bucket_index(5)] = 90
    histogram[service.bucket_index(2000)] = 10

    assert service.quantile(histogram, 0.99) == 2500.0


def test_a_duration_past_the_last_boundary_reads_as_the_last_one():
    """Should not claim a precision the overflow bucket does not have."""
    histogram = models.empty_histogram()
    histogram[-1] = 10

    assert service.quantile(histogram, 0.95) == service.BOUNDARIES_MS[-1]


# reading it back


def test_readings_fold_the_hours_of_one_endpoint(project):
    """Should answer a window, which is not what a bucket stores."""
    service.record(project, transaction(), NOW)
    service.record(project, at(NOW - datetime.timedelta(hours=2)), NOW)

    rows = service.readings(project, NOW - datetime.timedelta(hours=6), NOW)

    assert len(rows) == 1
    assert rows[0].count == 2


def test_readings_outside_the_window_are_left_out(project):
    """Should measure the window it was asked for."""
    service.record(project, at(NOW - datetime.timedelta(days=10)), NOW)

    assert service.readings(project, NOW - datetime.timedelta(hours=6), NOW) == []


def test_readings_are_ordered_by_how_busy_the_endpoint_is(project):
    """Should put the endpoint that carries the traffic first."""
    service.record(project, transaction(transaction="GET /quiet"), NOW)
    for _ in range(3):
        service.record(project, transaction(transaction="GET /busy"), NOW)

    rows = service.readings(project, NOW - datetime.timedelta(hours=6), NOW)

    assert [row.transaction for row in rows] == ["GET /busy", "GET /quiet"]


def test_a_reading_reports_its_failure_rate(project):
    """Should be a percentage a threshold can be set against."""
    service.record(project, transaction(), NOW)
    service.record(
        project, transaction(contexts={"trace": {"status": "internal_error"}}), NOW
    )

    row = service.readings(project, NOW - datetime.timedelta(hours=6), NOW)[0]

    assert row.failure_rate == 50.0


def test_a_reading_reports_its_average(project):
    """Should be the plain mean, which the sum and the count already give."""
    service.record(project, transaction(duration_ms=100), NOW)
    service.record(project, transaction(duration_ms=300), NOW)

    row = service.readings(project, NOW - datetime.timedelta(hours=6), NOW)[0]

    assert row.average == 200.0


def test_a_name_pattern_narrows_the_readings(project):
    """Should let a monitor watch one family of endpoints."""
    service.record(project, transaction(transaction="GET /api/orders"), NOW)
    service.record(project, transaction(transaction="GET /health"), NOW)

    rows = service.readings(
        project, NOW - datetime.timedelta(hours=6), NOW, pattern="GET /api/*"
    )

    assert [row.transaction for row in rows] == ["GET /api/orders"]


def test_combining_folds_every_matching_endpoint(project):
    """Should be one number for a threshold over a whole family."""
    service.record(project, transaction(transaction="GET /api/orders"), NOW)
    service.record(project, transaction(transaction="GET /api/items"), NOW)

    reading = service.combined(project, NOW - datetime.timedelta(hours=6), NOW)

    assert reading is not None
    assert reading.count == 2


def test_combining_nothing_reads_as_nothing(project):
    """Should let a monitor say 'no data' rather than 'zero'."""
    assert service.combined(project, NOW - datetime.timedelta(hours=6), NOW) is None


def test_the_span_breakdown_reads_one_endpoint(project):
    """Should be what the page shows when a reader picks an endpoint."""
    started = NOW.timestamp()
    service.record(
        project,
        transaction(
            spans=[
                {"op": "db", "start_timestamp": started, "timestamp": started + 0.05}
            ]
        ),
        NOW,
    )

    found = service.span_breakdown(
        project, "GET /checkout", NOW - datetime.timedelta(hours=6), NOW
    )

    assert found == [("db", 1, 50.0)]


def test_pruning_removes_old_buckets(project):
    """Should keep performance under the same retention as everything else."""
    service.record(project, at(NOW - datetime.timedelta(days=40)), NOW)
    service.record(project, transaction(), NOW)

    service.prune(NOW - datetime.timedelta(days=30))

    assert models.TransactionBucket.objects.count() == 1


# the ingest door


def envelope_body(payload):
    import json

    header = json.dumps({"event_id": "b" * 32}).encode()
    item = json.dumps({"type": "transaction"}).encode()
    return b"\n".join([header, item, json.dumps(payload).encode()])


@pytest.fixture
def dsn_key(project):
    from pandora.core import models as core_models

    return core_models.DsnKey.objects.create(project=project, public_key="k" * 32)


def test_a_transaction_envelope_is_counted(client, dsn_key, project):
    """Should stop being an acked-and-dropped item, which is what it was."""
    response = client.post(
        f"/api/{project.pk}/envelope/?sentry_key={dsn_key.public_key}",
        data=envelope_body(transaction()),
        content_type="application/x-sentry-envelope",
    )

    assert response.status_code == 200
    assert models.TransactionBucket.objects.get().count == 1


def test_a_transaction_envelope_stores_no_event(client, dsn_key, project):
    """Should never reach the event store — that is the whole storage argument."""
    from pandora.ingest import models as ingest_models

    client.post(
        f"/api/{project.pk}/envelope/?sentry_key={dsn_key.public_key}",
        data=envelope_body(transaction()),
        content_type="application/x-sentry-envelope",
    )

    assert ingest_models.RawEnvelope.objects.count() == 0


def test_an_unreadable_transaction_item_is_dropped(client, dsn_key, project):
    """Should ack the envelope rather than fail the whole request."""
    import json

    body = b"\n".join(
        [
            json.dumps({"event_id": "b" * 32}).encode(),
            json.dumps({"type": "transaction"}).encode(),
            b"{not json",
        ]
    )

    response = client.post(
        f"/api/{project.pk}/envelope/?sentry_key={dsn_key.public_key}",
        data=body,
        content_type="application/x-sentry-envelope",
    )

    assert response.status_code == 200
    assert models.TransactionBucket.objects.count() == 0


def test_a_drop_rule_refuses_a_transaction(client, dsn_key, project):
    """Should honour the same gate every other door goes through."""
    from pandora.scrub import models as scrub_models

    scrub_models.DropRule.objects.create(
        project=project, name="health", field="transaction", pattern="GET /checkout"
    )

    client.post(
        f"/api/{project.pk}/envelope/?sentry_key={dsn_key.public_key}",
        data=envelope_body(transaction()),
        content_type="application/x-sentry-envelope",
    )

    assert models.TransactionBucket.objects.count() == 0


# edges the aggregation has to survive


def test_a_reading_with_no_requests_reports_no_rates():
    """Should not divide by zero for an endpoint that was never called."""
    empty = service.Reading(
        transaction="GET /nothing",
        count=0,
        failures=0,
        duration_sum=0.0,
        duration_max=0.0,
        histogram=models.empty_histogram(),
    )

    result = (empty.failure_rate, empty.average)
    expected = (0.0, 0.0)

    assert result == expected


def test_a_bucket_with_a_corrupt_histogram_is_repaired(project):
    """Should not raise on a row written by an older, shorter histogram."""
    service.record(project, transaction(), NOW)
    models.TransactionBucket.objects.update(histogram=[1, 2, 3])

    service.record(project, transaction(), NOW)

    bucket = models.TransactionBucket.objects.get()
    assert sum(bucket.histogram) == 1


def test_a_reading_from_a_corrupt_histogram_is_repaired(project):
    """Should render the page rather than fail on one bad row."""
    service.record(project, transaction(), NOW)
    models.TransactionBucket.objects.update(histogram=[])

    rows = service.readings(project, NOW - datetime.timedelta(hours=6), NOW)

    assert rows[0].quantile(0.95) == 0.0


def test_a_transaction_with_no_span_list_records_none(project):
    """Should accept the common case of a transaction with no spans."""
    service.record(project, transaction(spans="not a list"), NOW)

    assert models.SpanSummary.objects.count() == 0


def test_a_span_that_is_not_an_object_is_skipped(project):
    """Should not fail a whole transaction over one malformed span."""
    started = NOW.timestamp()
    service.record(
        project,
        transaction(
            spans=[
                "boom",
                {"op": "db", "start_timestamp": started, "timestamp": started + 0.01},
            ]
        ),
        NOW,
    )

    assert models.SpanSummary.objects.get().op == "db"


def test_a_span_with_no_timestamps_is_skipped(project):
    """Should not invent a duration for a span the SDK sent half of."""
    service.record(project, transaction(spans=[{"op": "db"}]), NOW)

    assert models.SpanSummary.objects.count() == 0


def test_a_span_that_ran_backwards_is_skipped(project):
    """Should refuse a negative the same way the transaction does."""
    started = NOW.timestamp()
    service.record(
        project,
        transaction(
            spans=[{"op": "db", "start_timestamp": started, "timestamp": started - 1}]
        ),
        NOW,
    )

    assert models.SpanSummary.objects.count() == 0


def test_a_transaction_with_no_start_falls_back_to_when_it_arrived(project):
    """Should still bucket a transaction whose start the SDK left out."""
    started = NOW.timestamp()
    payload = transaction()
    del payload["start_timestamp"]
    payload["timestamp"] = started

    service.record(project, payload, NOW)

    assert models.TransactionBucket.objects.count() == 0


def test_an_iso_timestamp_is_read(project):
    """Should accept the string form as well as the float one."""
    service.record(
        project,
        transaction(
            start_timestamp="2026-09-09T12:30:00Z",
            timestamp="2026-09-09T12:30:00.250Z",
        ),
        NOW,
    )

    assert models.TransactionBucket.objects.get().duration_max == 250.0


def test_an_unreadable_timestamp_is_ignored(project):
    """Should drop the transaction rather than guess at its duration."""
    service.record(project, transaction(start_timestamp="yesterday"), NOW)

    assert models.TransactionBucket.objects.count() == 0


def test_the_environment_falls_back_to_the_token(project):
    """Should label a transaction whose payload named no environment."""
    payload = transaction()
    payload.pop("environment", None)

    service.record(project, payload, NOW, environment="production")

    assert models.TransactionBucket.objects.get().environment == "production"


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("GET /api/orders", ["GET /api/orders"]),
        ("GET /api/*", ["GET /api/orders"]),
        ("*orders", ["GET /api/orders"]),
        ("GET*orders", ["GET /api/orders"]),
        ("*", ["GET /api/orders", "GET /health"]),
    ],
)
def test_a_pattern_narrows_the_readings(project, pattern, expected):
    """Should support the glob forms an operator would type."""
    service.record(project, transaction(transaction="GET /api/orders"), NOW)
    service.record(project, transaction(transaction="GET /health"), NOW)

    rows = service.readings(
        project, NOW - datetime.timedelta(hours=6), NOW, pattern=pattern
    )

    assert sorted(row.transaction for row in rows) == sorted(expected)


def test_combining_narrows_by_environment(project):
    """Should keep a production threshold off staging traffic."""
    service.record(project, transaction(environment="production"), NOW)
    service.record(project, transaction(environment="staging"), NOW)

    reading = service.combined(
        project,
        NOW - datetime.timedelta(hours=6),
        NOW,
        environment="production",
    )

    assert reading is not None
    assert reading.count == 1


def test_pruning_nothing_removes_nothing(project):
    """Should be safe to run before any transaction has arrived."""
    assert service.prune(NOW) == 0


def test_the_demo_seeds_endpoints_a_reader_can_look_at(project):
    """Should give a fresh install something on the performance page."""
    from pandora.perf import demo

    seeded = demo.seed(project, "production", NOW)

    names = sorted(
        row.transaction
        for row in service.readings(project, NOW - datetime.timedelta(days=2), NOW)
    )
    expected = ["GET /api/items", "GET /api/orders", "POST /api/checkout"]

    assert seeded > 0
    assert names == expected


def test_the_demo_produces_a_latency_spread(project):
    """Should not seed one duration, which would make every percentile equal."""
    from pandora.perf import demo

    demo.seed(project, "production", NOW)

    rows = {
        row.transaction: row
        for row in service.readings(project, NOW - datetime.timedelta(days=2), NOW)
    }
    checkout = rows["POST /api/checkout"]

    assert checkout.p95 > checkout.p50


def test_the_demo_produces_failures(project):
    """Should make the failure-rate column show something other than zero."""
    from pandora.perf import demo

    demo.seed(project, "production", NOW)

    rows = {
        row.transaction: row
        for row in service.readings(project, NOW - datetime.timedelta(days=2), NOW)
    }

    assert rows["POST /api/checkout"].failure_rate > 0
