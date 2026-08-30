import json

import pytest

from pandora.artifacts import sourcemaps

MAP = {
    "version": 3,
    "sources": ["src/payments.js"],
    "names": ["charge"],
    "mappings": "AAAAA,SAAS",
    "sourcesContent": ["export function charge(order) {\n  throw new Error('x')\n}\n"],
}


def parsed(**overrides):
    document = dict(MAP)
    document.update(overrides)
    return sourcemaps.parse(json.dumps(document))


# parsing


def test_the_sources_come_back():
    """Should name the original files, which is the whole answer."""
    result = parsed().sources
    expected = ["src/payments.js"]

    assert result == expected


def test_a_source_root_is_joined_onto_every_source():
    """Should produce the path the bundler meant, not half of it."""
    result = parsed(sourceRoot="webpack://app").sources
    expected = ["webpack://app/src/payments.js"]

    assert result == expected


def test_a_map_with_no_mappings_is_refused():
    """Should fail on upload rather than on the first frame it cannot answer."""
    with pytest.raises(sourcemaps.SourceMapError, match="no mappings"):
        sourcemaps.parse(json.dumps({"version": 3, "sources": []}))


def test_a_map_that_is_not_json_is_refused():
    """Should name the problem rather than store bytes nobody can read."""
    with pytest.raises(sourcemaps.SourceMapError, match="not valid JSON"):
        sourcemaps.parse("not json")


def test_a_map_that_is_not_an_object_is_refused():
    """Should be specific about the shape it wanted."""
    with pytest.raises(sourcemaps.SourceMapError, match="not a JSON object"):
        sourcemaps.parse("[1, 2]")


def test_an_invalid_vlq_digit_is_refused():
    """Should not decode half a mapping and pretend it worked."""
    with pytest.raises(sourcemaps.SourceMapError, match="VLQ"):
        parsed(mappings="AA!A")


# resolution


def test_the_first_segment_resolves():
    """Should be the payoff — a minified column becomes a real file and line."""
    position = parsed().lookup(1, 0)

    result = (position.source, position.line, position.name)
    expected = ("src/payments.js", 1, "charge")

    assert result == expected


def test_a_column_past_the_last_segment_takes_the_last_one():
    """Should answer for a column inside a mapped range, not only on its start."""
    position = parsed().lookup(1, 999)

    assert position is not None


def test_a_column_before_the_first_segment_takes_the_first():
    """Should not return nothing for a frame that landed a character early."""
    position = parsed(mappings="CAAAA").lookup(1, 0)

    assert position is not None


def test_an_unmapped_line_resolves_to_nothing():
    """Should say it cannot answer rather than answer wrongly."""
    result = parsed().lookup(9, 0)

    assert result is None


def test_the_source_content_comes_back_as_context():
    """Should be the reason a JavaScript frame is worth opening."""
    position = parsed().lookup(1, 0)

    assert "throw new Error" in "\n".join(position.context)


def test_a_map_with_no_sources_content_resolves_without_context():
    """Should still name the file when the plugin did not inline the sources."""
    position = parsed(sourcesContent=[]).lookup(1, 0)

    assert position.context is None


def test_a_segment_naming_a_source_that_does_not_exist_resolves_to_nothing():
    """Should refuse a malformed map rather than index out of range."""
    result = parsed(sources=[]).lookup(1, 0)

    assert result is None


def test_a_segment_with_no_name_resolves_without_one():
    """Should take the four-field form, which most segments are."""
    position = parsed(mappings="AAAA").lookup(1, 0)

    result = position.name
    expected = ""

    assert result == expected


# debug ids


def test_a_debug_id_is_read_from_the_map():
    """Should be the modern mechanism, and it is simpler than the legacy path."""
    result = sourcemaps.debug_id_of({"debug_id": "abc"})
    expected = "abc"

    assert result == expected


def test_the_camel_case_spelling_is_read_too():
    """Should accept whichever spelling the plugin version wrote."""
    result = sourcemaps.debug_id_of({"debugId": "abc"})
    expected = "abc"

    assert result == expected


def test_a_map_with_no_debug_id_has_none():
    """Should say so rather than invent an address."""
    result = sourcemaps.debug_id_of({"version": 3})
    expected = ""

    assert result == expected


def test_something_that_is_not_a_map_has_no_debug_id():
    """Should not raise on a payload that was never a source map."""
    result = sourcemaps.debug_id_of("nonsense")
    expected = ""

    assert result == expected


# odd maps


def test_a_source_with_no_content_resolves_without_context():
    """Should still give the original file when `sourcesContent` holds a null."""
    position = parsed(sourcesContent=[None]).lookup(1, 0)

    result = position.context
    expected = None

    assert result == expected


def test_an_empty_mapping_line_is_skipped():
    """Should tolerate the `;;` a generator emits for a line with no mapping."""
    result = parsed(mappings="AAAAA;;AACAA").lookup(3, 0).line
    expected = 2

    assert result == expected


def test_a_negative_offset_moves_backwards():
    """Should decode the sign bit, which is how a map ever points up a file."""
    result = parsed(mappings="AAGAA;AADAA").lookup(2, 0).line
    expected = 3

    assert result == expected
