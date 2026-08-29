from pandora.events import payload


def frame(**overrides):
    built = {
        "filename": "checkout/gateway.py",
        "module": "checkout.gateway",
        "function": "charge",
        "lineno": 141,
        "in_app": True,
    }
    built.update(overrides)
    return built


def event(**overrides):
    built = {
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad input",
                    "module": "checkout.errors",
                    "stacktrace": {"frames": [frame()]},
                }
            ]
        }
    }
    built.update(overrides)
    return built


# shape


def test_a_payload_that_is_not_a_mapping_normalises_to_nothing():
    """Should refuse anything but an object rather than raising into the consumer."""
    result = payload.normalize(["not", "an", "object"])
    expected = {}

    assert result == expected


def test_an_event_without_interfaces_normalises_to_nothing():
    """Should keep the column empty so an Alertmanager row costs no extra bytes."""
    result = payload.normalize({"level": "error", "unknown_key": 1})
    expected = {}

    assert result == expected


def test_an_exception_keeps_the_fields_a_stack_trace_needs():
    """Should carry type, value, module and the frames through unchanged."""
    result = payload.normalize(event())["exceptions"]
    expected = [
        {
            "type": "ValueError",
            "value": "bad input",
            "module": "checkout.errors",
            "frames": [
                {
                    "filename": "checkout/gateway.py",
                    "module": "checkout.gateway",
                    "function": "charge",
                    "lineno": 141,
                    "in_app": True,
                }
            ],
        }
    ]

    assert result == expected


def test_a_chained_exception_keeps_every_link():
    """Should keep the whole chain — the cause is what explains the failure."""
    raw = event(
        exception={
            "values": [
                {"type": "OSError", "stacktrace": {"frames": [frame()]}},
                {"type": "ValueError", "stacktrace": {"frames": [frame()]}},
            ]
        }
    )

    result = [entry["type"] for entry in payload.normalize(raw)["exceptions"]]
    expected = ["OSError", "ValueError"]

    assert result == expected


def test_a_bare_exception_list_is_read_like_a_values_wrapper():
    """Should accept the older SDK shape where exception is the list itself."""
    raw = {"exception": [{"type": "KeyError"}]}

    result = [entry["type"] for entry in payload.normalize(raw)["exceptions"]]
    expected = ["KeyError"]

    assert result == expected


def test_a_mechanism_records_whether_the_exception_was_handled():
    """Should keep handled — unhandled is what makes an issue urgent."""
    raw = event(
        exception={
            "values": [
                {
                    "type": "ValueError",
                    "mechanism": {"type": "django", "handled": False},
                }
            ]
        }
    )

    result = payload.normalize(raw)["exceptions"][0]["mechanism"]
    expected = {"type": "django", "handled": False}

    assert result == expected


def test_a_mechanism_that_is_not_an_object_is_dropped():
    """Should ignore junk rather than storing it."""
    raw = event(exception={"values": [{"type": "ValueError", "mechanism": "junk"}]})

    result = "mechanism" in payload.normalize(raw)["exceptions"][0]

    assert result is False


# frames


def test_source_context_survives_normalisation():
    """Should keep the lines the renderer needs to show the failing statement."""
    raw = event(
        exception={
            "values": [
                {
                    "type": "ValueError",
                    "stacktrace": {
                        "frames": [
                            frame(
                                pre_context=["a = 1", "b = 2"],
                                context_line="raise ValueError(a)",
                                post_context=["return b"],
                            )
                        ]
                    },
                }
            ]
        }
    )

    result = payload.normalize(raw)["exceptions"][0]["frames"][0]
    expected = {
        "pre_context": ["a = 1", "b = 2"],
        "context_line": "raise ValueError(a)",
        "post_context": ["return b"],
    }

    assert {key: result[key] for key in expected} == expected


def test_frame_variables_are_capped_by_key_count():
    """Should bound a frame that carries a whole local namespace."""
    variables = {f"name{index}": index for index in range(payload.MAX_VARS + 20)}
    raw = event(
        exception={
            "values": [{"type": "E", "stacktrace": {"frames": [frame(vars=variables)]}}]
        }
    )

    result = len(payload.normalize(raw)["exceptions"][0]["frames"][0]["vars"])
    expected = payload.MAX_VARS

    assert result == expected


