from __future__ import annotations

import json
import math
from typing import Any


def loads(raw: str | bytes | bytearray) -> Any:
    try:
        return json.loads(
            raw,
            parse_float=_finite_float,
            parse_constant=_invalid_constant,
        )
    except (ValueError, RecursionError) as error:
        raise ValueError("payload is not finite JSON") from error


def _finite_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("JSON numbers must be finite")
    return value


def _invalid_constant(raw: str) -> Any:
    raise ValueError(f"{raw} is not valid JSON")
