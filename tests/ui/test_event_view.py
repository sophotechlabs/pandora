from pandora.ui import event_view


def frame(**overrides):
    built = {
        "module": "checkout.gateway",
        "function": "charge",
        "filename": "checkout/gateway.py",
        "lineno": 141,
        "in_app": True,
    }
    built.update(overrides)
    return built


def payload(**overrides):
    built = {
        "exceptions": [
            {
                "type": "ValueError",
                "value": "bad input",
                "module": "checkout.errors",
                "frames": [frame(in_app=False, module="django.core", function="run")],
            }
        ]
    }
    built.update(overrides)
    return built


# nothing to show


def test_an_empty_payload_has_no_body():
    """Should let an Alertmanager occurrence fall back to the raw view."""
    result = event_view.build({})

    assert result is None


def test_a_payload_that_is_not_a_mapping_has_no_body():
    """Should refuse a value the store never should have held."""
    result = event_view.build("junk")

    assert result is None


def test_a_payload_of_only_unknown_keys_has_no_body():
    """Should not render an empty shell of headings."""
    result = event_view.build({"unknown": 1})

    assert result is None


# exception order


def test_the_raised_exception_is_rendered_before_its_cause():
    """Should read top-down like a traceback, newest first."""
    body = event_view.build(
        payload(
            exceptions=[
                {"type": "OSError", "frames": [frame()]},
                {"type": "ValueError", "frames": [frame()]},
            ]
        )
    )

    result = [(block.kind, block.caused_by) for block in body.exceptions]
    expected = [("ValueError", False), ("OSError", True)]

    assert result == expected


def test_a_mechanism_becomes_a_handled_label():
    """Should say plainly whether anything caught it."""
    body = event_view.build(
        payload(
            exceptions=[
                {
                    "type": "ValueError",
                    "mechanism": {"type": "django", "handled": False},
                    "frames": [frame()],
                }
            ]
        )
    )

    result = (body.exceptions[0].handled, body.exceptions[0].mechanism)
    expected = ("unhandled", "django")

    assert result == expected


def test_a_handled_exception_says_so():
    """Should distinguish caught from uncaught rather than leaving both blank."""
    body = event_view.build(
        payload(
            exceptions=[
                {"type": "E", "mechanism": {"handled": True}, "frames": [frame()]}
            ]
        )
    )

    result = body.exceptions[0].handled
    expected = "handled"

    assert result == expected


def test_an_exception_without_a_mechanism_has_no_label():
    """Should leave the chip off rather than guessing."""
    body = event_view.build(payload())

    result = (body.exceptions[0].handled, body.exceptions[0].mechanism)
    expected = ("", "")

    assert result == expected


def test_frames_omitted_reaches_the_view():
    """Should tell the reader the stack was cut rather than silently short."""
    body = event_view.build(
        payload(
            exceptions=[
                {"type": "E", "frames": [frame()], "frames_omitted": 12},
            ]
        )
    )

    result = body.exceptions[0].frames_omitted
    expected = 12

    assert result == expected


# frame order and expansion


def test_frames_are_rendered_innermost_first():
    """Should put the line that actually failed at the top."""
    body = event_view.build(
        payload(
            exceptions=[
                {
                    "type": "E",
                    "frames": [
                        frame(function="outer", in_app=False),
                        frame(function="inner"),
                    ],
                }
            ]
        )
    )

    result = [row.location for row in body.exceptions[0].frames]
    expected = ["checkout.gateway in inner", "checkout.gateway in outer"]

    assert result == expected


def test_the_first_application_frame_opens_by_default():
    """Should land the reader on their own code, not the framework's."""
    body = event_view.build(
        payload(
            exceptions=[
                {
                    "type": "E",
                    "frames": [
                        frame(function="app", in_app=True),
                        frame(function="library", in_app=False),
                    ],
                }
            ]
        )
    )

    result = [(row.location, row.expanded) for row in body.exceptions[0].frames]
    expected = [
        ("checkout.gateway in library", False),
        ("checkout.gateway in app", True),
    ]

    assert result == expected


def test_the_innermost_frame_opens_when_nothing_is_marked_in_app():
    """Should still open something for an SDK that never sets in_app."""
    body = event_view.build(
        payload(
            exceptions=[
                {
                    "type": "E",
                    "frames": [frame(in_app=None), frame(function="deep", in_app=None)],
                }
            ]
        )
    )

    result = [row.expanded for row in body.exceptions[0].frames]
    expected = [True, False]

    assert result == expected


def test_a_frame_without_a_module_falls_back_to_its_function():
    """Should name the frame with whatever the SDK actually sent."""
    body = event_view.build(
        payload(exceptions=[{"type": "E", "frames": [{"function": "handler"}]}])
    )

    result = body.exceptions[0].frames[0].location
    expected = "handler"

    assert result == expected


