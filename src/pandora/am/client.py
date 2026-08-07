from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import requests
from django.conf import settings
from django.utils import timezone
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ALERTS_PATH = "/api/v2/alerts"
SILENCES_PATH = "/api/v2/silences"
SILENCE_PATH = "/api/v2/silence/{silence_id}"
ALERT_QUERY = {
    "active": "true",
    "silenced": "true",
    "inhibited": "true",
    "unprocessed": "true",
}
DEFAULT_TIMEOUT = (5.0, 30.0)
RETRY_TOTAL = 3
RETRY_BACKOFF = 0.5
RETRY_STATUSES = (429, 500, 502, 503, 504)
USER_AGENT = "pandora/0.4"
SILENCE_ID_FIELD = "silenceID"


class AlertmanagerError(RuntimeError):
    pass


class AlertmanagerClient:
    def __init__(
        self,
        base_url: str,
        *,
        ca_bundle: str = "",
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
        retries: int = RETRY_TOTAL,
        backoff_factor: float = RETRY_BACKOFF,
    ) -> None:
        self.base_url = base_url.strip().rstrip("/")
        if not self.base_url:
            raise AlertmanagerError(
                "no Alertmanager URL configured — set PANDORA_AM_URL"
            )
        self.timeout = timeout
        self._session = _build_session(ca_bundle, retries, backoff_factor)

    def alerts(self) -> list[dict[str, Any]]:
        payload = self._json("GET", ALERTS_PATH, params=dict(ALERT_QUERY))
        if not isinstance(payload, list):
            raise AlertmanagerError(
                "Alertmanager answered /alerts with a non-list body"
            )
        alerts = []
        for entry in payload:
            if not isinstance(entry, Mapping):
                raise AlertmanagerError(
                    "Alertmanager answered /alerts with a non-object alert"
                )
            alerts.append(dict(entry))
        return alerts

    def create_silence(
        self,
        *,
        matchers: Sequence[Mapping[str, Any]],
        starts_at: datetime,
        ends_at: datetime,
        comment: str,
        created_by: str,
    ) -> str:
        body = {
            "matchers": [dict(matcher) for matcher in matchers],
            "startsAt": _stamp(starts_at),
            "endsAt": _stamp(ends_at),
            "createdBy": created_by,
            "comment": comment,
        }
        payload = self._json("POST", SILENCES_PATH, json=body)
        if not isinstance(payload, Mapping):
            raise AlertmanagerError(
                "Alertmanager answered /silences with a non-object body"
            )
        silence_id = str(payload.get(SILENCE_ID_FIELD, ""))
        if not silence_id:
            raise AlertmanagerError(
                "Alertmanager took the silence without returning an id"
            )
        return silence_id

    def delete_silence(self, silence_id: str) -> None:
        path = SILENCE_PATH.format(silence_id=quote(silence_id, safe=""))
        self._request("DELETE", path)

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = f"{self.base_url}{path}"
        try:
            response = self._session.request(
                method, url, timeout=self.timeout, **kwargs
            )
            response.raise_for_status()
        except requests.RequestException as error:
            raise AlertmanagerError(f"{method} {url} failed: {error}") from error
        return response

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._request(method, path, **kwargs)
        try:
            return response.json()
        except ValueError as error:
            raise AlertmanagerError(
                f"{method} {path} returned a body that is not JSON"
            ) from error


def from_settings() -> AlertmanagerClient:
    return AlertmanagerClient(
        settings.PANDORA_AM_URL,
        ca_bundle=settings.PANDORA_AM_CA_BUNDLE,
    )


def _build_session(
    ca_bundle: str, retries: int, backoff_factor: float
) -> requests.Session:
    policy = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=list(RETRY_STATUSES),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=policy)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    session.headers["Accept"] = "application/json"
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    if ca_bundle:
        session.verify = ca_bundle
    return session


def _stamp(value: datetime) -> str:
    stamped = value
    if timezone.is_naive(stamped):
        stamped = stamped.replace(tzinfo=UTC)
    return stamped.astimezone(UTC).isoformat()
