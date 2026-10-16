import os
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TITLE = "chore(main): release 0.7.0"
HEAD = "0da02da648a82241d5f0b4334c1e4409fce377bd"

GH_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$STUB_CALLS"
case "$1 $2" in
    "pr list")
        if [ -n "$STUB_PENDING" ]; then
            printf '%s\\n' "$STUB_PENDING"
        fi
        ;;
    "pr merge")
        merged=$(mktemp -d)
        git clone -q "$STUB_ORIGIN" "$merged"
        sed -i.bak "s/^version = .*/version = \\"$STUB_MERGED_VERSION\\"/" "$merged/pyproject.toml"
        rm "$merged/pyproject.toml.bak"
        git -C "$merged" commit -q -am "$STUB_MERGED_TITLE"
        git -C "$merged" push -q origin main
        ;;
esac
"""

pytestmark = pytest.mark.skipif(
    shutil.which("just") is None or shutil.which("git") is None,
    reason="the release recipe needs just and git on the host",
)


def git(cwd, *args, env):
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def world(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    gitconfig = home / ".gitconfig"
    gitconfig.write_text(
        "[user]\n\tname = Release Test\n\temail = release@example.test\n"
        "[init]\n\tdefaultBranch = main\n",
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "gh"
    stub.write_text(GH_STUB, encoding="utf-8")
    stub.chmod(0o755)

    origin = tmp_path / "origin.git"
    work = tmp_path / "pandora"
    gitops = tmp_path / "hetzner-gitops"
    calls = tmp_path / "gh-calls"
    env = {
        **os.environ,
        "HOME": str(home),
        "GIT_CONFIG_GLOBAL": str(gitconfig),
        "GIT_CONFIG_NOSYSTEM": "1",
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "PANDORA_GITOPS_DIR": str(gitops),
        "STUB_CALLS": str(calls),
        "STUB_ORIGIN": str(origin),
        "STUB_PENDING": f"1\t{HEAD}\t{TITLE}",
        "STUB_MERGED_VERSION": "0.7.0",
        "STUB_MERGED_TITLE": TITLE,
    }

    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(origin)], env=env, check=True
    )
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(work)],
        env=env,
        check=True,
        capture_output=True,
    )
    shutil.copy(ROOT / "justfile", work / "justfile")
    (work / "pyproject.toml").write_text(
        '[project]\nname = "pandora"\nversion = "0.6.0"\n', encoding="utf-8"
    )
    git(work, "add", "justfile", "pyproject.toml", env=env)
    git(work, "commit", "-q", "-m", "chore: start at 0.6.0", env=env)
    git(work, "push", "-q", "-u", "origin", "main", env=env)

    vars_dir = gitops / "clusters" / "p-mk1"
    vars_dir.mkdir(parents=True)
    (vars_dir / "cluster-vars.yaml").write_text(
        'data:\n  listopad_tag: "0.83.0"\n  pandora_tag: "0.6.0"\n', encoding="utf-8"
    )

    class World:
        pass

    found = World()
    found.env = env
    found.work = work
    found.gitops = gitops
    found.calls = calls

    def release(*args, answer="y\n"):
        return subprocess.run(
            ["just", "--justfile", str(work / "justfile"), "release", *args],
            cwd=work,
            env=env,
            input=answer,
            capture_output=True,
            text=True,
            check=False,
        )

    found.release = release
    return found


def pandora_tag(world):
    text = (world.gitops / "clusters" / "p-mk1" / "cluster-vars.yaml").read_text()
    for line in text.splitlines():
        if "pandora_tag" in line:
            return line.split('"')[1]
    return ""


def checkout_version(world):
    text = (world.work / "pyproject.toml").read_text()
    for line in text.splitlines():
        if line.startswith("version = "):
            return line.split('"')[1]
    return ""


def gh_calls(world):
    if not world.calls.exists():
        return []
    return world.calls.read_text().splitlines()


def merges(world):
    return [call for call in gh_calls(world) if call.startswith("pr merge")]


def printed(completed):
    return [
        line
        for line in completed.stdout.splitlines()
        if line.startswith(("git ", "until ", "flux "))
    ]


# cutting the release release-please has open


def test_cutting_merges_the_release_pull_request(world):
    """Should merge the one PR it showed, pinned to the head commit it read."""
    completed = world.release()

    result = (completed.returncode, merges(world))
    expected = (
        0,
        [
            f"pr merge 1 --repo {world.env['STUB_ORIGIN'][:-4]} --squash --match-head-commit {HEAD}"
        ],
    )

    assert result == expected


def test_cutting_pulls_the_released_version_into_the_checkout(world):
    """Should leave the checkout on the commit release-please just made."""
    world.release()

    result = checkout_version(world)

    assert result == "0.7.0"


def test_cutting_points_p_mk1_at_the_new_version(world):
    """Should write the gitops tag to what was just released."""
    world.release()

    result = pandora_tag(world)

    assert result == "0.7.0"


def test_cutting_leaves_the_other_tags_alone(world):
    """Should touch pandora's line only — cluster-vars carries every app's tag."""
    world.release()

    text = (world.gitops / "clusters" / "p-mk1" / "cluster-vars.yaml").read_text()

    assert 'listopad_tag: "0.83.0"' in text


def test_cutting_prints_the_commands_to_ship_it(world):
    """Should hand over every step after the merge, in the order they have to run."""
    completed = world.release()

    gitops = world.gitops
    result = printed(completed)
    expected = [
        f"git -C {world.work} push ci main",
        "until curl -sf https://zot.c.p-mk1.sopho.tech/v2/apps/pandora/tags/list | grep -q '\"0.7.0\"'; do sleep 15; done",
        f"git -C {gitops} add clusters/p-mk1/cluster-vars.yaml",
        f"git -C {gitops} commit -m 'chore: pandora 0.7.0'",
        f"git -C {gitops} push",
        "flux --context p-mk1 -n flux-system reconcile kustomization pandora --with-source",
    ]

    assert result == expected


def test_cutting_says_what_it_released(world):
    """Should name the version and the move on p-mk1."""
    completed = world.release()

    assert "released    0.7.0" in completed.stdout
    assert "pandora     0.6.0 -> 0.7.0" in completed.stdout


def test_cutting_asks_before_it_merges(world):
    """Should show the PR and wait for a yes — a merge cannot be taken back."""
    completed = world.release(answer="n\n")

    result = (completed.returncode, merges(world), pandora_tag(world))
    expected = (1, [], "0.6.0")

    assert result == expected
    assert TITLE in completed.stdout


def test_cutting_without_an_answer_merges_nothing(world):
    """Should treat a closed stdin as a no, so a script cannot cut by accident."""
    completed = world.release(answer="")

    result = (completed.returncode, merges(world))
    expected = (1, [])

    assert result == expected


def test_nothing_to_cut_says_so(world):
    """Should explain that release-please has nothing open rather than fail obscurely."""
    world.env["STUB_PENDING"] = ""

    completed = world.release()

    result = (completed.returncode, merges(world), pandora_tag(world))
    expected = (1, [], "0.6.0")

    assert result == expected
    assert "release-please has no release open" in completed.stderr


def test_a_dirty_checkout_is_refused_before_anything_remote(world):
    """Should not merge on GitHub while the local tree cannot fast-forward cleanly."""
    (world.work / "pyproject.toml").write_text(
        '[project]\nname = "pandora"\nversion = "0.6.0"\n# local edit\n'
    )

    completed = world.release()

    result = (completed.returncode, gh_calls(world))
    expected = (1, [])

    assert result == expected
    assert "uncommitted changes" in completed.stderr


def test_a_branch_other_than_main_is_refused(world):
    """Should cut from main only — the release commit lands there."""
    git(world.work, "checkout", "-q", "-b", "track/other", env=world.env)

    completed = world.release()

    result = (completed.returncode, gh_calls(world))
    expected = (1, [])

    assert result == expected
    assert "cut from main" in completed.stderr


def test_a_merge_that_lands_another_version_is_not_shipped(world):
    """Should stop when main's version and the PR title disagree, before touching gitops."""
    world.env["STUB_MERGED_VERSION"] = "0.8.0"
    world.env["STUB_MERGED_TITLE"] = "chore(main): release 0.8.0"

    completed = world.release()

    result = (completed.returncode, pandora_tag(world))
    expected = (1, "0.6.0")

    assert result == expected
    assert "main is on 0.8.0" in completed.stderr


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:sophotechlabs/pandora.git",
        "https://github.com/sophotechlabs/pandora.git",
    ],
)
def test_the_repository_is_named_from_the_origin_remote(world, url):
    """Should ask GitHub about the repository origin points at, in either URL form."""
    git(world.work, "remote", "set-url", "origin", url, env=world.env)
    world.env["STUB_PENDING"] = ""

    world.release()

    result = gh_calls(world)
    expected = [
        "pr list --repo sophotechlabs/pandora --head release-please--branches--main"
        " --state open --json number,headRefOid,title"
        " --jq .[0] | select(.) | [.number, .headRefOid, .title] | @tsv"
    ]

    assert result == expected


