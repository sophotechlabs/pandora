from __future__ import annotations

from pandora.issues import lifecycle
from pandora.issues.models import Issue

MAX_TEXT = 4000
MAX_FRAMES = 12


def text_for(issue: Issue, occurrence: lifecycle.Occurrence) -> str:
    """What free text in the stream searches.

    One column on the issues table rather than an index over every stored event:
    the title, the culprit, the fingerprint parts, the newest message and the
    frame paths it came from. It finds the issue about `charge.py` without
    pulling a search engine into a single-container product.
    """
    parts = [
        issue.title,
        issue.culprit,
        occurrence.message,
        *occurrence.fingerprint,
        *_frames(occurrence),
    ]
    seen: list[str] = []
    for part in parts:
        text = str(part).strip()
        if text and text not in seen:
            seen.append(text)
    return "\n".join(seen)[:MAX_TEXT]


def _frames(occurrence: lifecycle.Occurrence) -> list[str]:
    found: list[str] = []
    for exception in (occurrence.payload or {}).get("exceptions", []) or []:
        for frame in exception.get("frames", []) or []:
            for name in ("filename", "abs_path", "module", "function"):
                value = frame.get(name)
                if value:
                    found.append(str(value))
            if len(found) >= MAX_FRAMES:
                return found[:MAX_FRAMES]
    return found[:MAX_FRAMES]
