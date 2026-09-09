import pytest

from pandora.issues import priority


def derive(**overrides):
    fields = {"level": "error"}
    fields.update(overrides)
    return priority.derive(priority.Inputs(**fields))


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("fatal", priority.HIGH),
        ("error", priority.MEDIUM),
        ("warning", priority.LOW),
        ("info", priority.LOW),
        ("debug", priority.LOW),
    ],
)
def test_the_level_sets_the_floor(level, expected):
    """Should rank a quiet issue by the only thing known when it is created."""
    assert derive(level=level) == expected


def test_an_unknown_level_ranks_lowest():
    """Should not promote something whose level nobody recognises."""
    assert derive(level="whatever") == priority.LOW


def test_a_firing_alert_raises_the_rank():
    """Should put an issue whose alert is still open above one that cleared."""
    assert derive(level="error", open_episode_count=1) == priority.HIGH


def test_a_firing_alert_raises_a_warning_one_step_only():
    """Should not make every open warning as urgent as a fatal error."""
    assert derive(level="warning", open_episode_count=1) == priority.MEDIUM


def test_reaching_many_people_raises_the_rank(settings):
    """Should separate the fault everybody hit from the one person who did."""
    settings.PANDORA_PRIORITY_USER_THRESHOLD = 100

    assert derive(level="error", user_count=100) == priority.HIGH


def test_reaching_fewer_people_than_the_threshold_does_not(settings):
    """Should keep the bar where the operator set it."""
    settings.PANDORA_PRIORITY_USER_THRESHOLD = 100

    assert derive(level="error", user_count=99) == priority.MEDIUM


def test_a_threshold_of_zero_turns_the_rule_off(settings):
    """Should read zero as off, the way the other caps in the program do."""
    settings.PANDORA_PRIORITY_USER_THRESHOLD = 0

    assert derive(level="error", user_count=1_000_000) == priority.MEDIUM


def test_an_escalating_issue_is_always_high():
    """Should override the level for an issue that came back out of silence."""
    assert derive(level="debug", escalating=True) == priority.HIGH


def test_the_ranks_order_low_to_high():
    """Should be what the priority sort orders on, so it is pinned."""
    result = sorted(priority.ORDER, key=lambda name: priority.ORDER[name])
    expected = [priority.LOW, priority.MEDIUM, priority.HIGH]

    assert result == expected


def test_at_least_names_every_rank_from_the_wanted_one_up():
    """Should be what priority:>=medium filters on."""
    result = sorted(priority.at_least(priority.MEDIUM))
    expected = sorted([priority.MEDIUM, priority.HIGH])

    assert result == expected


def test_at_least_an_unknown_rank_matches_nothing():
    """Should reject a hand-edited query rather than widen it."""
    assert priority.at_least("urgent") == []
