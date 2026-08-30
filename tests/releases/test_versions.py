import pytest

from pandora.releases import versions


@pytest.mark.parametrize(
    ("lower", "higher"),
    [
        ("1.2.3", "1.2.4"),
        ("1.2.3", "1.3.0"),
        ("1.9.0", "1.10.0"),
        ("1.2.3", "2.0.0"),
        ("v1.2.3", "v1.2.4"),
        ("1.2.3-rc1", "1.2.3"),
        ("1.2.3-rc1", "1.2.3-rc2"),
        ("1.2", "1.3"),
        ("2026.8.3", "2026.8.4"),
        ("2026.8.30", "2026.9.1"),
        ("2025.12.1", "2026.1.1"),
    ],
)
def test_a_later_version_sorts_after_an_earlier_one(lower, higher):
    """Should order the way a person reads them, which string compare does not."""
    assert versions.sort_key(lower) < versions.sort_key(higher)


def test_a_build_suffix_does_not_change_the_order():
    """Should ignore build metadata, as semver says to."""
    result = versions.sort_key("1.2.3+build7")
    expected = versions.sort_key("1.2.3")

    assert result == expected


def test_a_git_sha_sorts_below_every_parsed_version():
    """Should keep an unparseable version out of the way of the ordered ones."""
    assert versions.sort_key("9f2c1ab") < versions.sort_key("0.0.1")


def test_two_git_shas_sort_alphabetically():
    """Should be stable, so `dateCreated` is the only tie-break the caller needs."""
    assert versions.sort_key("aaa1111") < versions.sort_key("bbb2222")


@pytest.mark.parametrize(
    ("version", "parsed"),
    [
        ("1.2.3", True),
        ("v1.2.3-rc1+build7", True),
        ("2026.8.3", True),
        ("1.2", True),
        ("9f2c1ab", False),
        ("release-of-the-day", False),
        ("", False),
    ],
)
def test_whether_a_version_was_understood_is_recorded(version, parsed):
    """Should let the UI say when the ordering is alphabetical rather than real."""
    result = versions.is_parsed(version)

    assert result == parsed


def test_whitespace_is_ignored():
    """Should not sort a version differently because CI added a newline."""
    result = versions.sort_key(" 1.2.3 ")
    expected = versions.sort_key("1.2.3")

    assert result == expected


def test_a_very_long_version_is_cut_to_the_column():
    """Should store a nonsense version rather than raising on the write."""
    result = len(versions.sort_key("x" * 500))

    assert result <= versions.SORT_LENGTH


def test_a_huge_major_still_orders():
    """Should not overflow the padding on a version that counts builds."""
    assert versions.sort_key("9.0.0") < versions.sort_key("10.0.0")
