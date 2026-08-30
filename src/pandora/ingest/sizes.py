from __future__ import annotations

from django.conf import settings

ENVELOPE = "envelope"
EVENT = "event"
CHECK_IN = "check_in"
CLIENT_REPORT = "client_report"
SESSION = "session"
SESSIONS = "sessions"

MIB = 1024 * 1024
KIB = 1024

DEFAULTS = {
    ENVELOPE: 200 * MIB,
    EVENT: 1 * MIB,
    CHECK_IN: 100 * KIB,
    CLIENT_REPORT: 4 * KIB,
    SESSION: 100 * KIB,
    SESSIONS: 100 * KIB,
}


def limit(kind: str) -> int:
    """What an item of this type may weigh.

    The envelope stays the operator's own cap — `PANDORA_INGEST_MAX_BYTES`, and
    raising it to the protocol's 200 MiB is their decision, not a default that
    changes underneath them. Inside it, each item type gets the limit the
    protocol names, because one number for everything lets a 1 MiB client report
    through where the spec says 4 KiB.
    """
    if kind == ENVELOPE:
        return int(settings.PANDORA_INGEST_MAX_BYTES)
    if kind == EVENT:
        return min(int(settings.PANDORA_INGEST_MAX_BYTES), DEFAULTS[EVENT])
    return DEFAULTS.get(kind, int(settings.PANDORA_INGEST_MAX_BYTES))


def fits(kind: str, size: int) -> bool:
    return size <= limit(kind)
