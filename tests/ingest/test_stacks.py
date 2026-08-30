from pandora.ingest.translators import stacks

PYTHON = """Traceback (most recent call last):
  File "/app/src/payments/charge.py", line 42, in charge
    gateway.send(order)
  File "/app/src/http/client.py", line 7, in send
    raise GatewayError("declined")
payments.errors.GatewayError: declined"""

JAVA = """java.lang.IllegalStateException: pool exhausted
\tat com.example.Pool.acquire(Pool.java:88)
\tat com.example.Service.handle(Service.java:31)
\tat java.base/java.lang.Thread.run(Thread.java:840)"""

GO = """panic: runtime error: index out of range [3] with length 2

goroutine 1 [running]:
main.charge(0xc000010000)
\t/app/main.go:42 +0x1a5
main.main()
\t/app/main.go:17 +0x28"""

NODE = """TypeError: Cannot read properties of undefined
    at charge (/app/src/payments.js:42:19)
    at /app/src/server.js:7:3"""


# python


def test_a_python_traceback_is_recognised():
    """Should be the commonest trace in a log line anywhere."""
    result = stacks.parse(PYTHON).language
    expected = "python"

    assert result == expected


def test_a_python_traceback_yields_its_frames():
    """Should produce the frame list the issue page already renders."""
    result = len(stacks.parse(PYTHON).frames)
    expected = 2

    assert result == expected


def test_a_python_frame_carries_file_line_and_function():
    """Should be enough to group on and enough to read."""
    frame = stacks.parse(PYTHON).frames[0]

    result = (frame["filename"], frame["lineno"], frame["function"])
    expected = ("/app/src/payments/charge.py", 42, "charge")

    assert result == expected


def test_a_python_frame_keeps_the_source_line():
    """Should show the line, which is what makes a trace worth opening."""
    result = stacks.parse(PYTHON).frames[0]["context_line"]
    expected = "gateway.send(order)"

    assert result == expected


def test_the_python_exception_type_is_taken_from_the_last_line():
    """Should be the class, which is half the fingerprint."""
    parsed = stacks.parse(PYTHON)

    result = (parsed.kind, parsed.value, parsed.module)
    expected = ("GatewayError", "declined", "payments.errors")

    assert result == expected


# java


def test_a_java_stack_is_recognised():
    """Should take the shape every JVM prints."""
    result = stacks.parse(JAVA).language
    expected = "java"

    assert result == expected


def test_a_java_stack_yields_its_frames():
    """Should keep the whole chain, not only the top."""
    result = len(stacks.parse(JAVA).frames)
    expected = 3

    assert result == expected


def test_a_java_frame_splits_the_source_and_the_line():
    """Should turn `Pool.java:88` into two fields the UI can use."""
    frame = stacks.parse(JAVA).frames[0]

    result = (frame["filename"], frame["lineno"], frame["module"])
    expected = ("Pool.java", 88, "com.example.Pool")

    assert result == expected


def test_the_java_exception_type_loses_its_package():
    """Should group on the class, with the package kept beside it."""
    parsed = stacks.parse(JAVA)

    result = (parsed.kind, parsed.module, parsed.value)
    expected = ("IllegalStateException", "java.lang", "pool exhausted")

    assert result == expected


def test_a_java_stack_from_a_named_thread_is_recognised():
    """Should take the form the JVM prints for an uncaught exception."""
    text = (
        'Exception in thread "main" java.lang.NullPointerException\n\tat A.b(A.java:1)'
    )

    result = stacks.parse(text).kind
    expected = "NullPointerException"

    assert result == expected


# go


def test_a_go_panic_is_recognised():
    """Should take the shape a Go binary prints on its way down."""
    result = stacks.parse(GO).language
    expected = "go"

    assert result == expected


def test_a_go_panic_carries_its_message():
    """Should be the value, which is what a human reads first."""
    result = stacks.parse(GO).value

    assert "index out of range" in result


def test_a_go_frame_pairs_the_function_with_its_file():
    """Should join the two lines Go prints into one frame."""
    frame = stacks.parse(GO).frames[0]

    result = (frame["function"], frame["filename"], frame["lineno"])
    expected = ("main.charge(0xc000010000)", "/app/main.go", 42)

    assert result == expected


# node


def test_a_node_stack_is_recognised():
    """Should take the V8 shape, which browsers and node both print."""
    result = stacks.parse(NODE).language
    expected = "javascript"

    assert result == expected


def test_a_node_frame_carries_the_column_too():
    """Should keep the column, which source maps need."""
    frame = stacks.parse(NODE).frames[0]

    result = (frame["filename"], frame["lineno"], frame["colno"], frame["function"])
    expected = ("/app/src/payments.js", 42, 19, "charge")

    assert result == expected


def test_a_node_frame_without_a_function_still_parses():
    """Should take a top-level frame, which has no name."""
    result = stacks.parse(NODE).frames[1]["filename"]
    expected = "/app/src/server.js"

    assert result == expected


