import pytest

from live.support import body_of, issue_titled
from pandora.perf import models as perf_models
from pandora.releases import models as release_models

pytestmark = pytest.mark.live

TRANSACTION = "POST /checkout"
SOURCE_PREFIX = "https://github.com/sophotechlabs/live-shop/blob/"


def checkout_bucket():
    found = perf_models.TransactionBucket.objects.filter(
        project__slug="live", transaction=TRANSACTION
    )
    assert found.exists(), (
        f"no bucket for {TRANSACTION!r};"
        f" pandora holds {sorted(perf_models.TransactionBucket.objects.values_list('transaction', flat=True))}"
    )
    return found.first()


def live_commit():
    found = release_models.Commit.objects.filter(
        repository__project__slug="live", author_email="committer@live.test"
    )
    assert found.exists(), (
        "sentry-cli set-commits stored no commit;"
        f" pandora holds {sorted(release_models.Commit.objects.values_list('key', flat=True))}"
    )
    return found.first()


# performance, from the SDK's own transaction


def test_the_sdk_transaction_was_counted():
    """Should turn the transaction the real SDK sent into an endpoint bucket."""
    result = checkout_bucket().count

    assert result >= 1


def test_the_transaction_duration_covers_the_span():
    """Should measure the endpoint from the SDK's timestamps, not a default."""
    result = checkout_bucket().duration_max

    assert result >= 50


def test_the_span_time_is_summed_by_operation():
    """Should say where the endpoint's time went, by the span's op."""
    ops = list(checkout_bucket().spans.values_list("op", flat=True))

    assert "db.query" in ops


def test_the_performance_page_lists_the_endpoint(signed_in, base_url):
    """Should show the endpoint on the page the aggregation exists for."""
    signed_in.goto(f"{base_url}/performance/")

    assert TRANSACTION in signed_in.content()


# people affected, from the SDK's set_user


def test_the_person_the_sdk_named_is_counted_once():
    """Should count the user the SDK set, once, however many events they sent."""
    result = issue_titled("ZeroDivisionError").user_count

    assert result == 1


def test_the_user_tag_is_derived_from_the_sdk_user(signed_in, base_url):
    """Should write Sentry's canonical user tag from the SDK's user context."""
    issue = issue_titled("ZeroDivisionError")

    signed_in.goto(f"{base_url}/issues/{issue.pk}/tags/")

    assert "id:4211" in signed_in.content()


# suspect commits, from sentry-cli releases set-commits --local


def test_sentry_cli_names_the_repository_by_its_git_remote():
    """Should file the commits under the remote name `--local` sends, not owner/name."""
    result = live_commit().repository.name

    assert result == "origin"


def test_sentry_cli_sent_the_files_the_commit_touched():
    """Should store the changed paths, which is what blame is computed from."""
    result = list(live_commit().files.values_list("path", flat=True))

    assert result == ["live/python_app.py"]


def test_the_commit_belongs_to_the_release():
    """Should attach the commit to the release sentry-cli named."""
    result = list(
        live_commit().release_commits.values_list("release__version", flat=True)
    )

    assert result == ["1.4.2"]


def test_the_issue_page_names_the_suspect_commit(signed_in, base_url):
    """Should blame the commit that last touched the file the SDK reported."""
    body = body_of(signed_in, base_url, issue_titled("ZeroDivisionError"))

    assert "Suspect commits" in body
    assert "Live Committer" in body


def test_the_frame_links_to_its_source(signed_in, base_url):
    """Should turn the SDK's frame into a link through the code mapping."""
    body = body_of(signed_in, base_url, issue_titled("ZeroDivisionError"))

    assert SOURCE_PREFIX in body
    assert "live/python_app.py#L" in body
