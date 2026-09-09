import pytest
from django.utils import timezone

from pandora.issues import models, similar

pytestmark = pytest.mark.django_db


@pytest.fixture
def make_issue(project):
    def build(digest, **overrides):
        fields = {
            "project": project,
            "fingerprint_hash": digest * 64,
            "fingerprint": ["pandora.checkout", "ValueError", "charge"],
            "title": "ValueError: pandora.checkout in charge",
            "culprit": "pandora.checkout in charge",
            "last_seen": timezone.now(),
        }
        fields.update(overrides)
        return models.Issue.objects.create(**fields)

    return build


def test_two_issues_from_the_same_code_path_are_similar(make_issue):
    """Should answer 'have we already decided about something like this'."""
    first = make_issue("a")
    make_issue(
        "b",
        fingerprint=["pandora.checkout", "TypeError", "charge"],
        title="TypeError: pandora.checkout in charge",
    )

    found = similar.similar(first)

    assert len(found) == 1


def test_an_unrelated_issue_is_not_similar(make_issue):
    """Should not fill the tab with everything in the project."""
    first = make_issue("a")
    make_issue(
        "b",
        fingerprint=["billing.invoices", "KeyError", "render"],
        title="KeyError: billing.invoices in render",
        culprit="billing.invoices in render",
    )

    assert similar.similar(first) == []


def test_the_issue_itself_is_never_its_own_match(make_issue):
    """Should not tell a reader the issue looks like itself."""
    first = make_issue("a")

    assert similar.similar(first) == []


def test_an_issue_in_another_project_is_not_a_match(make_issue, project):
    """Should keep one project's faults out of another's tab."""
    from pandora.core import models as core_models

    other = core_models.Project.objects.create(slug="other", name="Other")
    first = make_issue("a")
    models.Issue.objects.create(
        project=other,
        fingerprint_hash="b" * 64,
        fingerprint=["pandora.checkout", "ValueError", "charge"],
        title="ValueError: pandora.checkout in charge",
    )

    assert similar.similar(first) == []


def test_matches_are_ranked_by_how_much_they_share(make_issue):
    """Should put the nearest neighbour first, which is what a reader opens."""
    first = make_issue("a")
    make_issue(
        "b",
        fingerprint=["pandora.checkout", "TypeError", "charge"],
        title="TypeError: pandora.checkout in charge",
    )
    make_issue(
        "c",
        fingerprint=["pandora.checkout", "TypeError", "refund"],
        title="TypeError: pandora.checkout in refund",
        culprit="pandora.checkout in refund",
    )

    found = similar.similar(first)

    assert found[0].score >= found[1].score


def test_the_result_list_is_bounded(make_issue):
    """Should not render a hundred rows into a tab nobody scrolls."""
    first = make_issue("a")
    for index in range(20):
        make_issue(
            f"{index:x}" * 2,
            fingerprint_hash=f"{index:064d}",
            fingerprint=["pandora.checkout", "TypeError", "charge"],
        )

    assert len(similar.similar(first)) == similar.RESULT_LIMIT


def test_an_issue_with_no_signature_matches_nothing(make_issue):
    """Should not match everything when there is nothing to compare."""
    bare = make_issue("a", fingerprint=[], title="", culprit="")
    make_issue("b")

    assert similar.similar(bare) == []


def test_identical_signatures_score_one():
    """Should be the top of the scale, so the percentage reads honestly."""
    assert similar.score({"a", "b"}, {"a", "b"}) == 1.0


def test_disjoint_signatures_score_zero():
    """Should be the bottom of the scale."""
    assert similar.score({"a"}, {"b"}) == 0.0


def test_an_empty_signature_scores_zero():
    """Should not divide by nothing."""
    assert similar.score(set(), {"a"}) == 0.0


def test_common_words_do_not_create_a_match(make_issue):
    """Should not call two issues similar because both say 'error'."""
    result = similar.tokens(
        models.Issue(fingerprint=["error", "exception"], title="", culprit="")
    )

    assert result == frozenset()


def test_the_percentage_is_rounded_for_display():
    """Should render a whole number, which is all the tab needs."""
    match = similar.Match(issue=models.Issue(), score=0.666)

    assert match.percent == 67
