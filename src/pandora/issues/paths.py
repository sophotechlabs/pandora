from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from django.db.models import Q

from pandora.core.models import Project
from pandora.issues.models import PathRule

log = logging.getLogger(__name__)


def load_rules(project: Project) -> list[PathRule]:
    return list(
        PathRule.objects.filter(active=True).filter(
            Q(project__isnull=True) | Q(project=project)
        )
    )


def canonical(path: str, rules: Sequence[PathRule]) -> str:
    """Collapse the parts of a path that move between machines.

    A venv, a container layout and an nvm prefix put the same file at three
    addresses, and grouping on the address splits one issue into three. The
    substitutions run in order, so an earlier rule can feed a later one.
    """
    result = path
    for rule in rules:
        try:
            result = re.sub(rule.pattern, rule.replacement, result)
        except re.error:
            log.warning(
                "path rule %s has an invalid pattern %r — skipped",
                rule.pk,
                rule.pattern,
            )
    return result
