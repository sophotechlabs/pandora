from pandora.web import dashboard

# dashboard_callback tests


def test_the_callback_passes_the_context_through(rf):
    """Should hand unfold its context back untouched until Phase 3 fills it."""
    context = {"title": "pandora", "kpis": ()}

    result = dashboard.dashboard_callback(rf.get("/admin/"), context)
    expected = {"title": "pandora", "kpis": ()}

    assert result == expected


def test_the_callback_returns_the_same_object(rf):
    """Should not copy the context — unfold mutates the dict it passed in."""
    context = {}

    result = dashboard.dashboard_callback(rf.get("/admin/"), context)

    assert result is context