def test_a_frame_with_only_a_module_is_named_by_it():
    """Should not render an empty label."""
    body = event_view.build(
        payload(exceptions=[{"type": "E", "frames": [{"module": "vendor.pkg"}]}])
    )

    result = body.exceptions[0].frames[0].location
    expected = "vendor.pkg"

    assert result == expected


def test_a_frame_with_neither_falls_back_to_its_filename():
    """Should still be identifiable in a minified stack."""
    body = event_view.build(
        payload(exceptions=[{"type": "E", "frames": [{"filename": "bundle.js"}]}])
    )

    result = body.exceptions[0].frames[0].location
    expected = "bundle.js"

    assert result == expected


def test_a_frames_value_that_is_not_a_list_renders_no_frames():
    """Should tolerate a payload written by an older normaliser."""
    body = event_view.build(payload(exceptions=[{"type": "E", "frames": "junk"}]))

    result = body.exceptions[0].frames
    expected = ()

    assert result == expected


def test_a_non_integer_line_number_is_dropped():
    """Should not print a line number the renderer cannot trust."""
    body = event_view.build(
        payload(exceptions=[{"type": "E", "frames": [frame(lineno="141")]}])
    )

    result = body.exceptions[0].frames[0].lineno

    assert result is None


# source context


def test_source_context_is_numbered_around_the_failing_line():
    """Should let a reader match the snippet to the file on disk."""
    body = event_view.build(
        payload(
            exceptions=[
                {
                    "type": "E",
                    "frames": [
                        frame(
                            lineno=10,
                            pre_context=["a", "b"],
                            context_line="boom()",
                            post_context=["c"],
                        )
                    ],
                }
            ]
        )
    )

    result = [
        (line.number, line.text, line.current)
        for line in body.exceptions[0].frames[0].context
    ]
    expected = [
        (8, "a", False),
        (9, "b", False),
        (10, "boom()", True),
        (11, "c", False),
    ]

    assert result == expected


def test_source_context_without_a_line_number_renders_unnumbered():
    """Should still show the snippet an SDK sent without a lineno."""
    body = event_view.build(
        payload(
            exceptions=[
                {
                    "type": "E",
                    "frames": [
                        {"pre_context": ["a"], "context_line": "boom()"},
                    ],
                }
            ]
        )
    )

    result = [(line.number, line.text) for line in body.exceptions[0].frames[0].context]
    expected = [(None, "a"), (None, "boom()")]

    assert result == expected


def test_a_frame_with_no_snippet_has_no_context():
    """Should render the collapsed row without an empty code block."""
    body = event_view.build(payload())

    result = body.exceptions[0].frames[0].context
    expected = ()

    assert result == expected


def test_frame_variables_are_rendered_as_text():
    """Should show locals without asking the template to format JSON."""
    body = event_view.build(
        payload(
            exceptions=[
                {
                    "type": "E",
                    "frames": [frame(vars={"count": 3, "items": [1, 2], "name": "x"})],
                }
            ]
        )
    )

    result = dict(body.exceptions[0].frames[0].variables)
    expected = {"count": "3", "items": "[1, 2]", "name": "x"}

    assert result == expected


# threads


def test_a_crashed_thread_stands_in_for_a_missing_exception():
    """Should show frames from a crash that carried no exception interface."""
    body = event_view.build(
        {
            "threads": [
                {"id": "2", "name": "worker", "crashed": False, "frames": [frame()]},
                {"id": "1", "name": "main", "crashed": True, "frames": [frame()]},
            ]
        }
    )

    result = [block.kind for block in body.exceptions]
    expected = ["Thread main"]

    assert result == expected


def test_a_thread_without_a_name_is_titled_by_its_id():
    """Should still identify the thread on a platform that sends no names."""
    body = event_view.build(
        {"threads": [{"id": "9", "crashed": True, "frames": [frame()]}]}
    )

    result = body.exceptions[0].kind
    expected = "Thread 9"

    assert result == expected


def test_an_unidentified_thread_is_titled_plainly():
    """Should not render a dangling label."""
    body = event_view.build({"threads": [{"crashed": True, "frames": [frame()]}]})

    result = body.exceptions[0].kind
    expected = "Thread"

    assert result == expected


def test_a_thread_with_no_frames_is_skipped():
    """Should not raise an empty heading over nothing."""
    body = event_view.build(
        {"threads": [{"id": "1", "crashed": True}], "user": {"id": "7"}}
    )

    result = body.exceptions
    expected = ()

    assert result == expected


# breadcrumbs


def test_breadcrumbs_are_rendered_newest_first():
    """Should put what happened last at the top, next to the failure."""
    body = event_view.build(
        {
            "breadcrumbs": [
                {"message": "first", "timestamp": 1786000000},
                {"message": "second", "timestamp": 1786000060},
            ]
        }
    )

    result = [crumb.message for crumb in body.breadcrumbs]
    expected = ["second", "first"]

    assert result == expected


