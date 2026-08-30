import base64
import datetime
import http

import pytest
from django import urls

from pandora.core import models as core_models
from pandora.issues import models as issue_models
from pandora.web import api

ISSUES_URL = "/api/v1/issues"

# route contract


def test_the_issue_routes_live_under_the_versioned_prefix():
    """Should expose every read route under /api/v1/ so consumers can pin it."""
    result = [
        urls.reverse("api-v1-issues"),
        urls.reverse("api-v1-issue", args=[7]),
        urls.reverse("api-v1-issue-events", args=[7]),
    ]
    expected = ["/api/v1/issues", "/api/v1/issues/7", "/api/v1/issues/7/events"]

    assert result == expected


def test_the_issue_routes_constrain_the_issue_to_an_integer():
    """Should hand the views an int issue id, never a string."""
    result = [
        urls.resolve("/api/v1/issues/7").kwargs,
        urls.resolve("/api/v1/issues/7/events").kwargs,
    ]
    expected = [{"issue_id": 7}, {"issue_id": 7}]

    assert result == expected


def test_a_non_numeric_issue_id_does_not_resolve():
    """Should refuse a detail path whose issue id is not a number."""
    with pytest.raises(urls.Resolver404):
        urls.resolve("/api/v1/issues/seven")


# page-size contract


def test_the_page_sizes_are_pinned():
    """Should pin the default and maximum page size the API documents."""
    result = (api.DEFAULT_LIMIT, api.MAX_LIMIT)
    expected = (50, 200)

    assert result == expected


def test_the_detail_sub_lists_are_bounded():
    """Should bound the episode and tag lists a detail response embeds."""
    result = (api.DETAIL_EPISODE_LIMIT, api.DETAIL_TAG_LIMIT)
    expected = (20, 500)

    assert result == expected


def test_one_tag_key_cannot_take_the_whole_detail_budget():
    """Should ration the tag rows per key — one id key used to eat the response."""
    result = api.DETAIL_TAG_VALUES < api.DETAIL_TAG_LIMIT
    expected = True

    assert result == expected


def test_only_safe_methods_are_served():
    """Should keep the API read-only — no write verb is ever routed."""
    result = api.SAFE_METHODS
    expected = ("GET", "HEAD")

    assert result == expected


# limit parsing


def test_a_missing_limit_falls_back_to_the_default(rf):
    """Should page at the documented default when the caller names no limit."""
    result = api.parse_limit(rf.get(ISSUES_URL).GET)
    expected = 50

    assert result == expected


def test_an_explicit_limit_is_honoured(rf):
    """Should use the caller's page size when it is within the cap."""
    result = api.parse_limit(rf.get(ISSUES_URL, {"limit": "5"}).GET)
    expected = 5

    assert result == expected


def test_an_oversized_limit_is_clamped(rf):
    """Should clamp to the cap rather than let one call scan the table."""
    result = api.parse_limit(rf.get(ISSUES_URL, {"limit": "5000"}).GET)
    expected = 200

    assert result == expected


def test_a_non_numeric_limit_is_rejected(rf):
    """Should refuse a limit that is not a number instead of guessing one."""
    with pytest.raises(api.ApiError) as error:
        api.parse_limit(rf.get(ISSUES_URL, {"limit": "many"}).GET)

    result = (error.value.status, error.value.detail)
    expected = (http.HTTPStatus.BAD_REQUEST, "limit must be a positive integer")
    assert result == expected


def test_a_zero_limit_is_rejected(rf):
    """Should refuse an empty page — a zero limit is a caller bug, not a filter."""
    with pytest.raises(api.ApiError) as error:
        api.parse_limit(rf.get(ISSUES_URL, {"limit": "0"}).GET)

    result = error.value.detail
    expected = "limit must be a positive integer"
    assert result == expected


def test_a_negative_limit_is_rejected(rf):
    """Should refuse a negative page size."""
    with pytest.raises(api.ApiError) as error:
        api.parse_limit(rf.get(ISSUES_URL, {"limit": "-5"}).GET)

    result = error.value.status
    expected = http.HTTPStatus.BAD_REQUEST
    assert result == expected


# state filter parsing


def test_absent_state_filters_parse_to_nothing(rf):
    """Should treat a missing filter as no filter, not as an empty match."""
    result = api.parse_states(rf.get(ISSUES_URL).GET, "triage_state", ["new"])
    expected = []

    assert result == expected


