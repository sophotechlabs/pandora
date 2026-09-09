from __future__ import annotations

import fnmatch
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

BROWSER_EXTENSION = "browser_extension"
LEGACY_BROWSER = "legacy_browser"
LOCALHOST = "localhost"
WEB_CRAWLER = "web_crawler"
HEALTH_CHECK = "health_check"
CHUNK_LOAD = "chunk_load"
ALLOWED_DOMAINS = "allowed_domains"

KINDS = (
    BROWSER_EXTENSION,
    LEGACY_BROWSER,
    LOCALHOST,
    WEB_CRAWLER,
    HEALTH_CHECK,
    CHUNK_LOAD,
    ALLOWED_DOMAINS,
)

EXTENSION_SCHEMES = (
    "chrome-extension://",
    "moz-extension://",
    "safari-extension://",
    "safari-web-extension://",
    "ms-browser-extension://",
    "webkit-masked-url://",
)
EXTENSION_MESSAGES = (
    "top.GLOBALS",
    "originalCreateNotification",
    "canvas.contentDocument",
    "MyApp_RemoveAllHighlights",
    "atomicFindClose",
    "conduitPage",
    "bmi_SafeAddOnload",
    "__gCrWeb",
)

CRAWLERS = re.compile(
    r"(?i)\b("
    r"googlebot|bingbot|slurp|duckduckbot|baiduspider|yandex(?:bot)?|sogou|exabot"
    r"|facebot|facebookexternalhit|ia_archiver|ahrefsbot|semrushbot|mj12bot"
    r"|dotbot|petalbot|applebot|twitterbot|linkedinbot|discordbot|telegrambot"
    r"|slackbot|whatsapp|pingdom|uptimerobot|headlesschrome|python-requests|curl"
    r"|wget|scrapy|bot|crawler|spider"
    r")\b"
)

UNSPECIFIED_ADDRESS = "0.0.0.0"  # noqa: S104
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]", UNSPECIFIED_ADDRESS)
LOOPBACK_IPS = ("127.0.0.1", "::1", UNSPECIFIED_ADDRESS)

HEALTH_PATHS = (
    "/health",
    "/healthz",
    "/healthcheck",
    "/health-check",
    "/_health",
    "/ping",
    "/ready",
    "/readyz",
    "/readiness",
    "/live",
    "/livez",
    "/liveness",
    "/status",
    "/up",
)

CHUNK_MESSAGES = re.compile(
    r"(?i)("
    r"chunkloaderror"
    r"|loading chunk \S+ failed"
    r"|loading css chunk \S+ failed"
    r"|hydration failed"
    r"|text content does not match server-rendered html"
    r"|there was an error while hydrating"
    r"|minified react error #(?:418|419|422|423|425)"
    r"|importing a module script failed"
    r"|failed to fetch dynamically imported module"
    r")"
)

LEGACY_BROWSERS: dict[str, int] = {
    "ie": 12,
    "internet explorer": 12,
    "ie mobile": 12,
    "opera mini": 100,
    "android": 5,
    "edge": 18,
    "opera": 15,
    "safari": 6,
    "firefox": 10,
    "chrome": 10,
}


def matches(kind: str, payload: Mapping[str, Any], options: Any = None) -> bool:
    """Whether one of Sentry's standard inbound filters refuses this event.

    Every check reads the payload the SDK already sends, so a filter costs one
    pass over data that is in memory anyway and nothing reaches the disk.
    """
    checks = {
        BROWSER_EXTENSION: _browser_extension,
        LEGACY_BROWSER: _legacy_browser,
        LOCALHOST: _localhost,
        WEB_CRAWLER: _web_crawler,
        HEALTH_CHECK: _health_check,
        CHUNK_LOAD: _chunk_load,
        ALLOWED_DOMAINS: _outside_allowed_domains,
    }
    check = checks.get(kind)
    if check is None:
        return False
    return check(payload, options or {})


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return ""


def _messages(payload: Mapping[str, Any]) -> list[str]:
    found = [_text(payload.get("message"))]
    logentry = payload.get("logentry")
    if isinstance(logentry, Mapping):
        found.append(_text(logentry.get("formatted")))
        found.append(_text(logentry.get("message")))
    for entry in _exceptions(payload):
        found.append(_text(entry.get("value")))
        found.append(_text(entry.get("type")))
    return [text for text in found if text]