def test_a_numeric_breadcrumb_timestamp_becomes_a_clock_time():
    """Should be readable beside the event rather than an epoch number."""
    body = event_view.build({"breadcrumbs": [{"timestamp": 1786000000}]})

    result = len(body.breadcrumbs[0].stamp)
    expected = 8

    assert result == expected


def test_an_iso_breadcrumb_timestamp_keeps_its_time_part():
    """Should show the time without repeating the event's own date."""
    body = event_view.build({"breadcrumbs": [{"timestamp": "2026-08-04T10:11:12Z"}]})

    result = body.breadcrumbs[0].stamp
    expected = "10:11:12"

    assert result == expected


def test_a_breadcrumb_without_a_timestamp_renders_blank():
    """Should not invent a time the SDK never sent."""
    body = event_view.build({"breadcrumbs": [{"message": "clicked"}]})

    result = body.breadcrumbs[0].stamp
    expected = ""

    assert result == expected


def test_a_breadcrumb_falls_back_to_its_type_for_a_category():
    """Should label the row with whatever the SDK provided."""
    body = event_view.build({"breadcrumbs": [{"type": "http", "message": "GET /"}]})

    result = body.breadcrumbs[0].category
    expected = "http"

    assert result == expected


# context cards


def test_the_scalar_fields_become_one_event_card():
    """Should collect release and friends where a reader looks for them."""
    body = event_view.build(
        {"release": "checkout@1", "server_name": "web-1", "user": {"id": "7"}}
    )

    result = dict(body.cards[0].pairs)
    expected = {"Release": "checkout@1", "Server": "web-1"}

    assert (body.cards[0].title, result) == ("Event", expected)


def test_each_context_becomes_its_own_card():
    """Should keep runtime, os and browser apart instead of one flat blob."""
    body = event_view.build(
        {
            "contexts": {
                "runtime": {"name": "CPython"},
                "os": {"name": "Linux"},
                "empty": {},
            }
        }
    )

    result = [card.title for card in body.cards]
    expected = ["Runtime", "Os"]

    assert result == expected


def test_user_request_and_sdk_cards_come_before_the_contexts():
    """Should order the cards by how often they are read."""
    body = event_view.build(
        {
            "user": {"id": "7"},
            "request": {"url": "https://example.test/", "headers": {"Accept": "*/*"}},
            "sdk": {"name": "sentry.python"},
            "contexts": {"runtime": {"name": "CPython"}},
            "extra": {"basket": "b-1"},
        }
    )

    result = [card.title for card in body.cards]
    expected = ["User", "Request", "Headers", "Sdk", "Runtime", "Extra"]

    assert result == expected


def test_a_request_without_headers_gets_no_headers_card():
    """Should not raise a heading over an empty list."""
    body = event_view.build({"request": {"url": "https://example.test/"}})

    result = [card.title for card in body.cards]
    expected = ["Request"]

    assert result == expected


def test_a_request_that_is_not_an_object_yields_no_cards():
    """Should tolerate a payload written by an older normaliser."""
    body = event_view.build({"request": "junk", "user": {"id": "7"}})

    result = [card.title for card in body.cards]
    expected = ["User"]

    assert result == expected


def test_a_card_value_that_is_a_structure_is_rendered_as_json():
    """Should show a nested value rather than dropping it."""
    body = event_view.build({"user": {"geo": {"country_code": "SK"}}})

    result = dict(body.cards[0].pairs)
    expected = {"geo": '{"country_code": "SK"}'}

    assert result == expected


def test_an_empty_card_value_is_dropped():
    """Should not print a key with nothing after it."""
    body = event_view.build({"user": {"id": "7", "email": None, "username": ""}})

    result = dict(body.cards[0].pairs)
    expected = {"id": "7"}

    assert result == expected


def test_a_long_card_value_is_truncated():
    """Should keep one header from filling the panel."""
    body = event_view.build(
        {"request": {"headers": {"Cookie": "x" * (event_view.VALUE_MAX + 100)}}}
    )

    result = len(dict(body.cards[0].pairs)["Cookie"])
    expected = event_view.VALUE_MAX

    assert result == expected


def test_a_boolean_card_value_is_rendered_as_text():
    """Should print False rather than dropping it as empty."""
    body = event_view.build({"contexts": {"app": {"in_foreground": False}}})

    result = dict(body.cards[0].pairs)
    expected = {"in_foreground": "False"}

    assert result == expected


def test_an_exception_with_an_empty_frame_list_renders_no_frames():
    """Should render the heading and value even when the stack was stripped."""
    body = event_view.build(payload(exceptions=[{"type": "E", "frames": []}]))

    result = (body.exceptions[0].kind, body.exceptions[0].frames)
    expected = ("E", ())

    assert result == expected