def test_a_blank_state_filter_is_ignored(rf):
    """Should ignore an empty parameter a caller sent with no value."""
    result = api.parse_states(
        rf.get(ISSUES_URL, {"triage_state": " "}).GET,
        "triage_state",
        ["new"],
    )
    expected = []

    assert result == expected


def test_a_state_filter_accepts_repeated_values(rf):
    """Should collect every repetition so 'open' can mean new plus ack."""
    params = rf.get(ISSUES_URL, {"triage_state": ["new", "ack"]}).GET

    result = api.parse_states(params, "triage_state", ["new", "ack"])
    expected = ["new", "ack"]

    assert result == expected


def test_an_unknown_state_names_the_valid_ones(rf):
    """Should tell the caller what it may send rather than return an empty page."""
    params = rf.get(ISSUES_URL, {"triage_state": "bogus"}).GET

    with pytest.raises(api.ApiError) as error:
        api.parse_states(params, "triage_state", issue_models.TriageState.values)

    result = (error.value.status, error.value.detail)
    expected = (
        http.HTTPStatus.BAD_REQUEST,
        "unknown triage_state 'bogus'; valid: new, ack, resolved, ignored",
    )
    assert result == expected


# timestamp parsing


def test_an_offset_timestamp_keeps_its_instant():
    """Should keep the instant a caller sent with an explicit offset."""
    result = api.parse_timestamp("2026-08-04T14:00:00+02:00", "bad")
    expected = datetime.datetime(2026, 8, 4, 12, 0, tzinfo=datetime.UTC)

    assert result == expected


def test_a_naive_timestamp_is_read_as_utc():
    """Should read an offset-less timestamp as UTC, the only timezone pandora runs in."""
    result = api.parse_timestamp("2026-08-04T12:00:00", "bad")
    expected = datetime.datetime(2026, 8, 4, 12, 0, tzinfo=datetime.UTC)

    assert result == expected


def test_an_unparseable_timestamp_is_rejected():
    """Should refuse a timestamp that is not ISO 8601."""
    with pytest.raises(api.ApiError) as error:
        api.parse_timestamp("yesterday", "since must be an ISO 8601 timestamp")

    result = (error.value.status, error.value.detail)
    expected = (http.HTTPStatus.BAD_REQUEST, "since must be an ISO 8601 timestamp")
    assert result == expected


def test_a_well_formed_impossible_date_is_rejected():
    """Should refuse a date that parses but cannot exist."""
    with pytest.raises(api.ApiError) as error:
        api.parse_timestamp(
            "2026-13-45T00:00:00Z", "since must be an ISO 8601 timestamp"
        )

    result = error.value.status
    expected = http.HTTPStatus.BAD_REQUEST
    assert result == expected


# cursor construction


def test_a_cursor_round_trips_its_position():
    """Should carry both halves of the keyset — the timestamp and the tie-break id."""
    last_seen = datetime.datetime(2026, 8, 4, 12, 0, 30, 500000, tzinfo=datetime.UTC)

    result = api.decode_cursor(api.encode_cursor(last_seen, 42))
    expected = (last_seen, 42)

    assert result == expected


def test_a_cursor_is_opaque():
    """Should hand out an urlsafe base64 blob, not a hand-editable position."""
    last_seen = datetime.datetime(2026, 8, 4, 12, 0, tzinfo=datetime.UTC)

    result = api.encode_cursor(last_seen, 42)
    expected = "MjAyNi0wOC0wNFQxMjowMDowMCswMDowMHw0Mg"

    assert result == expected


def test_a_cursor_from_a_non_utc_instant_normalises():
    """Should encode the same position no matter what timezone the row carried."""
    berlin = datetime.timezone(datetime.timedelta(hours=2))
    last_seen = datetime.datetime(2026, 8, 4, 14, 0, tzinfo=berlin)

    result = api.encode_cursor(last_seen, 42)
    expected = api.encode_cursor(
        datetime.datetime(2026, 8, 4, 12, 0, tzinfo=datetime.UTC), 42
    )

    assert result == expected


def test_a_cursor_of_junk_is_rejected():
    """Should refuse a cursor that carries no position at all."""
    with pytest.raises(api.ApiError) as error:
        api.decode_cursor("!!!!")

    result = (error.value.status, error.value.detail)
    expected = (http.HTTPStatus.BAD_REQUEST, "cursor is not readable")
    assert result == expected


