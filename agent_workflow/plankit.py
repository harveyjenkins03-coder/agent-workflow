"""plankit: shared helpers for agents that plan, pre-check, build or review a written plan.

It replaces the one-off extract, apply and replay scripts each agent would otherwise write for itself. It never
commits, pushes, resets, cleans or stashes. Git does all the work, so a replay needs no compiler.

Commands
  outline  PLAN                      tasks with line ranges and their code blocks (kind, target)
  extract  PLAN TASK [--out DIR]     write a task's blocks to files
  apply    PLAN TASK... --tree DIR [--only red|green] [--check]
                                     apply the tasks' diff and create-file blocks in plan order with
                                     `git apply --index`; stop at the first failure; list the blocks
                                     it cannot apply (prose edits) as MANUAL
  newtree  NAME [--base REV] [--diff FILE]
                                     a fresh detached worktree at REV, optionally with a saved diff
                                     applied and staged; use it instead of resetting a used tree
  snapshot --tree DIR --base REV --out FILE
                                     save a tree's staged changes against REV as a binary diff
  replay   PLAN --base REV [--expect FILE] [--ignore PREFIX...] [--name NAME]
                                     newtree, apply every task, then compare file by file with an
                                     expected final diff
  built    --tree DIR --base REV [--rev REV] --expect FILE
                                     after a build: is the tree's `git diff BASE REV` the planner's
                                     snapshot, for the snapshot's files? Lists what else changed.
  trees                              the scratch worktrees with staged, unstaged and untracked counts

The repository is --repo, $PLANKIT_REPO or the current directory's repository. Scratch worktrees go in --scratch,
$PLANKIT_SCRATCH or a folder named <repository>-scratch beside the repository.

Plan format it understands (planners: write every code change this way):
  * a fenced diff block whose preceding lines say  Save this as `<dir>/T<n>-<red|green>[-k].diff`
  * a fenced block whose preceding lines say  Create `<path>`  (the whole new file)
Anything else inside a task is reported as MANUAL.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

TASK_RE = re.compile(r"^#{2,4} Task ([A-Z]{1,4}\d+[a-z]?)\b")
FENCE_RE = re.compile(r"^```([A-Za-z0-9_+-]*)\s*$")
SAVE_RE = re.compile(r"Save (?:this|it) as `([^`]+\.diff)`")
CREATE_RE = re.compile(r"Create `([^`]+)`")
STEP_RE = re.compile(r"^\s*- \[[ x]\] \*\*Step [^:]*:\s*(.*?)\*\*")
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
LOOKBACK = 8


@dataclass
class Block:
    task: str
    start: int   # 1-based line of the opening fence
    end: int     # 1-based line of the closing fence
    lang: str
    body: str
    kind: str    # diff | create | manual
    target: str | None   # save-as name for diffs, path for creates
    step: str = ""       # the title of the step the block sits in

    @property
    def phase(self) -> str:
        if self.kind == "diff" and self.target:
            m = re.search(r"-(red|green)(?:-\w+)?\.diff$", self.target)
            if m:
                return m.group(1)
        return "any"


@dataclass
class Task:
    tid: str
    title: str
    start: int
    end: int
    blocks: list[Block] = field(default_factory=list)


def _last_pos(text: str, rx: re.Pattern) -> int:
    pos = -1
    for m in rx.finditer(text):
        pos = m.start()
    return pos


def parse(plan: Path) -> list[Task]:
    lines = Path(plan).read_text(encoding="utf-8").splitlines()
    tasks: list[Task] = []
    cur: Task | None = None
    step = ""
    last_close = -1
    i = 0
    while i < len(lines):
        line = lines[i]
        sm = STEP_RE.match(line)
        if sm:
            step = sm.group(1)
        m = TASK_RE.match(line)
        if m:
            if cur:
                cur.end = i
            cur = Task(m.group(1), line.strip(), i + 1, len(lines))
            tasks.append(cur)
            step = ""
            i += 1
            continue
        if line.startswith("## ") and cur:
            cur.end = i
            cur = None
            i += 1
            continue
        f = FENCE_RE.match(line)
        if f:
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            if cur is not None:
                body = "\n".join(lines[i + 1:j]) + "\n"
                # Look back a few lines, but never past the previous block: its "Create" is not this block's.
                before = "\n".join(lines[max(cur.start, last_close + 1, i - LOOKBACK):i])
                saves, creates, lang = SAVE_RE.findall(before), CREATE_RE.findall(before), f.group(1)
                if lang == "diff":
                    kind, target = "diff", (saves[-1] if saves else None)
                elif creates and _last_pos(before, CREATE_RE) > _last_pos(before, SAVE_RE):
                    kind, target = "create", creates[-1]
                else:
                    kind, target = "manual", None
                cur.blocks.append(Block(cur.tid, i + 1, j + 1, lang, body, kind, target, step))
            last_close = j
            i = j + 1
            continue
        i += 1
    return tasks


def pick(tasks: list[Task], wanted: list[str]) -> list[Task]:
    by_id = {t.tid: t for t in tasks}
    ids = [t.tid for t in tasks]
    out = []
    for w in wanted:
        if ".." in w:
            a, b = w.split("..")
            if a not in by_id or b not in by_id:
                sys.exit(f"plankit: no task range {w} in the plan")
            out.extend(by_id[x] for x in ids[ids.index(a):ids.index(b) + 1])
        elif w in by_id:
            out.append(by_id[w])
        else:
            sys.exit(f"plankit: no task {w} in the plan")
    return out


def phases(task: Task) -> list[str]:
    """Each block's phase: its own red/green name, else the phase of the step it sits in.

    The first block named green (or the first block under a step whose title says "pass") starts the green
    phase; everything before it is red. A task with neither is all "any"."""
    named = [b.phase for b in task.blocks]
    first_green = next((i for i, p in enumerate(named) if p == "green"), None)
    if first_green is None:
        for i, b in enumerate(task.blocks):
            if b.step and re.search(r"\bpass", b.step, re.I):
                first_green = i
                break
    if first_green is None:
        return named
    return [p if p != "any" else ("red" if i < first_green else "green") for i, p in enumerate(named)]


def git(tree: Path, *args: str, inp: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", "-C", str(tree), *args], input=inp, capture_output=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.decode(errors='replace').strip()}")
    return r


def apply_blocks(tree: Path, tasks: list[Task], only: str | None, check_only: bool) -> tuple[int, list[str]]:
    tree = Path(tree)
    applied, manual = 0, []
    for t in tasks:
        for b, ph in zip(t.blocks, phases(t)):
            if only and ph not in (only, "any"):
                continue
            if b.kind == "diff":
                r = git(tree, "apply", "--check", "--index", "-", inp=b.body.encode(), check=False)
                if r.returncode != 0:
                    print(f"FAIL {t.tid} L{b.start} {b.target or ''}: {r.stderr.decode(errors='replace').strip()[:400]}")
                    return 1, manual
                if not check_only:
                    git(tree, "apply", "--index", "-", inp=b.body.encode())
                applied += 1
            elif b.kind == "create":
                assert b.target is not None
                dest = (tree / b.target).resolve()
                if not dest.is_relative_to(tree.resolve()):
                    print(f"FAIL {t.tid} L{b.start}: {b.target} is outside the tree")
                    return 1, manual
                if not check_only:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(b.body.encode("utf-8"))
                    git(tree, "add", "--", b.target)
                applied += 1
            else:
                manual.append(f"{t.tid} L{b.start} ({b.lang or 'text'})")
    print(f"ok: {applied} blocks {'checked' if check_only else 'applied and staged'}")
    return 0, manual


def newtree(repo: Path, scratch: Path, name: str, base: str, diff: str | Path | None) -> Path:
    if not NAME_RE.fullmatch(name or ""):
        sys.exit("plankit: a tree name is letters, digits, dot, dash and underscore, starting with a letter or digit")
    path = Path(scratch) / name
    if path.exists():
        sys.exit(f"plankit: {path.name} exists; pick a new name (never reuse a used tree)")
    Path(scratch).mkdir(parents=True, exist_ok=True)
    git(Path(repo), "worktree", "add", "--detach", str(path), base)
    if diff:
        git(path, "apply", "--index", "--binary", str(Path(diff).resolve()))
    return path


def split_by_file(diff: bytes) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for chunk in re.split(rb"(?m)^(?=diff --git )", diff.replace(b"\r\n", b"\n")):
        m = re.match(rb"diff --git a/(\S+) b/(\S+)", chunk)
        if m:
            out[m.group(2).decode()] = chunk
    return out


def replay(repo: Path, scratch: Path, plan: Path, base: str, expect: Path | None, ignore: tuple[str, ...],
           name: str) -> int:
    tree = newtree(repo, scratch, name, base, None)
    print(f"tree {tree.name}")
    rc, manual = apply_blocks(tree, parse(plan), None, False)
    for m in manual:
        print(f"MANUAL {m}")
    if rc or not expect:
        return rc
    got = split_by_file(git(tree, "diff", "--cached", "--binary", base).stdout)
    want = split_by_file(Path(expect).read_bytes())
    bad = 0
    for f in sorted(set(got) | set(want)):
        if ignore and f.startswith(tuple(ignore)):
            continue
        if f not in got:
            print(f"MISSING {f} (in the expected diff only)")
            bad += 1
        elif f not in want:
            print(f"EXTRA   {f} (in the replay only)")
            bad += 1
        elif got[f] != want[f]:
            print(f"DIFFERS {f}")
            bad += 1
    print(f"compare: {len(want)} expected files, {bad} mismatches")
    return 1 if bad else 0


def built_report(got: dict[str, bytes], want: dict[str, bytes]) -> tuple[list[str], int]:
    """Compare a tree's per-file diffs with the planner's snapshot. A snapshot file changed differently, or not
    changed, is a mismatch; files changed beyond the snapshot (plans, records, review fixes) are listed as extra
    and are not mismatches. An empty snapshot is refused: a wrong path must not read as 0 mismatches."""
    if not want:
        sys.exit("plankit: the snapshot holds no file diff (wrong --expect path?)")
    lines, bad = [], 0
    for f in sorted(want):
        if f not in got:
            lines.append(f"MISSING {f} (in the snapshot, unchanged in the tree)")
            bad += 1
        elif got[f] != want[f]:
            lines.append(f"DIFFERS {f}")
            bad += 1
    lines += [f"extra   {f}" for f in sorted(set(got) - set(want))]
    return lines, bad


def status_counts(porcelain: list[str]) -> tuple[int, int, int]:
    """(staged, unstaged, untracked) from `git status --porcelain` lines."""
    tracked = [s for s in porcelain if not s.startswith("??")]
    return (sum(1 for s in tracked if s[:1] != " "), sum(1 for s in tracked if s[1:2] != " "),
            len(porcelain) - len(tracked))


# ---------------------------------------------------------------------------------------------- command line


def _repo(arg: str | None) -> Path:
    start = Path(arg or os.environ.get("PLANKIT_REPO") or ".")
    return Path(git(start, "rev-parse", "--show-toplevel").stdout.decode().strip())


def _scratch(arg: str | None, repo: Path) -> Path:
    given = arg or os.environ.get("PLANKIT_SCRATCH")
    return Path(given) if given else repo.parent / f"{repo.name}-scratch"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="plankit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo")
    p.add_argument("--scratch")
    s = p.add_subparsers(dest="cmd", required=True)
    o = s.add_parser("outline")
    o.add_argument("plan")
    e = s.add_parser("extract")
    e.add_argument("plan")
    e.add_argument("task")
    e.add_argument("--out")
    ap = s.add_parser("apply")
    ap.add_argument("plan")
    ap.add_argument("tasks", nargs="+", help="T3 or T3..T7")
    ap.add_argument("--tree", required=True)
    ap.add_argument("--only", choices=["red", "green"])
    ap.add_argument("--check", action="store_true")
    n = s.add_parser("newtree")
    n.add_argument("name")
    n.add_argument("--base", default="HEAD")
    n.add_argument("--diff")
    sn = s.add_parser("snapshot")
    sn.add_argument("--tree", required=True)
    sn.add_argument("--base", required=True)
    sn.add_argument("--out", required=True)
    r = s.add_parser("replay")
    r.add_argument("plan")
    r.add_argument("--base", required=True)
    r.add_argument("--expect")
    r.add_argument("--ignore", nargs="*", default=[])
    r.add_argument("--name")
    b = s.add_parser("built")
    b.add_argument("--tree", required=True)
    b.add_argument("--base", required=True)
    b.add_argument("--rev", default="HEAD")
    b.add_argument("--expect", required=True)
    s.add_parser("trees")
    a = p.parse_args(argv)

    if a.cmd == "outline":
        for t in parse(Path(a.plan)):
            print(f"{t.tid}  lines {t.start}-{t.end}  {t.title[:100]}")
            for blk, ph in zip(t.blocks, phases(t)):
                print(f"    {blk.kind:6} {ph:5} L{blk.start}-{blk.end} {blk.lang or '-':5} {blk.target or ''}"
                      f"  [{blk.step[:40]}]")
        return 0
    if a.cmd == "extract":
        (task,) = pick(parse(Path(a.plan)), [a.task])
        out = Path(a.out or f"plankit-out/{task.tid}")
        out.mkdir(parents=True, exist_ok=True)
        for i, blk in enumerate(task.blocks, 1):
            name = (Path(blk.target).name if blk.kind == "diff" and blk.target
                    else f"{task.tid}-block{i}-{blk.kind if blk.kind == 'create' else blk.lang or 'text'}.txt")
            (out / name).write_bytes(blk.body.encode("utf-8"))
            print(f"{blk.kind:6} L{blk.start:<6} -> {out / name}")
        return 0
    if a.cmd == "apply":
        rc, manual = apply_blocks(Path(a.tree), pick(parse(Path(a.plan)), a.tasks), a.only, a.check)
        for m in manual:
            print(f"MANUAL {m}: not a Save-as diff or Create block; do it by hand")
        return rc
    if a.cmd == "snapshot":
        diff = git(Path(a.tree), "diff", "--cached", "--binary", a.base).stdout
        Path(a.out).write_bytes(diff)
        print(f"{a.out}: {len(diff)} bytes")
        return 0
    if a.cmd == "built":
        got = split_by_file(git(Path(a.tree), "diff", "--binary", a.base, a.rev).stdout)
        want = split_by_file(Path(a.expect).read_bytes())
        lines, bad = built_report(got, want)
        print("\n".join(lines))
        print(f"built: {len(want)} snapshot files, {bad} mismatches, {len(set(got) - set(want))} extra files")
        return 1 if bad else 0
    repo = _repo(a.repo)
    scratch = _scratch(a.scratch, repo)
    if a.cmd == "newtree":
        path = newtree(repo, scratch, a.name, a.base, a.diff)
        stat = git(path, "diff", "--cached", "--shortstat").stdout.decode().strip() or "clean"
        print(f"{path}  ({stat})")
        return 0
    if a.cmd == "replay":
        name = a.name or f"replay-{Path(a.plan).stem[-20:]}"
        return replay(repo, scratch, Path(a.plan), a.base, Path(a.expect) if a.expect else None, tuple(a.ignore), name)
    if not scratch.is_dir():
        print("no scratch trees")
        return 0
    for t in sorted(q for q in scratch.iterdir() if q.is_dir()):
        r = git(t, "status", "--porcelain", check=False)
        if r.returncode != 0:
            print(f"{t.name:28} not a worktree")
            continue
        staged, unstaged, untracked = status_counts(r.stdout.decode(errors="replace").splitlines())
        head = git(t, "rev-parse", "--short", "HEAD", check=False).stdout.decode().strip()
        print(f"{t.name:28} {head:8} staged {staged:3}  unstaged {unstaged:3}  untracked {untracked:3}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