def test_a_deep_stack_keeps_the_innermost_frames_and_counts_the_rest():
    """Should drop the outermost frames, which are the least interesting."""
    frames = [frame(lineno=index) for index in range(payload.MAX_FRAMES + 5)]
    raw = event(exception={"values": [{"type": "E", "stacktrace": {"frames": frames}}]})

    normalised = payload.normalize(raw)["exceptions"][0]
    result = (
        len(normalised["frames"]),
        normalised["frames_omitted"],
        normalised["frames"][-1]["lineno"],
    )
    expected = (payload.MAX_FRAMES, 5, payload.MAX_FRAMES + 4)

    assert result == expected


def test_a_shallow_stack_records_no_omission():
    """Should leave frames_omitted absent when nothing was dropped."""
    result = "frames_omitted" in payload.normalize(event())["exceptions"][0]

    assert result is False


def test_a_stacktrace_that_is_not_an_object_leaves_no_frames():
    """Should tolerate an SDK sending the wrong shape."""
    raw = event(exception={"values": [{"type": "E", "stacktrace": "boom"}]})

    result = "frames" in payload.normalize(raw)["exceptions"][0]

    assert result is False


def test_frames_that_are_not_objects_are_skipped():
    """Should keep the good frames of a partly broken stacktrace."""
    raw = event(
        exception={
            "values": [{"type": "E", "stacktrace": {"frames": ["junk", frame()]}}]
        }
    )

    result = len(payload.normalize(raw)["exceptions"][0]["frames"])
    expected = 1

    assert result == expected


def test_a_frames_key_that_is_not_a_list_leaves_no_frames():
    """Should ignore a stacktrace whose frames are not a list."""
    raw = event(exception={"values": [{"type": "E", "stacktrace": {"frames": 7}}]})

    result = "frames" in payload.normalize(raw)["exceptions"][0]

    assert result is False


def test_context_lines_are_capped():
    """Should bound pre and post context so one frame cannot carry a whole file."""
    lines = [f"line {index}" for index in range(payload.MAX_CONTEXT_LINES + 10)]
    raw = event(
        exception={
            "values": [
                {"type": "E", "stacktrace": {"frames": [frame(pre_context=lines)]}}
            ]
        }
    )

    result = len(payload.normalize(raw)["exceptions"][0]["frames"][0]["pre_context"])
    expected = payload.MAX_CONTEXT_LINES

    assert result == expected


def test_a_frame_lineno_that_is_a_boolean_is_not_a_number():
    """Should refuse True as a line number — booleans are integers in Python."""
    raw = event(
        exception={
            "values": [{"type": "E", "stacktrace": {"frames": [frame(lineno=True)]}}]
        }
    )

    result = "lineno" in payload.normalize(raw)["exceptions"][0]["frames"][0]

    assert result is False


# threads


def test_threads_are_kept_when_there_is_no_exception():
    """Should keep a thread dump — a crash without an exception still has frames."""
    raw = {
        "threads": {
            "values": [
                {
                    "id": "7",
                    "name": "main",
                    "crashed": True,
                    "stacktrace": {"frames": [frame()]},
                }
            ]
        }
    }

    result = payload.normalize(raw)["threads"][0]
    expected = {"id": "7", "name": "main", "crashed": True}

    assert {key: result[key] for key in expected} == expected


def test_the_thread_list_is_capped():
    """Should bound a process that reports hundreds of threads."""
    values = [{"id": str(index)} for index in range(payload.MAX_THREADS + 10)]
    raw = {"threads": {"values": values}}

    result = len(payload.normalize(raw)["threads"])
    expected = payload.MAX_THREADS

    assert result == expected


# breadcrumbs


def test_breadcrumbs_keep_their_timeline_fields():
    """Should keep what the crumb timeline shows: when, what and how bad."""
    raw = {
        "breadcrumbs": {
            "values": [
                {
                    "type": "http",
                    "category": "fetch",
                    "level": "error",
                    "message": "GET /api/basket",
                    "timestamp": 1786000000.5,
                    "data": {"status_code": 500},
                }
            ]
        }
    }

    result = payload.normalize(raw)["breadcrumbs"][0]
    expected = {
        "type": "http",
        "category": "fetch",
        "level": "error",
        "message": "GET /api/basket",
        "timestamp": 1786000000.5,
        "data": {"status_code": 500},
    }

    assert result == expected


def test_a_string_breadcrumb_timestamp_is_kept_as_text():
    """Should accept the ISO form SDKs also send."""
    raw = {"breadcrumbs": {"values": [{"timestamp": "2026-08-04T10:00:00Z"}]}}

    result = payload.normalize(raw)["breadcrumbs"][0]["timestamp"]
    expected = "2026-08-04T10:00:00Z"

    assert result == expected