def test_a_truncated_cursor_is_rejected():
    """Should refuse a cursor a consumer cut short — base64 padding no longer adds up."""
    with pytest.raises(api.ApiError) as error:
        api.decode_cursor("a")

    result = (error.value.status, error.value.detail)
    expected = (http.HTTPStatus.BAD_REQUEST, "cursor is not readable")
    assert result == expected


def test_a_cursor_that_is_not_text_is_rejected():
    """Should refuse a cursor whose bytes are not a UTF-8 position."""
    cursor = base64.urlsafe_b64encode(b"\xff\xff").decode()

    with pytest.raises(api.ApiError) as error:
        api.decode_cursor(cursor)

    result = (error.value.status, error.value.detail)
    expected = (http.HTTPStatus.BAD_REQUEST, "cursor is not readable")
    assert result == expected


def test_a_cursor_without_its_tie_break_is_rejected():
    """Should refuse a cursor carrying only half the keyset."""
    cursor = base64.urlsafe_b64encode(b"2026-08-04T12:00:00+00:00").decode()

    with pytest.raises(api.ApiError) as error:
        api.decode_cursor(cursor)

    result = error.value.detail
    expected = "cursor is not readable"
    assert result == expected


def test_a_cursor_with_a_non_numeric_id_is_rejected():
    """Should refuse a cursor whose tie-break is not a row id."""
    cursor = base64.urlsafe_b64encode(b"2026-08-04T12:00:00+00:00|abc").decode()

    with pytest.raises(api.ApiError) as error:
        api.decode_cursor(cursor)

    result = error.value.detail
    expected = "cursor is not readable"
    assert result == expected


def test_a_cursor_with_a_broken_timestamp_is_rejected():
    """Should refuse a cursor whose position is not a timestamp."""
    cursor = base64.urlsafe_b64encode(b"whenever|42").decode()

    with pytest.raises(api.ApiError) as error:
        api.decode_cursor(cursor)

    result = error.value.detail
    expected = "cursor is not readable"
    assert result == expected


# optional string parameters


def test_an_absent_cursor_parameter_is_none(rf):
    """Should read a missing cursor as the first page."""
    result = api.parse_cursor_param(rf.get(ISSUES_URL).GET)

    assert result is None


def test_a_blank_cursor_parameter_is_none(rf):
    """Should read a whitespace cursor as the first page, not as a bad cursor."""
    result = api.parse_cursor_param(rf.get(ISSUES_URL, {"cursor": "  "}).GET)

    assert result is None


def test_a_cursor_parameter_is_stripped(rf):
    """Should tolerate whitespace a consumer pasted around the cursor."""
    result = api.parse_cursor_param(rf.get(ISSUES_URL, {"cursor": " abc "}).GET)
    expected = "abc"

    assert result == expected


def test_an_absent_episode_parameter_is_none(rf):
    """Should read a missing episode filter as no filter."""
    result = api.parse_episode(rf.get(ISSUES_URL).GET)

    assert result is None


def test_an_episode_parameter_is_stripped(rf):
    """Should pass the episode key to the store without surrounding whitespace."""
    result = api.parse_episode(rf.get(ISSUES_URL, {"episode": " 12 "}).GET)
    expected = "12"

    assert result == expected


# serialisation


def test_a_missing_timestamp_serialises_as_null():
    """Should render an absent timestamp as JSON null, not as an empty string."""
    result = api.isoformat(None)

    assert result is None


def test_a_timestamp_serialises_as_utc_with_a_z():
    """Should render every timestamp in UTC with the Z suffix consumers expect."""
    berlin = datetime.timezone(datetime.timedelta(hours=2))

    result = api.isoformat(datetime.datetime(2026, 8, 4, 14, 0, tzinfo=berlin))
    expected = "2026-08-04T12:00:00Z"

    assert result == expected


def test_an_issue_serialises_to_the_documented_shape(issue):
    """Should render exactly the issue fields README documents, nothing more."""
    result = api.serialize_issue(issue)
    expected = {
        "id": issue.pk,
        "project": "infrastructure",
        "fingerprint_hash": "a" * 64,
        "title": "TargetDown: scrape target unreachable",
        "culprit": "alertname=TargetDown namespace=monitoring",
        "level": "warning",
        "environment": "p-mk1",
        "environments": ["p-mk1"],
        "source_state": "firing",
        "triage_state": "new",
        "event_count": 3,
        "open_episode_count": 1,
        "grouping_labels": {"alertname": "TargetDown", "namespace": "monitoring"},
        "first_seen": api.isoformat(issue.first_seen),
        "last_seen": api.isoformat(issue.last_seen),
        "last_resolved_at": None,
    }

    assert result == expected


