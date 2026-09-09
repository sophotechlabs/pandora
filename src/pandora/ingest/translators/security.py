from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

CSP = "csp"
EXPECT_CT = "expect-ct"
HPKP = "hpkp"
NEL = "nel"

TITLE_MAX = 500
VALUE_MAX = 500
LOCAL_DIRECTIVE_VALUES = ("self", "unsafe-inline", "unsafe-eval", "none", "data")


class SecurityReportError(ValueError):
    pass


def translate(document: Any) -> dict[str, Any]:
    """Turn a browser's security report into the event shape the rest expects.

    The browser posts these on its own, with no SDK and no envelope, so the
    translator is the whole integration: after this the report is an ordinary
    event and grouping, triage and notification are already built.
    """
    if not isinstance(document, Mapping):
        raise SecurityReportError("report is not a JSON object")
    if isinstance(document.get("csp-report"), Mapping):
        return _csp(document["csp-report"])
    if isinstance(document.get("expect-ct-report"), Mapping):
        return _expect_ct(document["expect-ct-report"])
    if isinstance(document.get("known-pins"), list):
        return _hpkp(document)
    raise SecurityReportError("report names no known security policy")


def translate_nel(entry: Any) -> dict[str, Any]:
    """One Network Error Logging report, as the browser's array carries it."""
    if not isinstance(entry, Mapping):
        raise SecurityReportError("report is not a JSON object")
    body = entry.get("body")
    if not isinstance(body, Mapping):
        raise SecurityReportError("report carries no body")
    error = _text(body.get("type")) or "unknown"
    phase = _text(body.get("phase"))
    url = _text(entry.get("url"))
    title = f"NEL: {error}"
    if phase:
        title = f"NEL: {error} during {phase}"
    return _event(
        title=title,
        culprit=_origin(url),
        logger=NEL,
        level="warning",
        extra={
            "type": error,
            "phase": phase,
            "url": url,
            "server_ip": _text(body.get("server_ip")),
            "status_code": body.get("status_code"),
            "elapsed_time": body.get("elapsed_time"),
            "sampling_fraction": body.get("sampling_fraction"),
            "user_agent": _text(entry.get("user_agent")),
        },
        tags={
            "error_type": error,
            "phase": phase,
            "url": url,
        },
    )


def _csp(report: Mapping[str, Any]) -> dict[str, Any]:
    directive = _text(report.get("effective-directive")) or _text(
        report.get("violated-directive")
    )
    blocked = _text(report.get("blocked-uri"))
    document = _text(report.get("document-uri"))
    title = f"Blocked '{directive or 'unknown'}' from '{_blamed(blocked)}'"
    return _event(
        title=title,
        culprit=_origin(document),
        logger=CSP,
        level="warning",
        extra={
            "effective-directive": directive,
            "blocked-uri": blocked,
            "document-uri": document,
            "original-policy": _text(report.get("original-policy")),
            "disposition": _text(report.get("disposition")),
            "referrer": _text(report.get("referrer")),
            "status-code": report.get("status-code"),
            "line-number": report.get("line-number"),
            "column-number": report.get("column-number"),
            "source-file": _text(report.get("source-file")),
        },
        tags={
            "effective-directive": directive,
            "blocked-uri": _origin(blocked) or blocked,
        },
    )


def _expect_ct(report: Mapping[str, Any]) -> dict[str, Any]:
    hostname = _text(report.get("hostname"))
    return _event(
        title=f"Expect-CT failed for '{hostname or 'unknown host'}'",
        culprit=hostname,
        logger=EXPECT_CT,
        level="warning",
        extra={
            "hostname": hostname,
            "port": report.get("port"),
            "date-time": _text(report.get("date-time")),
            "effective-expiration-date": _text(report.get("effective-expiration-date")),
            "served-certificate-chain": report.get("served-certificate-chain"),
            "scts": report.get("scts"),
        },
        tags={"hostname": hostname},
    )


def _hpkp(report: Mapping[str, Any]) -> dict[str, Any]:
    hostname = _text(report.get("hostname"))
    return _event(
        title=f"Public key pinning failed for '{hostname or 'unknown host'}'",
        culprit=hostname,
        logger=HPKP,
        level="warning",
        extra={
            "hostname": hostname,
            "port": report.get("port"),
            "date-time": _text(report.get("date-time")),
            "include-subdomains": report.get("include-subdomains"),
            "known-pins": report.get("known-pins"),
        },
        tags={"hostname": hostname},
    )


def _event(
    *,
    title: str,
    culprit: str,
    logger: str,
    level: str,
    extra: Mapping[str, Any],
    tags: Mapping[str, Any],
) -> dict[str, Any]:
    cleaned = {key: value for key, value in extra.items() if value not in (None, "")}
    return {
        "logger": logger,
        "level": level,
        "platform": "javascript",
        "culprit": culprit[:VALUE_MAX],
        "message": title[:TITLE_MAX],
        "logentry": {"formatted": title[:TITLE_MAX], "message": title[:TITLE_MAX]},
        "fingerprint": [logger, title[:TITLE_MAX]],
        "tags": {
            key: str(value)[:VALUE_MAX]
            for key, value in tags.items()
            if value not in (None, "")
        },
        "extra": cleaned,
    }


def _blamed(blocked: str) -> str:
    if not blocked:
        return "self"
    if blocked in LOCAL_DIRECTIVE_VALUES:
        return blocked
    origin = _origin(blocked)
    if origin:
        return origin
    return blocked


def _origin(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if not parsed.hostname:
        return ""
    if parsed.scheme:
        return f"{parsed.scheme}://{parsed.hostname}"
    return parsed.hostname


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()[:VALUE_MAX]
    return ""