def test_breadcrumbs_keep_the_newest_when_capped():
    """Should drop the oldest crumbs — the last ones led to the failure."""
    values = [{"message": str(index)} for index in range(payload.MAX_BREADCRUMBS + 5)]
    raw = {"breadcrumbs": {"values": values}}

    crumbs = payload.normalize(raw)["breadcrumbs"]
    result = (len(crumbs), crumbs[-1]["message"])
    expected = (payload.MAX_BREADCRUMBS, str(payload.MAX_BREADCRUMBS + 4))

    assert result == expected


# context interfaces


def test_the_user_interface_is_kept():
    """Should keep who hit the error, which is how impact gets counted later."""
    raw = {"user": {"id": "1", "username": "renata", "ip_address": "203.0.113.4"}}

    result = payload.normalize(raw)["user"]
    expected = {"id": "1", "username": "renata", "ip_address": "203.0.113.4"}

    assert result == expected


def test_request_headers_arrive_as_pairs_or_as_a_mapping():
    """Should read both header encodings the protocol allows."""
    listed = {"request": {"headers": [["Accept", "text/html"], ["bad"]]}}
    mapped = {"request": {"headers": {"Accept": "text/html"}}}

    result = (
        payload.normalize(listed)["request"]["headers"],
        payload.normalize(mapped)["request"]["headers"],
    )
    expected = ({"Accept": "text/html"}, {"Accept": "text/html"})

    assert result == expected


def test_a_context_entry_that_is_not_an_object_is_dropped():
    """Should keep the contexts map clean for the renderer."""
    raw = {"contexts": {"runtime": {"name": "CPython"}, "broken": "text"}}

    result = payload.normalize(raw)["contexts"]
    expected = {"runtime": {"name": "CPython"}}

    assert result == expected


def test_the_sdk_and_modules_are_kept():
    """Should record what reported the event and what it was running."""
    raw = {
        "sdk": {"name": "sentry.python", "version": "2.24.1"},
        "modules": {"django": "5.2"},
    }

    result = payload.normalize(raw)
    expected = {
        "sdk": {"name": "sentry.python", "version": "2.24.1"},
        "modules": {"django": "5.2"},
    }

    assert result == expected


def test_a_logentry_keeps_its_template_and_its_formatted_line():
    """Should keep both — grouping reads the template, the reader wants the line."""
    raw = {
        "logentry": {
            "message": "fetch failed for source %s",
            "formatted": "fetch failed for source dou",
            "params": ["dou"],
        }
    }

    result = payload.normalize(raw)["logentry"]
    expected = {
        "message": "fetch failed for source %s",
        "formatted": "fetch failed for source dou",
        "params": ["dou"],
    }

    assert result == expected


def test_debug_images_come_out_of_debug_meta():
    """Should keep the image list a symbolicator would need."""
    raw = {"debug_meta": {"images": [{"type": "macho", "debug_id": "abc"}, "junk"]}}

    result = payload.normalize(raw)["debug_images"]
    expected = [{"type": "macho", "debug_id": "abc"}]

    assert result == expected


def test_debug_meta_without_images_yields_nothing():
    """Should leave the key absent rather than storing an empty list."""
    result = "debug_images" in payload.normalize({"debug_meta": {"sdk_info": {}}})

    assert result is False


def test_the_scalar_fields_of_an_event_are_kept():
    """Should carry release and friends — the release track reads them."""
    raw = {
        "platform": "python",
        "release": "checkout@2026.8.3",
        "dist": "7",
        "server_name": "web-1",
        "transaction": "POST /authorise",
        "environment": "production",
    }

    result = payload.normalize(raw)
    expected = raw

    assert result == expected


# trimming


def test_a_nested_value_deeper_than_the_limit_becomes_text():
    """Should stop recursing so a hostile payload cannot nest without bound."""
    nested = {"level": 1}
    for _ in range(payload.MAX_DEPTH + 3):
        nested = {"deeper": nested}

    result = payload.normalize({"extra": nested})["extra"]

    assert isinstance(result, dict)


def test_a_long_string_is_truncated():
    """Should bound one value rather than the whole row."""
    raw = {"extra": {"blob": "x" * (payload.MAX_STRING + 500)}}

    result = len(payload.normalize(raw)["extra"]["blob"])
    expected = payload.MAX_STRING

    assert result == expected


def test_a_list_is_capped_by_item_count():
    """Should bound an array the same way as a mapping."""
    raw = {"extra": {"items": list(range(payload.MAX_ITEMS + 20))}}

    result = len(payload.normalize(raw)["extra"]["items"])
    expected = payload.MAX_ITEMS

    assert result == expected