def test_an_open_episode_serialises_with_a_null_end(episode):
    """Should render an open episode with ends_at null so a consumer can tell."""
    result = api.serialize_episode(episode)
    expected = {
        "id": episode.pk,
        "am_fingerprint": "3c1f6a2b9d4e5087",
        "labels": {"alertname": "TargetDown", "job": "node-exporter"},
        "environment": "p-mk1",
        "starts_at": api.isoformat(episode.starts_at),
        "ends_at": None,
        "delivery_count": 2,
        "last_delivery_at": api.isoformat(episode.last_delivery_at),
    }

    assert result == expected


def test_a_closed_episode_serialises_with_its_end(episode):
    """Should render the end of a resolved episode."""
    episode.ends_at = episode.starts_at + datetime.timedelta(minutes=30)
    episode.save()

    result = api.serialize_episode(episode)["ends_at"]
    expected = api.isoformat(episode.ends_at)

    assert result == expected


def test_a_tag_stat_serialises_to_a_flat_row(issue):
    """Should render tag stats as flat rows so the order survives JSON."""
    stat = issue_models.TagStat.objects.create(
        issue=issue,
        key="namespace",
        value="monitoring",
        count=12,
    )

    result = api.serialize_tag_stat(stat)
    expected = {"key": "namespace", "value": "monitoring", "count": 12}

    assert result == expected


# authentication


def test_a_call_without_a_token_is_unauthorised(client, read_token):
    """Should refuse an anonymous read and say which scheme it wants."""
    response = client.get(ISSUES_URL)

    result = {
        "status_code": response.status_code,
        "challenge": response["WWW-Authenticate"],
        "body": response.json(),
    }
    expected = {
        "status_code": http.HTTPStatus.UNAUTHORIZED,
        "challenge": "Bearer",
        "body": {"detail": "bearer token required"},
    }
    assert result == expected


def test_another_scheme_is_refused(client, read_token):
    """Should refuse Basic auth — the API speaks bearer tokens only."""
    response = client.get(ISSUES_URL, headers={"authorization": "Basic dXNlcjpwdw=="})

    result = (response.status_code, response.json())
    expected = (http.HTTPStatus.UNAUTHORIZED, {"detail": "bearer token required"})
    assert result == expected


def test_an_empty_bearer_value_is_refused(client, read_token):
    """Should refuse a header that names the scheme but carries no token."""
    response = client.get(ISSUES_URL, headers={"authorization": "Bearer "})

    result = (response.status_code, response.json())
    expected = (http.HTTPStatus.UNAUTHORIZED, {"detail": "bearer token required"})
    assert result == expected


def test_an_unknown_token_is_refused(client, read_token):
    """Should refuse a token that matches no row."""
    response = client.get(ISSUES_URL, headers={"authorization": "Bearer nope"})

    result = (response.status_code, response.json())
    expected = (http.HTTPStatus.UNAUTHORIZED, {"detail": "unknown token"})
    assert result == expected


def test_a_non_ascii_token_is_refused(client, read_token):
    """Should compare a non-ASCII token as bytes rather than raise on it."""
    response = client.get(ISSUES_URL, headers={"authorization": "Bearer café"})

    result = response.status_code
    expected = http.HTTPStatus.UNAUTHORIZED
    assert result == expected


def test_a_deactivated_token_is_refused(client, read_token):
    """Should stop honouring a token the moment it is deactivated."""
    read_token.active = False
    read_token.save()

    response = client.get(
        ISSUES_URL, headers={"authorization": "Bearer test-read-token"}
    )

    result = (response.status_code, response.json())
    expected = (http.HTTPStatus.UNAUTHORIZED, {"detail": "unknown token"})
    assert result == expected


def test_an_ingest_token_may_not_read(client, token):
    """Should refuse the webhook token — ingest and read are separate scopes."""
    response = client.get(
        ISSUES_URL,
        headers={"authorization": f"Bearer {token.token}"},
    )

    result = (response.status_code, response.json())
    expected = (
        http.HTTPStatus.FORBIDDEN,
        {"detail": "token scope 'ingest' cannot read"},
    )
    assert result == expected


def test_a_read_token_is_accepted(client, auth):
    """Should serve a read-scoped token."""
    response = client.get(ISSUES_URL, headers=auth)

    result = (response.status_code, response.json())
    expected = (http.HTTPStatus.OK, {"results": [], "next_cursor": None})
    assert result == expected