def _exceptions(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = payload.get("exception")
    values: Any = raw
    if isinstance(raw, Mapping):
        values = raw.get("values")
    if not isinstance(values, list):
        return []
    return [entry for entry in values if isinstance(entry, Mapping)]


def _frames(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    found: list[Mapping[str, Any]] = []
    for entry in _exceptions(payload):
        stacktrace = entry.get("stacktrace")
        if not isinstance(stacktrace, Mapping):
            continue
        raw = stacktrace.get("frames")
        if not isinstance(raw, list):
            continue
        found.extend(frame for frame in raw if isinstance(frame, Mapping))
    return found


def _request(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    request = payload.get("request")
    if isinstance(request, Mapping):
        return request
    return {}


def _context(payload: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    contexts = payload.get("contexts")
    if not isinstance(contexts, Mapping):
        return {}
    context = contexts.get(name)
    if isinstance(context, Mapping):
        return context
    return {}


def _user_agent(payload: Mapping[str, Any]) -> str:
    headers = _request(payload).get("headers")
    if isinstance(headers, Mapping):
        for key, value in headers.items():
            if str(key).lower() == "user-agent":
                return _text(value)
    if isinstance(headers, list):
        for entry in headers:
            if not isinstance(entry, list) or len(entry) != 2:
                continue
            if str(entry[0]).lower() == "user-agent":
                return _text(entry[1])
    return ""


def _host(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.hostname:
        return parsed.hostname.lower()
    return ""


def _browser_extension(payload: Mapping[str, Any], options: Any) -> bool:
    for frame in _frames(payload):
        address = _text(frame.get("abs_path")) or _text(frame.get("filename"))
        if address.startswith(EXTENSION_SCHEMES):
            return True
    for message in _messages(payload):
        if any(marker in message for marker in EXTENSION_MESSAGES):
            return True
        if message.startswith(EXTENSION_SCHEMES):
            return True
    return False


def _legacy_browser(payload: Mapping[str, Any], options: Any) -> bool:
    browser = _context(payload, "browser")
    name = _text(browser.get("name")).strip().lower()
    if not name:
        return False
    limit = LEGACY_BROWSERS.get(name)
    if limit is None:
        return False
    version = _major(_text(browser.get("version")))
    if version is None:
        return name in ("ie", "internet explorer", "ie mobile", "opera mini")
    return version < limit


def _major(version: str) -> int | None:
    head = version.strip().split(".")[0]
    if not head.isdigit():
        return None
    return int(head)


def _localhost(payload: Mapping[str, Any], options: Any) -> bool:
    host = _host(_text(_request(payload).get("url")))
    if host in LOOPBACK_HOSTS:
        return True
    user = payload.get("user")
    if isinstance(user, Mapping):
        return _text(user.get("ip_address")) in LOOPBACK_IPS
    return False


def _web_crawler(payload: Mapping[str, Any], options: Any) -> bool:
    agent = _user_agent(payload)
    if not agent:
        return False
    return CRAWLERS.search(agent) is not None


def _health_check(payload: Mapping[str, Any], options: Any) -> bool:
    transaction = _text(payload.get("transaction")).strip().lower()
    candidates = [transaction]
    url = _text(_request(payload).get("url"))
    if url:
        candidates.append(urlparse(url).path.lower())
    for candidate in candidates:
        if not candidate:
            continue
        trimmed = candidate.rstrip("/") or "/"
        if any(trimmed.endswith(path) for path in HEALTH_PATHS):
            return True
    return False


def _chunk_load(payload: Mapping[str, Any], options: Any) -> bool:
    return any(CHUNK_MESSAGES.search(message) for message in _messages(payload))


def _outside_allowed_domains(payload: Mapping[str, Any], options: Any) -> bool:
    domains = _domains(options)
    if not domains:
        return False
    host = _host(_text(_request(payload).get("url")))
    if not host:
        return False
    return not any(fnmatch.fnmatchcase(host, pattern) for pattern in domains)


def _domains(options: Any) -> Sequence[str]:
    if not isinstance(options, Mapping):
        return ()
    raw = options.get("domains")
    if not isinstance(raw, list):
        return ()
    return [str(entry).strip().lower() for entry in raw if str(entry).strip()]
