import http

import pytest
from django import test

from pandora.ingest import gate

# configuration


@test.override_settings(PANDORA_INGEST_MAX_BYTES=4096)
def test_gate_takes_its_cap_from_the_setting():
    """Should read the body-size cap from PANDORA_INGEST_MAX_BYTES."""
    result = gate.PassThroughGate().max_bytes
    expected = 4096

    assert result == expected


def test_gate_cap_can_be_overridden_per_instance():
    """Should let a caller pin the cap without touching settings."""
    result = gate.PassThroughGate(max_bytes=99).max_bytes
    expected = 99

    assert result == expected


@test.override_settings(PANDORA_GATE="pandora.ingest.gate.PassThroughGate")
def test_gate_factory_builds_the_configured_gate():
    """Should build the gate named by PANDORA_GATE."""
    result = gate.get_gate()

    assert isinstance(result, gate.PassThroughGate)


# check tests


def test_gate_allows_a_body_under_the_cap(token):
    """Should pass a request whose body fits the cap."""
    result = gate.PassThroughGate(max_bytes=1024).check(token, 512)
    expected = gate.Verdict(allowed=True, status=http.HTTPStatus.OK, reason="")

    assert result == expected


def test_gate_allows_a_body_exactly_at_the_cap(token):
    """Should treat the cap as inclusive."""
    result = gate.PassThroughGate(max_bytes=1024).check(token, 1024)

    assert result.allowed is True


def test_gate_rejects_an_oversized_body_with_413(token):
    """Should reject an oversized body before anything durable is written."""
    result = gate.PassThroughGate(max_bytes=1024).check(token, 1025)
    expected = gate.Verdict(
        allowed=False,
        status=http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        reason="oversized",
    )

    assert result == expected


# counter tests


def test_gate_counts_every_check(token):
    """Should count each check so the pass-through gate is still observable."""
    checked = gate.PassThroughGate(max_bytes=1024)
    before = gate.GATE_CHECKS._value.get()

    checked.check(token, 1)
    checked.check(token, 2)

    result = gate.GATE_CHECKS._value.get() - before
    expected = 2
    assert result == expected


def test_gate_counts_rejections_by_reason(token):
    """Should count a rejection under its reason label."""
    before = gate.GATE_REJECTIONS.labels(reason="oversized")._value.get()

    gate.PassThroughGate(max_bytes=1).check(token, 2)

    result = gate.GATE_REJECTIONS.labels(reason="oversized")._value.get() - before
    expected = 1
    assert result == expected


@pytest.mark.parametrize("content_length", [0, 1, 1024])
def test_gate_never_rejects_inside_the_cap(token, content_length):
    """Should pass every body size from empty up to the cap."""
    result = gate.PassThroughGate(max_bytes=1024).check(token, content_length)

    assert result.allowed is True