def test_a_write_verb_is_refused(client, auth):
    """Should answer 405 with an Allow header — the API never writes."""
    response = client.post(ISSUES_URL, headers=auth)

    result = {
        "status_code": response.status_code,
        "allow": response["Allow"],
        "body": response.json(),
    }
    expected = {
        "status_code": http.HTTPStatus.METHOD_NOT_ALLOWED,
        "allow": "GET, HEAD",
        "body": {"detail": "read-only endpoint"},
    }
    assert result == expected


def test_a_write_verb_is_refused_before_the_token_is_read(
    client, db, django_assert_num_queries
):
    """Should reject the verb without touching the database."""
    with django_assert_num_queries(0):
        response = client.post(ISSUES_URL)

    result = response.status_code
    expected = http.HTTPStatus.METHOD_NOT_ALLOWED
    assert result == expected


def test_a_head_request_is_served(client, auth):
    """Should serve HEAD so a consumer can probe the endpoint cheaply."""
    response = client.head(ISSUES_URL, headers=auth)

    result = response.status_code
    expected = http.HTTPStatus.OK
    assert result == expected


def test_the_list_costs_three_queries(client, auth, ladder, django_assert_num_queries):
    """Should authenticate, page and prefetch environments — nothing per row."""
    with django_assert_num_queries(3):
        client.get(ISSUES_URL, headers=auth)


# list behaviour


def test_the_list_returns_the_issues_of_the_token_project(client, auth, issue):
    """Should render the project's issues in the documented envelope."""
    response = client.get(ISSUES_URL, headers=auth)

    result = response.json()
    expected = {"results": [api.serialize_issue(issue)], "next_cursor": None}

    assert result == expected


def test_the_list_never_leaves_the_token_project(client, auth, issue, other_project):
    """Should keep a read token inside the project it was issued for."""
    issue_models.Issue.objects.create(
        project=other_project,
        fingerprint_hash="b" * 64,
        title="an issue of another project",
    )

    response = client.get(ISSUES_URL, headers=auth)

    result = [row["title"] for row in response.json()["results"]]
    expected = ["TargetDown: scrape target unreachable"]

    assert result == expected


def test_the_list_is_ordered_newest_first(client, auth, ladder):
    """Should sort by last seen descending — the changelist index order."""
    response = client.get(ISSUES_URL, headers=auth)

    result = [row["id"] for row in response.json()["results"]]
    expected = [issue.pk for issue in ladder]

    assert result == expected


def test_a_full_page_hands_back_a_cursor(client, auth, ladder):
    """Should return a cursor whenever more rows are waiting behind the page."""
    response = client.get(ISSUES_URL, {"limit": "2"}, headers=auth)

    payload = response.json()
    result = ([row["id"] for row in payload["results"]], payload["next_cursor"])
    expected = (
        [ladder[0].pk, ladder[1].pk],
        api.encode_cursor(ladder[1].last_seen, ladder[1].pk),
    )

    assert result == expected


def test_the_last_page_hands_back_no_cursor(client, auth, ladder):
    """Should end the walk with a null cursor rather than an empty extra page."""
    response = client.get(ISSUES_URL, {"limit": "5"}, headers=auth)

    result = response.json()["next_cursor"]

    assert result is None


def test_walking_the_cursor_covers_every_issue_once(client, auth, ladder):
    """Should page through the whole list with no row repeated or skipped."""
    seen = []
    cursor = None
    for _ in range(3):
        query = {"limit": "2"}
        if cursor is not None:
            query["cursor"] = cursor
        payload = client.get(ISSUES_URL, query, headers=auth).json()
        seen.extend(row["id"] for row in payload["results"])
        cursor = payload["next_cursor"]

    result = (seen, cursor)
    expected = ([issue.pk for issue in ladder], None)

    assert result == expected


def test_issues_sharing_a_last_seen_are_paged_by_id(client, auth, make_issue):
    """Should break a timestamp tie by id so a page boundary loses nothing."""
    tied = [make_issue() for _ in range(3)]

    first = client.get(ISSUES_URL, {"limit": "2"}, headers=auth).json()
    second = client.get(
        ISSUES_URL,
        {"limit": "2", "cursor": first["next_cursor"]},
        headers=auth,
    ).json()

    result = [row["id"] for row in first["results"] + second["results"]]
    expected = sorted((issue.pk for issue in tied), reverse=True)

    assert result == expected


