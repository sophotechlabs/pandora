from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from opentelemetry import trace


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, str] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            payload["trace_id"] = trace.format_trace_id(context.trace_id)
            payload["span_id"] = trace.format_span_id(context.span_id)

        return json.dumps(payload, default=str)
