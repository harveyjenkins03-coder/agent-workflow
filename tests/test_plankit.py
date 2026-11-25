"""plankit: plans parsed into tasks and blocks, applied with git, replayed in fresh worktrees."""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

from agent_workflow import plankit

FENCE = "`" * 3

PLAN = textwrap.dedent(f"""\
    # Plan: a small change

    ## Tasks

    ### Task T1: add a line

    - [ ] **Step 1: Write the failing test**

    Save this as `plans/diffs/T1-red.diff`:

    {FENCE}diff
    diff --git a/a.txt b/a.txt
    --- a/a.txt
    +++ b/a.txt
    @@ -1 +1,2 @@
     one
    +two
    {FENCE}

    - [ ] **Step 2: Make it pass**

    Create `b.txt`:

    {FENCE}text
    hello
    {FENCE}

    Now tidy the wording by hand.

    {FENCE}text
    prose only
    {FENCE}

    ### Task T2: a second file

    Create `c.txt`:

    {FENCE}text
    third
    {FENCE}

    ## Notes

    {FENCE}text
    outside any task
    {FENCE}
    """)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.name", "Test")
    git(r, "config", "user.email", "test" + "@" + "example.com")
    git(r, "config", "commit.gpgsign", "false")
    (r / "a.txt").write_text("one\n", encoding="utf-8")
    git(r, "add", "a.txt")
    git(r, "commit", "-q", "-m", "base")
    return r


@pytest.fixture
def plan(tmp_path: Path) -> Path:
    p = tmp_path / "plan.md"
    p.write_text(PLAN, encoding="utf-8")
    return p


def test_parse_finds_tasks_and_block_kinds(plan):
    tasks = plankit.parse(plan)
    assert [t.tid for t in tasks] == ["T1", "T2"]
    t1 = tasks[0]
    assert [(b.kind, b.target) for b in t1.blocks] == [
        ("diff", "plans/diffs/T1-red.diff"), ("create", "b.txt"), ("manual", None)]
    assert tasks[1].blocks[0].kind == "create"


def test_a_block_after_a_create_block_is_not_mistaken_for_one(plan):
    # The look-back for "Create `x`" stops at the previous block's closing fence.
    manual = plankit.parse(plan)[0].blocks[2]
    assert manual.kind == "manual"


def test_blocks_outside_tasks_are_ignored(plan):
    bodies = [b.body for t in plankit.parse(plan) for b in t.blocks]
    assert not any("outside any task" in b for b in bodies)


def test_phases_from_names_and_step_titles(plan):
    t1 = plankit.parse(plan)[0]
    assert plankit.phases(t1) == ["red", "green", "green"]


def test_pick_ranges_and_unknown_ids(plan):
    tasks = plankit.parse(plan)
    assert [t.tid for t in plankit.pick(tasks, ["T1..T2"])] == ["T1", "T2"]
    with pytest.raises(SystemExit):
        plankit.pick(tasks, ["T9"])


def test_apply_stages_diffs_and_creates_and_lists_manual(repo, plan):
    rc, manual = plankit.apply_blocks(repo, plankit.parse(plan), None, False)
    assert rc == 0
    assert manual == ["T1 L30 (text)"]
    assert (repo / "a.txt").read_text(encoding="utf-8") == "one\ntwo\n"
    staged = git(repo, "diff", "--cached", "--name-only").split()
    assert staged == ["a.txt", "b.txt", "c.txt"]


def test_check_only_changes_nothing(repo, plan):
    rc, _ = plankit.apply_blocks(repo, plankit.parse(plan), None, True)
    assert rc == 0
    assert git(repo, "status", "--porcelain") == ""


def test_apply_stops_at_the_first_failing_diff(repo, plan):
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    git(repo, "commit", "-qam", "drift")
    rc, _ = plankit.apply_blocks(repo, plankit.parse(plan), None, False)
    assert rc == 1
    assert not (repo / "b.txt").exists()


def test_only_red_applies_the_red_blocks(repo, plan):
    rc, _ = plankit.apply_blocks(repo, plankit.parse(plan)[:1], "red", False)
    assert rc == 0
    assert git(repo, "diff", "--cached", "--name-only").split() == ["a.txt"]


def test_newtree_refuses_bad_and_reused_names(repo, tmp_path):
    scratch = tmp_path / "scratch"
    with pytest.raises(SystemExit):
        plankit.newtree(repo, scratch, "../escape", "HEAD", None)
    path = plankit.newtree(repo, scratch, "one", "HEAD", None)
    assert (path / "a.txt").is_file()
    with pytest.raises(SystemExit):
        plankit.newtree(repo, scratch, "one", "HEAD", None)


def test_replay_matches_an_expected_diff(repo, plan, tmp_path):
    scratch = tmp_path / "scratch"
    first = plankit.newtree(repo, scratch, "first", "HEAD", None)
    plankit.apply_blocks(first, plankit.parse(plan), None, False)
    expect = tmp_path / "expect.diff"
    expect.write_bytes(subprocess.run(["git", "-C", str(first), "diff", "--cached", "--binary", "HEAD"],
                                      capture_output=True, check=True).stdout)
    assert plankit.replay(repo, scratch, plan, "HEAD", expect, (), "second") == 0
    expect.write_bytes(expect.read_bytes().replace(b"+third", b"+other"))
    assert plankit.replay(repo, scratch, plan, "HEAD", expect, (), "third") == 1


def test_split_by_file():
    diff = b"diff --git a/x b/x\n+1\ndiff --git a/y b/y\n+2\n"
    assert set(plankit.split_by_file(diff)) == {"x", "y"}


def test_built_report_lists_extras_without_failing():
    want = {"src/world.py": b"A", "data/table.toml": b"B"}
    got = dict(want, **{"records/tasks.csv": b"T", "docs/plan.md": b"P"})
    lines, bad = plankit.built_report(got, want)
    assert bad == 0
    assert lines == ["extra   docs/plan.md", "extra   records/tasks.csv"]


def test_built_report_counts_changed_and_missing():
    lines, bad = plankit.built_report({"a": b"A", "b": b"changed"}, {"a": b"A", "b": b"B", "c": b"C"})
    assert bad == 2
    assert lines == ["DIFFERS b", "MISSING c (in the snapshot, unchanged in the tree)"]


def test_an_empty_snapshot_is_refused_not_passed():
    with pytest.raises(SystemExit):
        plankit.built_report({"a": b"A"}, {})


def test_status_counts_by_column():
    porcelain = ["M  staged.py", "A  added.py", " M unstaged.py", "MM both.py", "?? new.py", "?? other.py"]
    assert plankit.status_counts(porcelain) == (3, 2, 2)
    assert plankit.status_counts([]) == (0, 0, 0)
