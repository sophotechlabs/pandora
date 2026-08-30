import pytest
from django.utils import timezone

from pandora.artifacts import service
from pandora.events import payload as payload_interfaces
from pandora.ui import event_view
from tests.bundles import DEBUG_ID, build

pytestmark = pytest.mark.django_db

NOW = timezone.now()
MINIFIED = "app://basket.4c9e10.js"


def payload(**overrides):
    body = {
        "exceptions": [
            {
                "type": "TypeError",
                "value": "undefined is not a function",
                "frames": [
                    {
                        "abs_path": MINIFIED,
                        "filename": "basket.4c9e10.js",
                        "function": "n",
                        "lineno": 1,
                        "colno": 0,
                        "in_app": True,
                    }
                ],
            }
        ],
        "debug_images": [
            {"type": "sourcemap", "code_file": MINIFIED, "debug_id": DEBUG_ID}
        ],
    }
    body.update(overrides)
    return body


def frame_of(body):
    return body.exceptions[0].frames[0]


@pytest.fixture(autouse=True)
def cache():
    service.clear_cache()
    yield
    service.clear_cache()


@pytest.fixture
def bundle_bytes():
    return build


# with a map


def test_a_minified_frame_resolves_to_its_original_file(project, bundle_bytes):
    """Should be the payoff — `basket.4c9e10.js` becomes `src/payments.js`."""
    service.store_bundle(project, bundle_bytes(), NOW)

    frame = frame_of(event_view.build(payload(), project.pk))

    result = frame.filename
    expected = "src/payments.js"

    assert result == expected


def test_a_resolved_frame_carries_source_context(project, bundle_bytes):
    """Should show the original lines, which is what a JavaScript user never had."""
    service.store_bundle(project, bundle_bytes(), NOW)

    frame = frame_of(event_view.build(payload(), project.pk))

    result = "\n".join(line.text for line in frame.context)

    assert "throw new Error" in result


def test_a_resolved_frame_takes_the_original_name(project, bundle_bytes):
    """Should show `charge`, not the single letter the minifier chose."""
    service.store_bundle(project, bundle_bytes(), NOW)

    frame = frame_of(event_view.build(payload(), project.pk))

    assert "charge" in frame.location


def test_a_resolved_frame_is_opened(project, bundle_bytes):
    """Should be expanded, because it is now worth reading."""
    service.store_bundle(project, bundle_bytes(), NOW)

    result = frame_of(event_view.build(payload(), project.pk)).expanded

    assert result is True


# without one


def test_an_unresolved_frame_says_which_map_is_missing(project):
    """Should tell the user what to upload rather than show a blank panel."""
    frame = frame_of(event_view.build(payload(), project.pk))

    result = frame.minified
    expected = DEBUG_ID

    assert result == expected


def test_an_unresolved_frame_keeps_the_minified_path(project):
    """Should degrade to what the SDK sent, not to nothing."""
    frame = frame_of(event_view.build(payload(), project.pk))

    result = frame.filename
    expected = "basket.4c9e10.js"

    assert result == expected


def test_a_frame_with_no_debug_image_is_not_marked_minified(project):
    """Should not put the message on a Python frame."""
    body = payload(debug_images=[])

    result = frame_of(event_view.build(body, project.pk)).minified
    expected = ""

    assert result == expected


def test_a_debug_image_of_another_kind_is_ignored(project):
    """Should only read the sourcemap images, not every debug image."""
    body = payload(
        debug_images=[{"type": "macho", "code_file": MINIFIED, "debug_id": DEBUG_ID}]
    )

    result = frame_of(event_view.build(body, project.pk)).minified
    expected = ""

    assert result == expected


def test_a_malformed_debug_image_is_ignored(project):
    """Should not raise on an image that is not an object."""
    body = payload(debug_images=["nonsense"])

    result = frame_of(event_view.build(body, project.pk)).minified
    expected = ""

    assert result == expected