def test_an_unreadable_cursor_is_rejected_over_http(client, auth, ladder):
    """Should answer 400 rather than 500 when a consumer sends a broken cursor."""
    response = client.get(ISSUES_URL, {"cursor": "!!!!"}, headers=auth)

    result = (response.status_code, response.json())
    expected = (http.HTTPStatus.BAD_REQUEST, {"detail": "cursor is not readable"})
    assert result == expected


# list filters


def test_the_triage_state_filter_selects_one_state(client, auth, make_issue):
    """Should return only the issues in the triage state a caller asked for."""
    make_issue(title="still new")
    make_issue(
        title="already acknowledged", triage_state=issue_models.TriageState.ACKNOWLEDGED
    )

    response = client.get(ISSUES_URL, {"triage_state": "ack"}, headers=auth)

    result = [row["title"] for row in response.json()["results"]]
    expected = ["already acknowledged"]

    assert result == expected


def test_the_triage_state_filter_accepts_several_states(client, auth, make_issue):
    """Should union repeated values so 'open' is new plus acknowledged."""
    make_issue(title="still new")
    make_issue(
        title="already acknowledged", triage_state=issue_models.TriageState.ACKNOWLEDGED
    )
    make_issue(title="closed", triage_state=issue_models.TriageState.RESOLVED)

    response = client.get(
        ISSUES_URL,
        {"triage_state": ["new", "ack"]},
        headers=auth,
    )

    result = sorted(row["title"] for row in response.json()["results"])
    expected = ["already acknowledged", "still new"]

    assert result == expected


def test_an_unknown_triage_state_is_rejected(client, auth):
    """Should answer 400 for a state that does not exist, not an empty page."""
    response = client.get(ISSUES_URL, {"triage_state": "bogus"}, headers=auth)

    result = (response.status_code, response.json())
    expected = (
        http.HTTPStatus.BAD_REQUEST,
        {"detail": "unknown triage_state 'bogus'; valid: new, ack, resolved, ignored"},
    )
    assert result == expected


def test_the_source_state_filter_selects_firing_issues(client, auth, make_issue):
    """Should filter on what Alertmanager says, separately from triage."""
    make_issue(title="firing now")
    make_issue(
        title="resolved itself",
        source_state=issue_models.SourceState.RESOLVED,
        open_episode_count=0,
    )

    response = client.get(ISSUES_URL, {"source_state": "firing"}, headers=auth)

    result = [row["title"] for row in response.json()["results"]]
    expected = ["firing now"]

    assert result == expected


def test_an_unknown_source_state_is_rejected(client, auth):
    """Should answer 400 for a source state Alertmanager never sends."""
    response = client.get(ISSUES_URL, {"source_state": "flapping"}, headers=auth)

    result = (response.status_code, response.json())
    expected = (
        http.HTTPStatus.BAD_REQUEST,
        {"detail": "unknown source_state 'flapping'; valid: firing, resolved"},
    )
    assert result == expected


def test_the_environment_filter_selects_one_cluster(client, auth, make_issue):
    """Should filter by environment so one pandora can hold several clusters."""
    make_issue(title="on p-mk1")
    make_issue(title="on p-mk2", environment="p-mk2")

    response = client.get(ISSUES_URL, {"environment": "p-mk2"}, headers=auth)

    result = [row["title"] for row in response.json()["results"]]
    expected = ["on p-mk2"]

    assert result == expected


def test_the_since_filter_selects_recently_active_issues(client, auth, make_issue):
    """Should return the issues seen since the instant a poller last checked."""
    recent = make_issue(title="seen just now")
    make_issue(
        title="quiet for a day",
        last_seen=recent.last_seen - datetime.timedelta(days=1),
    )

    response = client.get(
        ISSUES_URL,
        {"since": api.isoformat(recent.last_seen - datetime.timedelta(hours=1))},
        headers=auth,
    )

    result = [row["title"] for row in response.json()["results"]]
    expected = ["seen just now"]

    assert result == expected


def test_an_unparseable_since_is_rejected(client, auth):
    """Should answer 400 for a since value that is not a timestamp."""
    response = client.get(ISSUES_URL, {"since": "yesterday"}, headers=auth)

    result = (response.status_code, response.json())
    expected = (
        http.HTTPStatus.BAD_REQUEST,
        {"detail": "since must be an ISO 8601 timestamp"},
    )
    assert result == expected


def test_the_project_filter_accepts_the_token_project(client, auth, issue):
    """Should let a caller name the project it already has access to."""
    response = client.get(ISSUES_URL, {"project": "infrastructure"}, headers=auth)

    result = [row["project"] for row in response.json()["results"]]
    expected = ["infrastructure"]

    assert result == expected