def test_the_node_exception_type_is_taken_from_the_first_line():
    """Should be the class, as it is everywhere else."""
    parsed = stacks.parse(NODE)

    result = (parsed.kind, parsed.value)
    expected = ("TypeError", "Cannot read properties of undefined")

    assert result == expected


# what is not a trace


def test_a_plain_log_line_yields_nothing():
    """Should say it found nothing rather than invent a frame."""
    result = stacks.parse("connection refused")

    assert result.found is False


def test_an_empty_string_yields_nothing():
    """Should not raise on the line every log has."""
    result = stacks.parse("")

    assert result.found is False


def test_the_frame_list_is_bounded():
    """Should not let a runaway recursion produce ten thousand frames."""
    frames = "\n".join(
        f'  File "/app/a.py", line {index}, in f' for index in range(500)
    )
    text = f"Traceback (most recent call last):\n{frames}\nValueError: deep"

    result = len(stacks.parse(text).frames)
    expected = stacks.MAX_FRAMES

    assert result == expected


# the awkward shapes


def test_a_python_traceback_with_no_recognisable_exception_still_parses():
    """Should keep the frames even when the last line is not a class name."""
    text = 'Traceback (most recent call last):\n  File "/a.py", line 1, in f\nsomething odd'

    result = stacks.parse(text)

    assert result.frames and result.kind == ""


def test_a_python_frame_at_the_end_has_no_context_line():
    """Should not read past the end of the trace."""
    text = 'Traceback (most recent call last):\n  File "/a.py", line 1, in f'

    result = stacks.parse(text).frames[0]

    assert "context_line" not in result


def test_two_python_frames_in_a_row_have_no_context():
    """Should not mistake the next frame for the source line of this one."""
    text = (
        "Traceback (most recent call last):\n"
        '  File "/a.py", line 1, in f\n'
        '  File "/b.py", line 2, in g\n'
        "ValueError: x"
    )

    result = stacks.parse(text).frames[0]

    assert "context_line" not in result


def test_a_python_exception_with_no_message_parses():
    """Should take a bare class name, which is what a raise with no args prints."""
    text = (
        'Traceback (most recent call last):\n  File "/a.py", line 1, in f\nValueError'
    )

    parsed = stacks.parse(text)

    assert parsed.kind == "ValueError" and parsed.value == ""


def test_a_java_frame_with_no_line_number_parses():
    """Should take `Native Method`, which has no line to give."""
    text = "java.lang.NullPointerException\n\tat A.b(Native Method)"

    result = stacks.parse(text).frames[0]

    assert result["filename"] == "Native Method" and "lineno" not in result


def test_a_java_frame_with_an_unnumbered_source_parses():
    """Should not choke on `Unknown Source`."""
    text = "java.lang.NullPointerException\n\tat A.b(Unknown Source)"

    result = stacks.parse(text).frames[0]

    assert result["filename"] == "Unknown Source"


def test_a_java_frame_with_an_empty_source_parses():
    """Should take a frame the JVM printed with nothing between the brackets."""
    text = "java.lang.NullPointerException\n\tat A.b()"

    result = stacks.parse(text).frames[0]

    assert "filename" not in result


def test_a_java_stack_with_no_header_still_yields_frames():
    """Should take a trace pasted without its first line."""
    text = "\tat com.example.A.b(A.java:1)"

    parsed = stacks.parse(text)

    assert parsed.frames and parsed.kind == ""


def test_a_go_panic_with_no_frames_still_parses():
    """Should keep the message even when the goroutine dump was truncated."""
    result = stacks.parse("panic: boom")

    assert result.kind == "panic" and result.frames == []


def test_a_go_frame_with_no_location_line_parses():
    """Should take the function on its own when the file line is missing."""
    text = "panic: boom\n\nmain.charge()\n"

    result = stacks.parse(text).frames[0]

    assert result["function"] == "main.charge()" and "filename" not in result


def test_a_node_stack_with_no_header_still_yields_frames():
    """Should take a trace whose first line was consumed by a log prefix."""
    text = "    at charge (/app/a.js:1:2)"

    parsed = stacks.parse(text)

    assert parsed.frames and parsed.kind == ""


def test_a_java_frame_whose_source_has_no_colon_is_taken():
    """Should keep whatever the JVM printed as the source."""
    text = "java.lang.Error: x\n\tat A.b(Foo)"

    result = stacks.parse(text).frames[0]["filename"]
    expected = "Foo"

    assert result == expected


def test_a_java_frame_with_a_bare_method_has_no_module():
    """Should not invent a package for a frame that has none."""
    text = "java.lang.Error: x\n\tat run(A.java:1)"

    result = stacks.parse(text).frames[0]

    assert "module" not in result


def test_a_java_frame_with_a_non_numeric_line_is_taken_without_one():
    """Should keep the file when the line is not a number."""
    text = "java.lang.Error: x\n\tat A.b(A.java:main)"

    result = stacks.parse(text).frames[0]

    assert result["filename"] == "A.java" and "lineno" not in result


def test_a_go_dump_with_a_trailing_function_is_taken():
    """Should take the last frame even when nothing follows it."""
    text = "panic: boom\n\nmain.main()"

    result = stacks.parse(text).frames

    assert len(result) == 1
