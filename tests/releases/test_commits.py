import datetime

import pytest

from pandora.releases import commits, service
from pandora.releases import models as release_models

pytestmark = pytest.mark.django_db

NOW = datetime.datetime(2026, 9, 9, 12, 0, tzinfo=datetime.UTC)


@pytest.fixture
def release(project):
    return service.ensure_release(project, "1.4.0", "", NOW)


def commit_payload(**overrides):
    entry = {
        "id": "a" * 40,
        "repository": "sophotechlabs/pandora",
        "message": "fix the checkout total",
        "author_name": "Someone",
        "author_email": "someone@example.test",
        "timestamp": "2026-09-08T09:00:00Z",
        "patch_set": [{"path": "src/pandora/checkout.py", "type": "M"}],
    }
    entry.update(overrides)
    return entry


# storing what CI sent


def test_a_commit_is_stored_with_its_author(project, release):
    """Should keep who wrote it, which is the whole point of attribution."""
    commits.set_commits(project, release, [commit_payload()])

    stored = release_models.Commit.objects.get()
    result = (stored.key, stored.author_email, stored.summary)
    expected = ("a" * 40, "someone@example.test", "fix the checkout total")

    assert result == expected


def test_the_repository_is_created_from_the_name_ci_sent(project, release):
    """Should not need the repository registered before the first push."""
    commits.set_commits(project, release, [commit_payload()])

    repository = release_models.Repository.objects.get()
    result = (repository.name, repository.provider, repository.url)
    expected = (
        "sophotechlabs/pandora",
        release_models.RepositoryProvider.GITHUB,
        "https://github.com/sophotechlabs/pandora",
    )

    assert result == expected


def test_the_changed_files_are_stored(project, release):
    """Should be what makes a stack-trace path answerable at all."""
    commits.set_commits(project, release, [commit_payload()])

    stored = release_models.CommitFile.objects.get()
    result = (stored.path, stored.change)
    expected = ("src/pandora/checkout.py", "M")

    assert result == expected


def test_the_commit_joins_the_release(project, release):
    """Should be the association the suspect query walks."""
    commits.set_commits(project, release, [commit_payload()])

    assert release_models.ReleaseCommit.objects.filter(release=release).count() == 1


def test_pushing_the_same_commit_twice_stores_one(project, release):
    """Should let CI retry without doubling the history."""
    commits.set_commits(project, release, [commit_payload()])
    commits.set_commits(project, release, [commit_payload()])

    assert release_models.Commit.objects.count() == 1


def test_a_second_push_replaces_the_file_list(project, release):
    """Should not leave a file behind that the corrected payload dropped."""
    commits.set_commits(project, release, [commit_payload()])
    commits.set_commits(
        project,
        release,
        [commit_payload(patch_set=[{"path": "src/pandora/ledger.py", "type": "A"}])],
    )

    result = sorted(row.path for row in release_models.CommitFile.objects.all())
    expected = ["src/pandora/ledger.py"]

    assert result == expected


def test_a_commit_with_no_id_is_refused(project, release):
    """Should not store a commit nothing can be matched against."""
    with pytest.raises(commits.CommitError):
        commits.set_commits(project, release, [commit_payload(id="")])


def test_a_commit_with_no_repository_is_refused(project, release):
    """Should not guess which repository a path belongs to."""
    with pytest.raises(commits.CommitError):
        commits.set_commits(project, release, [commit_payload(repository="")])


def test_a_commit_that_is_not_an_object_is_refused(project, release):
    """Should refuse a payload shaped wrongly rather than skip it silently."""
    with pytest.raises(commits.CommitError):
        commits.set_commits(project, release, ["a" * 40])


def test_too_many_commits_are_refused(project, release):
    """Should bound one request rather than let a repo history land in a POST."""
    payload = [commit_payload(id=f"{index:040d}") for index in range(1001)]

    with pytest.raises(commits.CommitError):
        commits.set_commits(project, release, payload)


