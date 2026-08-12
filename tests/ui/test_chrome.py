import pytest

from pandora.issues import actions
from pandora.ui import context, views

pytestmark = pytest.mark.django_db


def test_an_unnamed_deployment_shows_no_badge(operator_client, settings):
    """Should keep the top bar clean when there is nothing to warn about."""
    settings.PANDORA_ENV = ""

    page = operator_client.get("/").content.decode()

    assert "env env-" not in page


def test_the_environment_is_shouted_in_the_top_bar(operator_client, settings):
    """Should make it obvious which pandora the operator is looking at."""
    settings.PANDORA_ENV = "prod"

    page = operator_client.get("/").content.decode()

    assert "PROD" in page
    assert "env-danger" in page


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ("local", "info"),
        ("dev", "info"),
        ("staging", "warning"),
        ("production", "danger"),
        ("p-mk1", "info"),
    ],
)
def test_each_environment_gets_a_tone(rf, settings, environment, expected):
    """Should paint production differently from a laptop."""
    settings.PANDORA_ENV = environment

    result = context.chrome(rf.get("/"))["pandora_env_tone"]

    assert result == expected


def test_the_nav_marks_where_the_reader_is(operator_client):
    """Should highlight the section, not just link to it."""
    page = " ".join(operator_client.get("/overview/").content.decode().split())

    assert '<a class="active" href="/overview/">Overview</a>' in page
    assert '<a class="" href="/ingest/">Ingest</a>' in page


def test_every_page_links_back_to_the_admin(operator_client):
    """Should keep the config surface one click away."""
    page = operator_client.get("/").content.decode()

    assert 'href="/admin/"' in page


def test_a_missing_page_stays_inside_the_ui(operator_client):
    """Should answer a bad address in pandora's own chrome, not Django's."""
    response = operator_client.get("/no-such-page/")
    page = response.content.decode()

    assert response.status_code == 404
    assert "Back to issues" in page
    assert "mainnav" in page


def test_the_stylesheet_and_script_are_served_from_pandora(operator_client):
    """Should carry no third-party asset — this runs on private networks."""
    page = operator_client.get("/").content.decode()

    assert "ui/pandora.css" in page
    assert "ui/pandora.js" in page
    assert "//cdn" not in page


def test_both_surfaces_offer_the_same_silence_windows(operator_client, make_issue):
    """Should not let the stream and the issue page drift apart on durations."""
    issue = make_issue()

    stream = operator_client.get("/").content.decode()
    detail = operator_client.get(f"/issues/{issue.pk}/").content.decode()

    for window in ("silence:1h", "silence:4h", "silence:1d"):
        assert window in stream
        assert window in detail


def test_the_silence_windows_match_what_the_action_accepts():
    """Should offer only windows the handler will honour."""
    result = [window for window, _ in views.SILENCE_LABELS]
    expected = list(actions.SILENCE_WINDOWS)

    assert result == expected