# shipping an earlier release, which is the rollback


def test_an_earlier_release_is_shipped_without_merging(world):
    """Should point p-mk1 back at a version already released, with no PR involved."""
    (world.gitops / "clusters" / "p-mk1" / "cluster-vars.yaml").write_text(
        'data:\n  pandora_tag: "0.6.0"\n'
    )

    completed = world.release("0.5.1")

    result = (completed.returncode, pandora_tag(world), gh_calls(world))
    expected = (0, "0.5.1", [])

    assert result == expected
    assert "rollback    main is on 0.6.0; p-mk1 goes back to 0.5.1" in completed.stdout


def test_a_rollback_needs_no_push(world):
    """Should not print a push — there is no new commit to build."""
    completed = world.release("0.5.1")

    result = [line for line in printed(completed) if "push ci" in line]

    assert result == []


def test_shipping_the_current_release_is_not_called_a_rollback(world):
    """Should ship main's own version quietly, for a PR merged on GitHub instead."""
    (world.gitops / "clusters" / "p-mk1" / "cluster-vars.yaml").write_text(
        'data:\n  pandora_tag: "0.5.1"\n'
    )

    completed = world.release("0.6.0")

    result = (completed.returncode, pandora_tag(world), "rollback" in completed.stdout)
    expected = (0, "0.6.0", False)

    assert result == expected