def test_too_many_files_are_refused(project, release):
    """Should bound the per-commit file list the same way."""
    files = [{"path": f"src/file{index}.py", "type": "M"} for index in range(501)]

    with pytest.raises(commits.CommitError):
        commits.set_commits(project, release, [commit_payload(patch_set=files)])


def test_a_bad_timestamp_is_refused(project, release):
    """Should not silently store a commit dated now when CI sent nonsense."""
    with pytest.raises(commits.CommitError):
        commits.set_commits(project, release, [commit_payload(timestamp="yesterday")])


def test_a_missing_timestamp_falls_back_to_now(project, release):
    """Should accept the shape sentry-cli sends when git gave it no date."""
    commits.set_commits(project, release, [commit_payload(timestamp=None)])

    assert release_models.Commit.objects.get().committed_at is not None


def test_an_unknown_change_type_is_read_as_a_modification(project, release):
    """Should not reject a whole push over one unexpected letter."""
    commits.set_commits(
        project,
        release,
        [commit_payload(patch_set=[{"path": "src/a.py", "type": "R"}])],
    )

    assert release_models.CommitFile.objects.get().change == "M"


def test_a_gitlab_repository_is_recognised_by_name(project, release):
    """Should not guess GitHub for a repository the name says is elsewhere."""
    commits.set_commits(
        project, release, [commit_payload(repository="gitlab.test/team/app")]
    )

    repository = release_models.Repository.objects.get()
    assert repository.provider == release_models.RepositoryProvider.GITLAB


# mapping a stack path into a repository


@pytest.fixture
def mapping(project):
    repository = release_models.Repository.objects.create(
        project=project,
        name="sophotechlabs/pandora",
        provider=release_models.RepositoryProvider.GITHUB,
        url="https://github.com/sophotechlabs/pandora",
    )
    return release_models.CodeMapping.objects.create(
        project=project,
        repository=repository,
        stack_root="/app/",
        source_root="src/",
        default_branch="main",
    )


def test_a_frame_path_becomes_a_repository_path(project, mapping):
    """Should strip the container prefix and add the one inside the repo."""
    found = commits.map_path("/app/pandora/checkout.py", [mapping])

    assert found is not None
    assert found[1] == "src/pandora/checkout.py"


def test_a_frame_outside_the_stack_root_does_not_map(project, mapping):
    """Should leave a vendored path alone rather than mis-attribute it."""
    assert commits.map_path("/usr/lib/python3/json.py", [mapping]) is None


def test_the_longest_stack_root_wins(project, mapping):
    """Should let a specific mapping sit under a catch-all one."""
    other = release_models.CodeMapping.objects.create(
        project=project,
        repository=mapping.repository,
        stack_root="/app/pandora/ui/",
        source_root="src/pandora/ui/",
    )

    found = commits.map_path("/app/pandora/ui/views.py", [mapping, other])

    assert found is not None
    assert found[0].pk == other.pk


def test_an_empty_path_does_not_map(project, mapping):
    """Should not turn a frame with no file into the repository root."""
    assert commits.map_path("   ", [mapping]) is None


def test_a_mapping_with_no_source_root_keeps_the_tail(project):
    """Should support a repository whose code sits at the top level."""
    repository = release_models.Repository.objects.create(
        project=project, name="team/app"
    )
    mapping = release_models.CodeMapping.objects.create(
        project=project, repository=repository, stack_root="/srv/", source_root=""
    )

    found = commits.map_path("/srv/app/main.go", [mapping])

    assert found is not None
    assert found[1] == "app/main.go"


# linking a frame to its source


def test_a_github_frame_links_to_the_line(project, mapping):
    """Should be a plain URL — no forge API call, no token, no rate limit."""
    result = commits.source_url(mapping, "src/pandora/checkout.py", 42)
    expected = (
        "https://github.com/sophotechlabs/pandora/blob/main/src/pandora/checkout.py#L42"
    )

    assert result == expected


