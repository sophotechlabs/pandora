import pytest

from pandora.scrub import inbound, service
from pandora.scrub import models as scrub_models


def event(**overrides):
    payload = {"event_id": "b" * 32, "message": "boom"}
    payload.update(overrides)
    return payload


def exception(value, frames=()):
    return {
        "exception": {
            "values": [
                {
                    "type": "Error",
                    "value": value,
                    "stacktrace": {"frames": list(frames)},
                }
            ]
        }
    }


# browser extensions


def test_a_frame_from_a_chrome_extension_is_refused():
    """Should drop the errors an operator can do nothing about."""
    payload = event(
        **exception("boom", [{"abs_path": "chrome-extension://abc/inject.js"}])
    )

    assert inbound.matches(inbound.BROWSER_EXTENSION, payload) is True


def test_a_frame_from_a_firefox_extension_is_refused():
    """Should cover the other browsers' schemes, not only Chrome's."""
    payload = event(
        **exception("boom", [{"filename": "moz-extension://abc/inject.js"}])
    )

    assert inbound.matches(inbound.BROWSER_EXTENSION, payload) is True


def test_a_known_extension_message_is_refused():
    """Should catch the classic messages that carry no usable stack."""
    payload = event(**exception("top.GLOBALS is not defined"))

    assert inbound.matches(inbound.BROWSER_EXTENSION, payload) is True


def test_an_ordinary_frame_is_kept():
    """Should not refuse an error from the application's own code."""
    payload = event(**exception("boom", [{"abs_path": "/app/main.js"}]))

    assert inbound.matches(inbound.BROWSER_EXTENSION, payload) is False


# legacy browsers


@pytest.mark.parametrize(
    ("name", "version"),
    [("IE", "11.0"), ("Internet Explorer", "10"), ("Opera Mini", "8")],
)
def test_an_old_browser_is_refused(name, version):
    """Should drop the browsers nobody is going to fix a bug for."""
    payload = event(contexts={"browser": {"name": name, "version": version}})

    assert inbound.matches(inbound.LEGACY_BROWSER, payload) is True


def test_a_current_browser_is_kept():
    """Should keep the browsers people actually use."""
    payload = event(contexts={"browser": {"name": "Chrome", "version": "141.0"}})

    assert inbound.matches(inbound.LEGACY_BROWSER, payload) is False


def test_a_browser_with_no_version_is_judged_by_name():
    """Should still refuse Internet Explorer when the SDK sent no version."""
    payload = event(contexts={"browser": {"name": "IE"}})

    assert inbound.matches(inbound.LEGACY_BROWSER, payload) is True


def test_an_unknown_browser_is_kept():
    """Should not guess about a browser the list has never heard of."""
    payload = event(contexts={"browser": {"name": "Ladybird", "version": "1"}})

    assert inbound.matches(inbound.LEGACY_BROWSER, payload) is False


def test_an_event_with_no_browser_context_is_kept():
    """Should leave every server-side error alone."""
    assert inbound.matches(inbound.LEGACY_BROWSER, event()) is False


# localhost


def test_an_error_from_localhost_is_refused():
    """Should keep a developer's own machine out of the production stream."""
    payload = event(request={"url": "http://localhost:8000/checkout"})

    assert inbound.matches(inbound.LOCALHOST, payload) is True


def test_an_error_from_the_loopback_address_is_refused():
    """Should catch the address form as well as the name."""
    payload = event(user={"ip_address": "127.0.0.1"})

    assert inbound.matches(inbound.LOCALHOST, payload) is True


def test_an_error_from_a_real_host_is_kept():
    """Should not refuse production traffic."""
    payload = event(request={"url": "https://app.test/checkout"})

    assert inbound.matches(inbound.LOCALHOST, payload) is False


# crawlers


def test_a_crawler_user_agent_is_refused():
    """Should stop a search engine filling the stream with 500s."""
    payload = event(
        request={"headers": {"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"}}
    )

    assert inbound.matches(inbound.WEB_CRAWLER, payload) is True


def test_a_header_list_is_read_the_same_way():
    """Should accept the list-of-pairs form some SDKs send."""
    payload = event(request={"headers": [["user-agent", "AhrefsBot/7.0"]]})

    assert inbound.matches(inbound.WEB_CRAWLER, payload) is True


def test_a_browser_user_agent_is_kept():
    """Should keep a real person's error."""
    payload = event(
        request={
            "headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/141 Safari/537.36"
                )
            }
        }
    )

    assert inbound.matches(inbound.WEB_CRAWLER, payload) is False


# health checks


@pytest.mark.parametrize("path", ["/healthz", "/ready", "GET /health"])
def test_a_health_check_transaction_is_refused(path):
    """Should keep the probe that runs every second out of the stream."""
    assert inbound.matches(inbound.HEALTH_CHECK, event(transaction=path)) is True


