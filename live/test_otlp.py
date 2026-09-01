"""The OpenTelemetry collector's own `otlphttp` exporter, JSON encoding."""

import pytest

from live.support import issue_titled
from pandora.issues import models as issue_models

pytestmark = pytest.mark.live


def test_an_otlp_log_became_an_issue():
    """Should take what the collector sends without a pandora-specific exporter."""
    issue = issue_titled("EvictionStorm")

    assert issue is not None


def test_the_resource_attribute_became_a_tag():
    """Should keep `service.name`, the attribute every OTel pipeline sets."""
    issue = issue_titled("EvictionStorm")

    result = set(
        issue_models.TagStat.objects.filter(issue=issue).values_list("key", flat=True)
    )

    assert any(key.startswith("service") for key in result)