def test_a_gitlab_frame_uses_the_gitlab_path(project):
    """Should use each forge's own blob path, which differ."""
    repository = release_models.Repository.objects.create(
        project=project,
        name="team/app",
        provider=release_models.RepositoryProvider.GITLAB,
        url="https://gitlab.test/team/app",
    )
    mapping = release_models.CodeMapping.objects.create(
        project=project, repository=repository
    )

    result = commits.source_url(mapping, "app/main.py", 7)
    expected = "https://gitlab.test/team/app/-/blob/main/app/main.py#L7"

    assert result == expected


def test_a_bitbucket_frame_uses_its_own_line_anchor(project):
    """Should not produce a link that lands on the wrong line."""
    repository = release_models.Repository.objects.create(
        project=project,
        name="team/app",
        provider=release_models.RepositoryProvider.BITBUCKET,
        url="https://bitbucket.test/team/app",
    )
    mapping = release_models.CodeMapping.objects.create(
        project=project, repository=repository
    )

    result = commits.source_url(mapping, "app/main.py", 7)
    expected = "https://bitbucket.test/team/app/src/main/app/main.py#lines-7"

    assert result == expected


def test_a_repository_with_no_url_produces_no_link(project):
    """Should render nothing rather than a broken href."""
    repository = release_models.Repository.objects.create(
        project=project, name="internal", provider=release_models.RepositoryProvider.GIT
    )
    mapping = release_models.CodeMapping.objects.create(
        project=project, repository=repository
    )

    assert commits.source_url(mapping, "app/main.py", 7) == ""


def test_a_frame_with_no_line_links_to_the_file(project, mapping):
    """Should still be useful when the SDK sent no line number."""
    result = commits.source_url(mapping, "src/pandora/checkout.py")
    expected = (
        "https://github.com/sophotechlabs/pandora/blob/main/src/pandora/checkout.py"
    )

    assert result == expected


def test_a_branch_or_commit_can_be_named(project, mapping):
    """Should link the code that was running, not whatever main holds now."""
    result = commits.source_url(mapping, "src/a.py", 1, ref="a" * 40)

    assert f"/blob/{'a' * 40}/src/a.py#L1" in result


# suspects


def payload_with(path):
    return {
        "exceptions": [
            {
                "type": "ValueError",
                "frames": [
                    {"filename": "/usr/lib/django/core.py", "in_app": False},
                    {"filename": path, "in_app": True, "lineno": 12},
                ],
            }
        ]
    }


def test_the_commit_that_touched_the_frame_is_the_suspect(project, release, mapping):
    """Should answer 'what changed here' from data CI already pushed."""
    commits.set_commits(project, release, [commit_payload()])

    found = commits.suspects(
        project,
        commits.frames_from(payload_with("/app/pandora/checkout.py")),
        NOW,
    )

    assert [suspect.commit.key for suspect in found] == ["a" * 40]


def test_a_frame_no_commit_touched_has_no_suspect(project, release, mapping):
    """Should stay silent rather than blame the nearest commit."""
    commits.set_commits(project, release, [commit_payload()])

    found = commits.suspects(
        project,
        commits.frames_from(payload_with("/app/pandora/ledger.py")),
        NOW,
    )

    assert found == []


def test_a_commit_after_the_issue_appeared_is_not_a_suspect(project, release, mapping):
    """Should not blame the fix for the fault it fixed."""
    commits.set_commits(
        project, release, [commit_payload(timestamp="2026-09-10T09:00:00Z")]
    )

    found = commits.suspects(
        project,
        commits.frames_from(payload_with("/app/pandora/checkout.py")),
        NOW,
    )

    assert found == []


def test_a_commit_older_than_the_window_is_not_a_suspect(project, release, mapping):
    """Should not blame a file nobody has touched in years."""
    commits.set_commits(
        project, release, [commit_payload(timestamp="2020-01-01T09:00:00Z")]
    )

    found = commits.suspects(
        project,
        commits.frames_from(payload_with("/app/pandora/checkout.py")),
        NOW,
    )

    assert found == []


