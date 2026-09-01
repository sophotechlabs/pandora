"""Print what a live run actually stored, for a failure the assertions only name."""

from __future__ import annotations

import json
import os
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pandora.web.settings")
django.setup()

from pandora.artifacts import models as artifact_models
from pandora.events.store import get_store
from pandora.ingest import models as ingest_models
from pandora.issues import models as issue_models


def main() -> None:
    fragment = sys.argv[1] if len(sys.argv) > 1 else ""
    print("== issues")
    for issue in issue_models.Issue.objects.order_by("id"):
        print(f"  {issue.pk} {issue.title}")

    print("== envelopes")
    for row in ingest_models.RawEnvelope.objects.order_by("id"):
        print(f"  {row.pk} {row.state} {row.error[:160] if row.error else ''}")

    print("== bundles")
    for bundle in artifact_models.ArtifactBundle.objects.all():
        files = list(bundle.files.values_list("path", "kind"))
        print(f"  {bundle.debug_id} {files}")

    if not fragment:
        return

    issue = issue_models.Issue.objects.filter(title__icontains=fragment).first()
    if issue is None:
        print(f"== no issue holding {fragment!r}")
        return

    print(f"== newest event of {issue.pk} {issue.title}")
    events = get_store().fetch(issue.project_id, issue_id=issue.pk, limit=1)
    for event in events:
        print(json.dumps(event.payload, indent=2)[:4000])


if __name__ == "__main__":
    main()