def test_debug_images_that_are_not_a_list_are_ignored(project):
    """Should survive a stored payload that carries the wrong shape."""
    body = payload(debug_images="nonsense")

    result = frame_of(event_view.build(body, project.pk)).minified
    expected = ""

    assert result == expected


def test_an_image_with_no_debug_id_is_ignored(project):
    """Should need both halves to be an address."""
    body = payload(debug_images=[{"type": "sourcemap", "code_file": MINIFIED}])

    result = frame_of(event_view.build(body, project.pk)).minified
    expected = ""

    assert result == expected


def test_a_frame_with_no_line_number_is_not_resolved(project, bundle_bytes):
    """Should not guess a position for a frame that has none."""
    service.store_bundle(project, bundle_bytes(), NOW)
    body = payload()
    body["exceptions"][0]["frames"][0].pop("lineno")

    result = frame_of(event_view.build(body, project.pk)).filename
    expected = "basket.4c9e10.js"

    assert result == expected


def test_a_frame_with_no_column_still_resolves(project, bundle_bytes):
    """Should assume column zero, which is what a minified line usually means."""
    service.store_bundle(project, bundle_bytes(), NOW)
    body = payload()
    body["exceptions"][0]["frames"][0].pop("colno")

    result = frame_of(event_view.build(body, project.pk)).filename
    expected = "src/payments.js"

    assert result == expected


def test_nothing_is_resolved_without_a_project(project, bundle_bytes):
    """Should leave the markdown export and other callers exactly as they were."""
    service.store_bundle(project, bundle_bytes(), NOW)

    result = frame_of(event_view.build(payload())).filename
    expected = "basket.4c9e10.js"

    assert result == expected


def test_a_position_outside_the_source_shows_no_context(project, bundle_bytes):
    """Should not index past the end of a file the map disagrees with."""
    service.store_bundle(
        project,
        bundle_bytes(
            document={
                "version": 3,
                "sources": ["src/payments.js"],
                "names": [],
                "mappings": "AAgBA",
                "sourcesContent": ["one line only"],
                "debug_id": DEBUG_ID,
            }
        ),
        NOW,
    )

    result = frame_of(event_view.build(payload(), project.pk)).context
    expected = ()

    assert result == expected


def test_a_map_with_no_sources_content_shows_no_context(project, bundle_bytes):
    """Should resolve the file name even when the bundler withheld the source."""
    document = {
        "version": 3,
        "sources": ["src/payments.js"],
        "names": [],
        "mappings": "AAAA",
        "debug_id": DEBUG_ID,
    }
    service.store_bundle(project, bundle_bytes(document=document), NOW)

    frame = frame_of(event_view.build(payload(), project.pk))

    result = (frame.filename, frame.context)
    expected = ("src/payments.js", ())

    assert result == expected


def test_the_stored_payload_is_the_shape_resolution_reads(project, bundle_bytes):
    """Should resolve what ingest actually stored, not the raw SDK shape.

    The reader took `debug_meta` while the writer stored `debug_images`, so no
    real event ever resolved. This is the contract between the two.
    """
    service.store_bundle(project, bundle_bytes(), NOW)
    raw = {
        "exception": {
            "values": [
                {
                    "type": "TypeError",
                    "value": "undefined is not a function",
                    "stacktrace": {
                        "frames": [
                            {
                                "abs_path": MINIFIED,
                                "filename": "basket.4c9e10.js",
                                "function": "n",
                                "lineno": 1,
                                "colno": 0,
                                "in_app": True,
                            }
                        ]
                    },
                }
            ]
        },
        "debug_meta": {
            "images": [
                {"type": "sourcemap", "code_file": MINIFIED, "debug_id": DEBUG_ID}
            ]
        },
    }

    stored = payload_interfaces.normalize(raw)

    result = frame_of(event_view.build(stored, project.pk)).filename
    expected = "src/payments.js"

    assert result == expected