def test_the_project_filter_cannot_reach_another_project(
    client, auth, issue, other_project
):
    """Should return nothing when the filter names a project the token cannot see."""
    issue_models.Issue.objects.create(
        project=other_project,
        fingerprint_hash="c" * 64,
        title="an issue of another project",
    )

    response = client.get(ISSUES_URL, {"project": "apps"}, headers=auth)

    result = response.json()["results"]
    expected = []

    assert result == expected


# detail behaviour


def test_the_detail_carries_the_fingerprint_components(client, auth, issue):
    """Should expose the fingerprint parts the list omits."""
    response = client.get(f"{ISSUES_URL}/{issue.pk}", headers=auth)

    result = response.json()["fingerprint"]
    expected = ["alertname:TargetDown", "namespace:monitoring"]

    assert result == expected


def test_the_detail_embeds_the_issue_fields(client, auth, issue):
    """Should repeat every list field so one call is enough to render a page."""
    response = client.get(f"{ISSUES_URL}/{issue.pk}", headers=auth)

    payload = response.json()
    result = {key: payload[key] for key in api.serialize_issue(issue)}
    expected = api.serialize_issue(issue)

    assert result == expected


def test_the_detail_embeds_the_episodes_newest_first(client, auth, issue, episode):
    """Should show the episode timeline with the newest episode at the top."""
    older = issue_models.Episode.objects.create(
        project=issue.project,
        issue=issue,
        am_fingerprint="0000000000000001",
        starts_at=episode.starts_at - datetime.timedelta(hours=1),
        ends_at=episode.starts_at,
    )

    response = client.get(f"{ISSUES_URL}/{issue.pk}", headers=auth)

    result = [row["id"] for row in response.json()["episodes"]]
    expected = [episode.pk, older.pk]

    assert result == expected


def test_the_detail_bounds_the_episode_list(client, auth, issue):
    """Should stop at the documented episode cap on a long-running issue."""
    issue_models.Episode.objects.bulk_create(
        issue_models.Episode(
            project=issue.project,
            issue=issue,
            am_fingerprint=f"{index:016d}",
            starts_at=issue.last_seen - datetime.timedelta(minutes=index),
        )
        for index in range(api.DETAIL_EPISODE_LIMIT + 5)
    )

    response = client.get(f"{ISSUES_URL}/{issue.pk}", headers=auth)

    result = len(response.json()["episodes"])
    expected = 20

    assert result == expected


def test_the_detail_orders_tags_by_key_then_frequency(client, auth, issue):
    """Should group tag rows by key and put the commonest value first."""
    issue_models.TagStat.objects.bulk_create(
        [
            issue_models.TagStat(issue=issue, key="severity", value="warning", count=2),
            issue_models.TagStat(
                issue=issue, key="namespace", value="monitoring", count=1
            ),
            issue_models.TagStat(
                issue=issue, key="namespace", value="flux-system", count=9
            ),
        ]
    )

    response = client.get(f"{ISSUES_URL}/{issue.pk}", headers=auth)

    result = [(row["key"], row["value"]) for row in response.json()["tag_stats"]]
    expected = [
        ("namespace", "flux-system"),
        ("namespace", "monitoring"),
        ("severity", "warning"),
    ]

    assert result == expected


def test_the_detail_rations_tag_rows_per_key(client, auth, issue):
    """Should cut a high-cardinality key down to its top values."""
    issue_models.TagStat.objects.bulk_create(
        [
            issue_models.TagStat(
                issue=issue, key="celery_task_id", value=f"id-{index:04d}", count=1
            )
            for index in range(50)
        ]
    )

    response = client.get(f"{ISSUES_URL}/{issue.pk}", headers=auth)

    result = len(response.json()["tag_stats"])
    expected = api.DETAIL_TAG_VALUES

    assert result == expected


def test_a_rationed_key_leaves_room_for_the_others(client, auth, issue):
    """Should still show the key an operator actually wants to read."""
    issue_models.TagStat.objects.bulk_create(
        [
            issue_models.TagStat(
                issue=issue, key="celery_task_id", value=f"id-{index:04d}", count=1
            )
            for index in range(50)
        ]
        + [issue_models.TagStat(issue=issue, key="source", value="corepilot", count=7)]
    )

    response = client.get(f"{ISSUES_URL}/{issue.pk}", headers=auth)

    result = [row for row in response.json()["tag_stats"] if row["key"] == "source"]
    expected = [{"key": "source", "value": "corepilot", "count": 7}]

    assert result == expected