def test_an_extra_that_is_not_a_mapping_is_dropped():
    """Should ignore an SDK putting a scalar where an object belongs."""
    result = "extra" in payload.normalize({"extra": "text"})

    assert result is False


def test_numbers_and_booleans_survive_trimming_unchanged():
    """Should keep JSON scalars as they are so the API round trips."""
    raw = {"extra": {"count": 3, "ratio": 0.5, "on": True, "missing": None}}

    result = payload.normalize(raw)["extra"]
    expected = {"count": 3, "ratio": 0.5, "on": True, "missing": None}

    assert result == expected


def test_a_tuple_is_normalised_to_a_list():
    """Should hand JSON-encodable output to the store."""
    result = payload._trim(("a", "b"), 1)
    expected = ["a", "b"]

    assert result == expected


def test_an_object_that_is_not_json_becomes_its_text():
    """Should never hand the store something json.dumps would refuse."""
    result = payload._trim(object(), 1)

    assert isinstance(result, str)


# edges


def test_a_boolean_where_text_belongs_is_rendered_as_text():
    """Should never hand the store a type the column cannot hold."""
    result = payload.normalize({"platform": True})
    expected = {"platform": "True"}

    assert result == expected


def test_a_mechanism_keeps_its_help_link_and_meta():
    """Should keep what a platform SDK attaches to explain the signal."""
    raw = event(
        exception={
            "values": [
                {
                    "type": "SIGSEGV",
                    "mechanism": {
                        "type": "signalhandler",
                        "help_link": "https://example.test/sigsegv",
                        "meta": {"signal": {"number": 11}},
                    },
                }
            ]
        }
    )

    result = payload.normalize(raw)["exceptions"][0]["mechanism"]
    expected = {
        "type": "signalhandler",
        "help_link": "https://example.test/sigsegv",
        "meta": {"signal": {"number": 11}},
    }

    assert result == expected


def test_an_empty_context_entry_is_dropped():
    """Should not store a heading with nothing under it."""
    raw = {"contexts": {"runtime": {"name": "CPython"}, "app": {}}}

    result = payload.normalize(raw)["contexts"]
    expected = {"runtime": {"name": "CPython"}}

    assert result == expected


def test_an_empty_debug_image_is_dropped():
    """Should keep the image list to entries a symbolicator could use."""
    raw = {"debug_meta": {"images": [{}, {"type": "elf"}]}}

    result = payload.normalize(raw)["debug_images"]
    expected = [{"type": "elf"}]

    assert result == expected


def test_sdk_integrations_that_are_not_a_list_are_dropped():
    """Should tolerate an SDK sending the wrong shape for its own metadata."""
    raw = {"sdk": {"name": "sentry.python", "integrations": "django"}}

    result = payload.normalize(raw)["sdk"]
    expected = {"name": "sentry.python"}

    assert result == expected


def test_blank_sdk_integrations_are_dropped():
    """Should not store empty strings in a name list."""
    raw = {"sdk": {"name": "s", "integrations": ["django", "", "  "]}}

    result = payload.normalize(raw)["sdk"]["integrations"]
    expected = ["django"]

    assert result == expected


def test_a_list_nested_past_the_depth_limit_becomes_text():
    """Should stop recursing into arrays as well as objects."""
    nested = [1]
    for _ in range(payload.MAX_DEPTH + 3):
        nested = [nested]

    result = payload.normalize({"extra": {"deep": nested}})["extra"]["deep"]

    assert isinstance(result, list)


def test_source_lines_keep_their_indentation():
    """Should never strip a code line — the indentation is the code."""
    raw = event(
        exception={
            "values": [
                {
                    "type": "E",
                    "stacktrace": {
                        "frames": [
                            frame(
                                pre_context=["    body = build()"],
                                context_line="    raise ValueError(body)",
                            )
                        ]
                    },
                }
            ]
        }
    )

    stored = payload.normalize(raw)["exceptions"][0]["frames"][0]
    result = (stored["pre_context"][0], stored["context_line"])
    expected = ("    body = build()", "    raise ValueError(body)")

    assert result == expected


def test_a_context_line_that_is_not_text_is_dropped():
    """Should not render a number where a line of code belongs."""
    raw = event(
        exception={
            "values": [
                {"type": "E", "stacktrace": {"frames": [frame(context_line=42)]}}
            ]
        }
    )

    result = "context_line" in payload.normalize(raw)["exceptions"][0]["frames"][0]

    assert result is False
