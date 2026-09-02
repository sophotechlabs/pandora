from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db import transaction
from django.db.models import F

from pandora.core.models import Project
from pandora.ingest.models import ClientDiscard

MAX_ENTRIES = 100
MAX_QUANTITY = 2_147_483_647
VALUE_LENGTH = 64


def accept(project: Project, payload: Any, received_at: datetime) -> int:
    if not isinstance(payload, dict):
        return 0
    entries = payload.get("discarded_events")
    if not isinstance(entries, list):
        return 0

    grouped: dict[tuple[str, str], int] = {}
    for entry in entries[:MAX_ENTRIES]:
        parsed = _entry(entry)
        if parsed is None:
            continue
        reason, category, quantity = parsed
        key = (reason, category)
        grouped[key] = grouped.get(key, 0) + quantity

    hour = received_at.replace(minute=0, second=0, microsecond=0)
    with transaction.atomic():
        for (reason, category), quantity in grouped.items():
            row, created = ClientDiscard.objects.get_or_create(
                project=project,
                hour=hour,
                category=category,
                reason=reason,
                defaults={"quantity": quantity},
            )
            if created:
                continue
            ClientDiscard.objects.filter(pk=row.pk).update(
                quantity=F("quantity") + quantity
            )
    return sum(grouped.values())


def prune(before: datetime) -> int:
    deleted, _ = ClientDiscard.objects.filter(hour__lt=before).delete()
    return deleted


def _entry(entry: Any) -> tuple[str, str, int] | None:
    if not isinstance(entry, dict):
        return None
    reason = entry.get("reason")
    category = entry.get("category")
    quantity = entry.get("quantity")
    if not isinstance(reason, str) or not isinstance(category, str):
        return None
    reason = reason.strip()[:VALUE_LENGTH]
    category = category.strip()[:VALUE_LENGTH]
    if not reason or not category:
        return None
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        return None
    if quantity < 1 or quantity > MAX_QUANTITY:
        return None
    return (reason, category, quantity)