def test_a_health_check_url_is_refused():
    """Should also catch it when the SDK named no transaction."""
    payload = event(request={"url": "https://app.test/healthz"})

    assert inbound.matches(inbound.HEALTH_CHECK, payload) is True


def test_an_ordinary_transaction_is_kept():
    """Should not refuse a real endpoint whose name merely contains a word."""
    assert (
        inbound.matches(inbound.HEALTH_CHECK, event(transaction="/checkout")) is False
    )


# chunk load and hydration


@pytest.mark.parametrize(
    "message",
    [
        "ChunkLoadError",
        "Loading chunk 7 failed",
        "Hydration failed because the initial UI does not match",
        "Minified React error #418",
        "Failed to fetch dynamically imported module: /assets/x.js",
    ],
)
def test_a_deploy_race_error_is_refused(message):
    """Should drop the errors a deploy causes and a reload fixes."""
    assert inbound.matches(inbound.CHUNK_LOAD, event(**exception(message))) is True


def test_an_ordinary_error_is_kept():
    """Should not refuse a real fault that happens to be in the browser."""
    assert inbound.matches(inbound.CHUNK_LOAD, event(**exception("boom"))) is False


# allowed domains


def test_an_event_from_an_unlisted_domain_is_refused():
    """Should stop somebody else's site reporting into this project."""
    payload = event(request={"url": "https://elsewhere.test/checkout"})
    options = {"domains": ["app.test", "*.app.test"]}

    assert inbound.matches(inbound.ALLOWED_DOMAINS, payload, options) is True


def test_an_event_from_a_listed_domain_is_kept():
    """Should let the operator's own site through."""
    payload = event(request={"url": "https://www.app.test/checkout"})
    options = {"domains": ["app.test", "*.app.test"]}

    assert inbound.matches(inbound.ALLOWED_DOMAINS, payload, options) is False


def test_no_configured_domains_refuses_nothing():
    """Should stay inert until the operator lists a domain."""
    payload = event(request={"url": "https://elsewhere.test/checkout"})

    assert inbound.matches(inbound.ALLOWED_DOMAINS, payload, {}) is False


def test_an_event_with_no_url_is_kept():
    """Should not refuse a server-side error that carries no request."""
    options = {"domains": ["app.test"]}

    assert inbound.matches(inbound.ALLOWED_DOMAINS, event(), options) is False


def test_an_unknown_kind_refuses_nothing():
    """Should ignore a row whose kind the code no longer knows."""
    assert inbound.matches("mystery", event()) is False


# the gate


@pytest.mark.django_db
def test_an_enabled_filter_refuses_the_payload(project):
    """Should be what the ingest path asks, in one call for both halves."""
    scrub_models.InboundFilter.objects.create(project=project, kind=inbound.LOCALHOST)
    payload = event(request={"url": "http://localhost:8000/checkout"})

    refusal = service.refused(payload, project)

    assert refusal is not None
    assert refusal.name == inbound.LOCALHOST


@pytest.mark.django_db
def test_a_disabled_filter_refuses_nothing(project):
    """Should be a switch, so turning it off actually turns it off."""
    scrub_models.InboundFilter.objects.create(
        project=project, kind=inbound.LOCALHOST, active=False
    )
    payload = event(request={"url": "http://localhost:8000/checkout"})

    assert service.refused(payload, project) is None


@pytest.mark.django_db
def test_a_filter_for_another_project_does_not_apply(project):
    """Should keep one project's policy out of another's ingest."""
    from pandora.core import models as core_models

    other = core_models.Project.objects.create(slug="other", name="Other")
    scrub_models.InboundFilter.objects.create(project=other, kind=inbound.LOCALHOST)
    payload = event(request={"url": "http://localhost:8000/checkout"})

    assert service.refused(payload, project) is None


@pytest.mark.django_db
def test_a_global_filter_applies_to_every_project(project):
    """Should let one row cover an install that never split into projects."""
    scrub_models.InboundFilter.objects.create(project=None, kind=inbound.LOCALHOST)
    payload = event(request={"url": "http://localhost:8000/checkout"})

    assert service.refused(payload, project) is not None


@pytest.mark.django_db
def test_a_refusal_is_counted(project):
    """Should say how much a filter is doing, so a useless one can be removed."""
    row = scrub_models.InboundFilter.objects.create(
        project=project, kind=inbound.LOCALHOST
    )
    payload = event(request={"url": "http://localhost:8000/checkout"})

    refusal = service.refused(payload, project)
    service.record_refusal(refusal, "sdk")

    row.refresh_from_db()
    assert row.dropped == 1