def test_the_newest_commit_on_a_file_is_the_one_blamed(project, release, mapping):
    """Should name the last change, not every change."""
    commits.set_commits(
        project,
        release,
        [
            commit_payload(id="b" * 40, timestamp="2026-09-01T09:00:00Z"),
            commit_payload(id="c" * 40, timestamp="2026-09-08T09:00:00Z"),
        ],
    )

    found = commits.suspects(
        project,
        commits.frames_from(payload_with("/app/pandora/checkout.py")),
        NOW,
    )

    assert [suspect.commit.key for suspect in found] == ["c" * 40]


def test_a_project_with_no_code_mapping_has_no_suspects(project, release):
    """Should not run the query at all until the operator states the mapping."""
    commits.set_commits(project, release, [commit_payload()])

    found = commits.suspects(
        project,
        commits.frames_from(payload_with("/app/pandora/checkout.py")),
        NOW,
    )

    assert found == []


def test_a_commit_belonging_to_another_project_is_not_a_suspect(
    project, mapping, release
):
    """Should keep one project's history out of another's blame."""
    from pandora.core import models as core_models

    other = core_models.Project.objects.create(slug="other", name="Other")
    other_release = service.ensure_release(other, "9.9.9", "", NOW)
    commits.set_commits(other, other_release, [commit_payload()])

    found = commits.suspects(
        project,
        commits.frames_from(payload_with("/app/pandora/checkout.py")),
        NOW,
    )

    assert found == []


# frames


def test_in_app_frames_are_walked_nearest_the_fault_first():
    """Should blame the code that raised, not the framework that called it."""
    frames = commits.frames_from(payload_with("/app/pandora/checkout.py"))

    assert [frame["filename"] for frame in frames] == ["/app/pandora/checkout.py"]


def test_a_stack_with_no_in_app_frames_falls_back_to_all_of_them():
    """Should still say something when the SDK marked nothing in-app."""
    payload = {
        "exceptions": [
            {"frames": [{"filename": "a.py"}, {"filename": "b.py"}]},
        ]
    }

    frames = commits.frames_from(payload)

    assert [frame["filename"] for frame in frames] == ["b.py", "a.py"]


def test_a_payload_with_no_exception_has_no_frames():
    """Should return nothing for an alert, which has no stack at all."""
    assert commits.frames_from({"logentry": "boom"}) == []


def test_a_payload_that_is_not_a_mapping_has_no_frames():
    """Should not raise on a stored payload of the wrong shape."""
    assert commits.frames_from("boom") == []


# resolve via commit message


@pytest.mark.parametrize(
    "message",
    [
        "fixes #17",
        "Fix PANDORA-17",
        "closes #17",
        "resolved #17",
    ],
)
def test_a_commit_message_can_name_the_issue_it_closed(message):
    """Should accept the phrasings people already write in commit messages."""
    assert commits.issue_ids_fixed(message) == [17]


def test_a_message_naming_nothing_resolves_nothing():
    """Should not read every number in a message as an issue."""
    assert commits.issue_ids_fixed("bump timeout to 30") == []


def test_a_message_can_name_several_issues():
    """Should close all of them, the way one commit often does."""
    assert commits.issue_ids_fixed("fixes #3 and closes #9") == [3, 9]


# edges the commit path has to survive


def test_a_patch_set_that_is_not_a_list_is_refused(project, release):
    """Should name the mistake rather than store a commit with no files."""
    with pytest.raises(commits.CommitError):
        commits.set_commits(project, release, [commit_payload(patch_set="src/a.py")])


def test_a_patch_set_entry_that_is_not_an_object_is_refused(project, release):
    """Should refuse a half-formed payload rather than skip part of it."""
    with pytest.raises(commits.CommitError):
        commits.set_commits(project, release, [commit_payload(patch_set=["src/a.py"])])


