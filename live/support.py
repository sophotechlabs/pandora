from __future__ import annotations

from pandora.issues import models as issue_models


def issue_titled(fragment: str):
    found = issue_models.Issue.objects.filter(title__icontains=fragment).order_by("id")
    assert found.exists(), (
        f"no issue whose title holds {fragment!r};"
        f" pandora holds {sorted(issue_models.Issue.objects.values_list('title', flat=True))}"
    )
    return found.first()


def body_of(page, base_url, issue) -> str:
    page.goto(f"{base_url}/issues/{issue.pk}/")
    return page.content()