@pytest.mark.django_db
def test_a_drop_rule_still_wins_the_report(project):
    """Should keep naming the operator's own rule when that is what refused it."""
    scrub_models.DropRule.objects.create(
        project=project, name="noisy", field="message", pattern="boom"
    )
    scrub_models.InboundFilter.objects.create(project=project, kind=inbound.LOCALHOST)
    payload = event(
        request={"url": "http://localhost:8000/checkout"},
        logentry={"formatted": "boom"},
    )

    refusal = service.refused(payload, project)

    assert refusal is not None
    assert refusal.name == "noisy"


# edges the standard filters have to survive


def test_a_payload_with_a_malformed_exception_matches_nothing():
    """Should not raise on a body that is the wrong shape."""
    payload = {"exception": "boom"}

    assert inbound.matches(inbound.BROWSER_EXTENSION, payload) is False


def test_an_exception_list_without_a_wrapper_is_read():
    """Should accept the bare-list form some SDKs send."""
    payload = {
        "exception": [
            {
                "value": "boom",
                "stacktrace": {"frames": [{"abs_path": "moz-extension://a"}]},
            }
        ]
    }

    assert inbound.matches(inbound.BROWSER_EXTENSION, payload) is True


def test_a_stacktrace_of_the_wrong_shape_matches_nothing():
    """Should skip a frame list that is not a list."""
    payload = {
        "exception": {"values": [{"stacktrace": {"frames": "boom"}}]},
    }

    assert inbound.matches(inbound.BROWSER_EXTENSION, payload) is False


def test_a_logentry_message_is_searched():
    """Should read the message wherever the SDK put it."""
    payload = {"logentry": {"formatted": "ChunkLoadError: boom"}}

    assert inbound.matches(inbound.CHUNK_LOAD, payload) is True


def test_a_request_of_the_wrong_shape_matches_nothing():
    """Should not raise when the request interface is a string."""
    payload = {"request": "https://app.test"}

    result = (
        inbound.matches(inbound.LOCALHOST, payload),
        inbound.matches(inbound.WEB_CRAWLER, payload),
        inbound.matches(inbound.HEALTH_CHECK, payload),
    )

    assert result == (False, False, False)


def test_a_user_of_the_wrong_shape_matches_nothing():
    """Should not read a string user as an address."""
    assert inbound.matches(inbound.LOCALHOST, {"user": "arch"}) is False


def test_headers_of_the_wrong_shape_match_nothing():
    """Should skip a header list whose entries are not pairs."""
    payload = {"request": {"headers": [["user-agent"], "boom"]}}

    assert inbound.matches(inbound.WEB_CRAWLER, payload) is False


def test_a_browser_version_that_is_not_a_number_is_judged_by_name():
    """Should fall back to the name rather than crash on a version string."""
    payload = {"contexts": {"browser": {"name": "IE", "version": "beta"}}}

    assert inbound.matches(inbound.LEGACY_BROWSER, payload) is True


def test_a_modern_browser_with_an_unreadable_version_is_kept():
    """Should not refuse Chrome because its version did not parse."""
    payload = {"contexts": {"browser": {"name": "Chrome", "version": "beta"}}}

    assert inbound.matches(inbound.LEGACY_BROWSER, payload) is False


def test_a_context_block_of_the_wrong_shape_matches_nothing():
    """Should not raise when contexts is a list."""
    assert inbound.matches(inbound.LEGACY_BROWSER, {"contexts": []}) is False


def test_a_browser_context_of_the_wrong_shape_matches_nothing():
    """Should not raise when one context is a string."""
    payload = {"contexts": {"browser": "chrome"}}

    assert inbound.matches(inbound.LEGACY_BROWSER, payload) is False


def test_an_unparseable_url_has_no_host():
    """Should not refuse an event because its URL was nonsense."""
    payload = {"request": {"url": "not a url"}}

    assert inbound.matches(inbound.LOCALHOST, payload) is False


def test_allowed_domains_ignores_a_malformed_option():
    """Should stay inert rather than refuse everything on a bad config."""
    payload = {"request": {"url": "https://elsewhere.test/"}}

    result = (
        inbound.matches(inbound.ALLOWED_DOMAINS, payload, {"domains": "app.test"}),
        inbound.matches(inbound.ALLOWED_DOMAINS, payload, "app.test"),
    )

    assert result == (False, False)


def test_a_health_transaction_with_a_trailing_slash_is_refused():
    """Should not miss the probe because of one character."""
    assert inbound.matches(inbound.HEALTH_CHECK, {"transaction": "/healthz/"}) is True


def test_an_empty_transaction_and_url_is_kept():
    """Should not treat an event with no route as a health check."""
    assert inbound.matches(inbound.HEALTH_CHECK, {"transaction": ""}) is False