def test_a_non_string_field_is_refused(project, release):
    """Should not coerce a number into a commit message."""
    with pytest.raises(commits.CommitError):
        commits.set_commits(project, release, [commit_payload(message=42)])


def test_a_duplicate_path_in_one_commit_is_stored_once(project, release):
    """Should not violate the per-commit uniqueness on a repeated path."""
    commits.set_commits(
        project,
        release,
        [
            commit_payload(
                patch_set=[
                    {"path": "src/a.py", "type": "M"},
                    {"path": "src/a.py", "type": "A"},
                ]
            )
        ],
    )

    assert release_models.CommitFile.objects.count() == 1


def test_a_leading_slash_is_stripped_from_a_path(project, release):
    """Should store the repository-relative path the mapping produces."""
    commits.set_commits(
        project,
        release,
        [commit_payload(patch_set=[{"path": "/src/a.py", "type": "M"}])],
    )

    assert release_models.CommitFile.objects.get().path == "src/a.py"


def test_an_empty_path_is_skipped(project, release):
    """Should not store a row nothing can be matched against."""
    commits.set_commits(
        project,
        release,
        [commit_payload(patch_set=[{"path": "  ", "type": "M"}])],
    )

    assert release_models.CommitFile.objects.count() == 0


def test_a_bitbucket_repository_is_recognised_by_name(project, release):
    """Should not guess GitHub for a Bitbucket URL."""
    commits.set_commits(
        project, release, [commit_payload(repository="bitbucket.test/team/app")]
    )

    repository = release_models.Repository.objects.get()
    result = (repository.provider, repository.url)
    expected = (release_models.RepositoryProvider.BITBUCKET, "")

    assert result == expected


def test_a_bare_repository_name_is_plain_git(project, release):
    """Should not invent a forge URL for a name that names no forge."""
    commits.set_commits(project, release, [commit_payload(repository="internal")])

    assert (
        release_models.Repository.objects.get().provider
        == release_models.RepositoryProvider.GIT
    )


def test_a_naive_timestamp_is_made_aware(project, release):
    """Should not store a naive datetime the comparisons would then reject."""
    commits.set_commits(
        project, release, [commit_payload(timestamp="2026-09-08T09:00:00")]
    )

    assert release_models.Commit.objects.get().committed_at.tzinfo is not None


def test_a_commit_with_no_message_has_no_summary(project, release):
    """Should render an empty line rather than raise on an empty message."""
    commits.set_commits(project, release, [commit_payload(message="")])

    assert release_models.Commit.objects.get().summary == ""


def test_a_mapping_list_that_is_empty_maps_nothing():
    """Should be the state of a project nobody has configured."""
    assert commits.map_path("/app/main.py", []) is None


def test_frames_from_a_payload_with_no_frames(project):
    """Should return nothing for an exception the SDK sent without a stack."""
    assert commits.frames_from({"exceptions": [{"type": "ValueError"}]}) == []


def test_frames_from_an_exception_list_of_the_wrong_shape():
    """Should not raise on a stored payload written by an older version."""
    result = (
        commits.frames_from({"exceptions": "boom"}),
        commits.frames_from({"exceptions": ["boom"]}),
        commits.frames_from({"exceptions": [{"frames": "boom"}]}),
        commits.frames_from({"exceptions": [{"frames": []}]}),
    )

    assert result == ([], [], [], [])


def test_a_frame_with_only_a_module_still_maps(project, mapping):
    """Should use whichever path field the SDK filled in."""
    frames = [{"module": "/app/pandora/checkout.py"}]

    found = commits.map_path(commits._frame_path(frames[0]), [mapping])

    assert found is not None


def test_a_frame_with_no_path_at_all_maps_nothing(project, mapping):
    """Should skip a frame that names no file."""
    assert commits._frame_path({"function": "charge"}) == ""
