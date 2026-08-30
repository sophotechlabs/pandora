import http

import pytest

from pandora.issues.models import SavedView
from pandora.people.models import AuditEntry

pytestmark = pytest.mark.django_db


def save(session, name, query="is:new", sort="last_seen"):
    return session.post(
        "/views/save/",
        {"name": name, "q": query, "sort": sort, "next": "/"},
    )


# saving


def test_a_search_can_be_saved(operator_client):
    """Should turn the query someone types every morning into one click."""
    save(operator_client, "Payments backlog", query="is:unresolved label:namespace=pay")

    view = SavedView.objects.get()
    result = (view.name, view.query)
    expected = ("Payments backlog", "is:unresolved label:namespace=pay")

    assert result == expected


def test_saving_remembers_the_sort_as_well(operator_client):
    """Should carry the whole view, not half of it."""
    save(operator_client, "Loudest", query="is:unresolved", sort="relevance")

    result = SavedView.objects.get().sort
    expected = "relevance"

    assert result == expected


def test_saving_records_who_saved_it(operator_client):
    """Should say where a view came from on a shared install."""
    save(operator_client, "Mine")

    result = SavedView.objects.get().created_by
    expected = "operator"

    assert result == expected


def test_saving_the_same_name_twice_updates_it(operator_client):
    """Should let someone refine a view rather than accumulate near-duplicates."""
    save(operator_client, "Backlog", query="is:new")

    save(operator_client, "Backlog", query="is:unresolved")

    view = SavedView.objects.get()
    result = (SavedView.objects.count(), view.query)
    expected = (1, "is:unresolved")

    assert result == expected


def test_a_view_with_no_name_is_refused(operator_client):
    """Should say what is missing instead of saving an unnamed row."""
    response = save(operator_client, "  ")
    body = operator_client.get(response.url).content.decode()

    assert "needs a name" in body and SavedView.objects.count() == 0


def test_saving_is_recorded_in_the_history(operator_client):
    """Should show up like every other change someone made."""
    save(operator_client, "Backlog")

    result = AuditEntry.objects.filter(action="view.save").count()
    expected = 1

    assert result == expected


def test_saving_lands_on_the_view_it_saved(operator_client):
    """Should show the result rather than leaving someone on a stale page."""
    response = save(operator_client, "Backlog", query="is:new", sort="relevance")

    assert "q=is%3Anew" in response.url and "sort=relevance" in response.url


# using and removing


def test_a_saved_view_appears_beside_the_segments(operator_client):
    """Should sit where someone already looks for a filter."""
    save(operator_client, "Payments backlog")

    body = operator_client.get("/").content.decode()

    assert "Payments backlog" in body


def test_the_active_view_is_marked(operator_client):
    """Should show which one you are looking at."""
    save(operator_client, "Backlog", query="is:new")

    response = operator_client.get("/", {"q": "is:new"})

    result = [view.is_current for view in response.context["views"]]
    expected = [True]

    assert result == expected


def test_a_view_can_be_deleted(operator_client):
    """Should let a view that stopped being useful go."""
    save(operator_client, "Backlog")
    view = SavedView.objects.get()

    operator_client.post(f"/views/{view.pk}/delete/")

    result = SavedView.objects.count()
    expected = 0

    assert result == expected


def test_deleting_an_absent_view_is_not_found(operator_client):
    """Should answer rather than raise on a stale button."""
    result = operator_client.post("/views/999/delete/").status_code
    expected = http.HTTPStatus.NOT_FOUND

    assert result == expected


def test_a_viewer_may_not_save_a_view(client, django_user_model):
    """Should sit behind the same permission as the rest of the write surface."""
    from pandora.people.models import Membership, Role, Team

    viewer = django_user_model.objects.create_user(
        username="viewer", password="pass", is_staff=True
    )
    Membership.objects.create(
        user=viewer, team=Team.objects.create(name="watchers"), role=Role.VIEWER
    )
    client.force_login(viewer)

    result = save(client, "Backlog").status_code
    expected = http.HTTPStatus.FORBIDDEN

    assert result == expected


def test_a_view_reads_as_its_name():
    """Should be pickable from a list in the admin."""
    result = str(SavedView(name="Payments backlog"))
    expected = "Payments backlog"

    assert result == expected


def test_a_viewer_may_not_delete_a_view(client, django_user_model):
    """Should sit behind the same permission as saving one."""
    from pandora.people.models import Membership, Role, Team

    view = SavedView.objects.create(name="Backlog", query="is:new")
    viewer = django_user_model.objects.create_user(
        username="viewer", password="pass", is_staff=True
    )
    Membership.objects.create(
        user=viewer, team=Team.objects.create(name="watchers"), role=Role.VIEWER
    )
    client.force_login(viewer)

    result = client.post(f"/views/{view.pk}/delete/").status_code
    expected = http.HTTPStatus.FORBIDDEN

    assert result == expected
