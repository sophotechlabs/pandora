"""Real `sentry-cli`, a real minified bundle, a real `@sentry/node` crash.

The upload protocol was written from Sentry's published specification. This is
the only test that puts the actual tool on the other end of it.
"""

import pytest

from live.support import body_of, issue_titled
from pandora.artifacts import models as artifact_models

pytestmark = pytest.mark.live


def test_sentry_cli_uploaded_a_bundle():
    """Should have accepted what the tool sent, with no flags to make it work."""
    result = artifact_models.ArtifactBundle.objects.count()

    assert result > 0


def test_the_bundle_holds_the_map():
    """Should have unpacked the archive rather than storing an opaque blob."""
    result = artifact_models.BundleFile.objects.filter(path__endswith=".map").count()

    assert result > 0


def test_the_minified_frame_resolves_to_the_original_file(signed_in, base_url):
    """Should turn `app.js` back into `src/app.js`, which is the whole feature."""
    body = body_of(signed_in, base_url, issue_titled("TypeError"))

    assert "src/app.js" in body


def test_the_original_function_name_is_shown(signed_in, base_url):
    """Should name `applyCoupon`, not the single letter the minifier chose."""
    body = body_of(signed_in, base_url, issue_titled("TypeError"))

    assert "applyCoupon" in body


def test_the_original_source_line_is_shown(signed_in, base_url):
    """Should show the code the developer wrote, out of `sourcesContent`."""
    body = body_of(signed_in, base_url, issue_titled("TypeError"))

    assert "coupon.factor" in body