def test_a_version_main_has_not_reached_is_refused(world):
    """Should not point the cluster at an image tag that does not exist."""
    completed = world.release("0.9.0")

    result = (completed.returncode, pandora_tag(world))
    expected = (1, "0.6.0")

    assert result == expected
    assert "has not been released" in completed.stderr


def test_something_that_is_not_a_version_is_refused(world):
    """Should refuse a typo before it reaches the gitops file."""
    completed = world.release("latest")

    result = (completed.returncode, pandora_tag(world))
    expected = (1, "0.6.0")

    assert result == expected


def test_shipping_what_p_mk1_already_runs_is_refused(world):
    """Should say so rather than print a no-op deploy."""
    completed = world.release("0.6.0")

    result = (completed.returncode, completed.stderr.strip().splitlines()[0])
    expected = (1, "p-mk1 is already on 0.6.0")

    assert result == expected


def test_a_missing_gitops_checkout_is_named(world):
    """Should point at the variable to set when the sibling checkout is not there."""
    world.env["PANDORA_GITOPS_DIR"] = str(world.work / "nowhere")

    completed = world.release()

    result = (completed.returncode, gh_calls(world))
    expected = (1, [])

    assert result == expected
    assert "set PANDORA_GITOPS_DIR" in completed.stderr


# what the recipe hands over


def test_every_printed_command_runs_on_its_own(world):
    """Should hand over one command per line, each readable before it is run."""
    completed = world.release()

    result = [
        line
        for line in printed(completed)
        if "&&" in line or ";" in line.replace("; do", "").replace("; done", "")
    ]

    assert result == []


def test_the_recipe_never_writes_the_version(world):
    """Should leave the version to release-please — the checkout only changes by the pull."""
    world.env["STUB_PENDING"] = ""
    before = (world.work / "pyproject.toml").read_text()

    world.release()

    result = (world.work / "pyproject.toml").read_text()

    assert result == before