def test_the_rationed_rows_are_the_most_frequent_ones(client, auth, issue):
    """Should keep the top values, not whichever the index reached first."""
    issue_models.TagStat.objects.bulk_create(
        [
            issue_models.TagStat(
                issue=issue, key="source", value=f"board-{index:04d}", count=index
            )
            for index in range(api.DETAIL_TAG_VALUES + 5)
        ]
    )

    response = client.get(f"{ISSUES_URL}/{issue.pk}", headers=auth)

    result = [row["count"] for row in response.json()["tag_stats"]]
    expected = sorted(result, reverse=True)

    assert result == expected
    assert min(result) == 5


def test_the_detail_still_bounds_the_whole_tag_list(client, auth, issue, monkeypatch):
    """Should stop at the response budget however many keys an issue collected."""
    monkeypatch.setattr(api, "DETAIL_TAG_LIMIT", 4)
    issue_models.TagStat.objects.bulk_create(
        [
            issue_models.TagStat(
                issue=issue, key=f"key-{index:02d}", value="one", count=1
            )
            for index in range(6)
        ]
    )

    response = client.get(f"{ISSUES_URL}/{issue.pk}", headers=auth)

    result = len(response.json()["tag_stats"])
    expected = 4

    assert result == expected


def test_an_unknown_issue_is_not_found(client, auth):
    """Should answer a JSON 404 rather than Django's HTML page."""
    response = client.get(f"{ISSUES_URL}/999999", headers=auth)

    result = (response.status_code, response.json())
    expected = (http.HTTPStatus.NOT_FOUND, {"detail": "issue not found"})
    assert result == expected


def test_an_issue_of_another_project_is_not_found(client, auth, other_project):
    """Should hide another project's issue behind 404, never 403."""
    hidden = issue_models.Issue.objects.create(
        project=other_project,
        fingerprint_hash="d" * 64,
        title="an issue of another project",
    )

    response = client.get(f"{ISSUES_URL}/{hidden.pk}", headers=auth)

    result = (response.status_code, response.json())
    expected = (http.HTTPStatus.NOT_FOUND, {"detail": "issue not found"})
    assert result == expected


def test_the_detail_needs_a_token_too(client, issue, read_token):
    """Should refuse an anonymous detail read."""
    response = client.get(f"{ISSUES_URL}/{issue.pk}")

    result = response.status_code
    expected = http.HTTPStatus.UNAUTHORIZED
    assert result == expected


def test_the_detail_costs_five_queries(client, auth, issue, django_assert_num_queries):
    """Should authenticate, load the issue and each embedded list once."""
    with django_assert_num_queries(5):
        client.get(f"{ISSUES_URL}/{issue.pk}", headers=auth)


# token scoping


def test_a_token_of_another_project_sees_its_own_issues(client, other_project, issue):
    """Should scope by the token's project rather than by a request parameter."""
    other_token = core_models.IngestToken.objects.create(
        project=other_project,
        name="apps reader",
        token="test-other-read-token",
        scope=core_models.TokenScope.READ,
    )
    mine = issue_models.Issue.objects.create(
        project=other_project,
        fingerprint_hash="e" * 64,
        title="an issue of the apps project",
    )

    response = client.get(
        ISSUES_URL,
        headers={"authorization": f"Bearer {other_token.token}"},
    )

    result = [row["id"] for row in response.json()["results"]]
    expected = [mine.pk]

    assert result == expected


def test_the_api_filter_matches_either_environment(client, auth, make_issue):
    """Should find an issue from whichever cluster the caller knows about."""
    from pandora.issues import environments

    issue = make_issue(title="Both", environment="p-mk1")
    environments.record(issue, "p-mk2", issue.last_seen)

    response = client.get(ISSUES_URL, {"environment": "p-mk2"}, headers=auth)

    result = [row["title"] for row in response.json()["results"]]
    expected = ["Both"]

    assert result == expected


def test_the_serialised_issue_names_every_environment(client, auth, make_issue):
    """Should let a consumer see the spread without a second request."""
    from pandora.issues import environments

    issue = make_issue(title="Both", environment="p-mk1")
    environments.record(issue, "p-mk2", issue.last_seen)

    response = client.get(f"{ISSUES_URL}/{issue.pk}", headers=auth)

    result = response.json()["environments"]
    expected = ["p-mk1", "p-mk2"]

    assert result == expected
