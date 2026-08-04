import json
import logging
import sys

from opentelemetry import trace
from opentelemetry.sdk import trace as sdk_trace

from pandora.core import log


def make_record(**overrides):
    fields = {
        "name": "pandora.ingest.processor",
        "level": logging.INFO,
        "pathname": "processor.py",
        "lineno": 10,
        "msg": "processed %s envelopes",
        "args": (3,),
        "exc_info": None,
    }
    fields.update(overrides)
    return logging.LogRecord(**fields)


# JsonFormatter tests


def test_formatter_emits_the_pinned_field_set():
    """Should emit exactly time, level, logger and message for a plain record."""
    result = json.loads(log.JsonFormatter().format(make_record()))
    expected = sorted(["time", "level", "logger", "message"])

    assert sorted(result) == expected


def test_formatter_renders_the_message_arguments():
    """Should interpolate the record arguments into the message."""
    result = json.loads(log.JsonFormatter().format(make_record()))

    assert result["message"] == "processed 3 envelopes"
    assert result["logger"] == "pandora.ingest.processor"


def test_formatter_stamps_utc():
    """Should timestamp the line in UTC."""
    result = json.loads(log.JsonFormatter().format(make_record()))

    assert result["time"].endswith("+00:00")


def test_formatter_lowercases_the_level():
    """Should write the level in lowercase for Loki-friendly labels."""
    record = make_record(level=logging.WARNING)

    result = json.loads(log.JsonFormatter().format(record))

    assert result["level"] == "warning"


def test_formatter_includes_the_traceback_when_present():
    """Should attach the formatted exception when the record carries one."""
    try:
        raise ValueError("boom")
    except ValueError:
        record = make_record(level=logging.ERROR, exc_info=sys.exc_info())

    result = json.loads(log.JsonFormatter().format(record))

    assert "ValueError: boom" in result["exception"]


def test_formatter_includes_trace_ids_inside_a_span():
    """Should correlate the line with the active span."""
    tracer = sdk_trace.TracerProvider().get_tracer("tests")
    with tracer.start_as_current_span("unit") as span:
        result = json.loads(log.JsonFormatter().format(make_record()))
        context = span.get_span_context()

    assert result["trace_id"] == trace.format_trace_id(context.trace_id)
    assert result["span_id"] == trace.format_span_id(context.span_id)
