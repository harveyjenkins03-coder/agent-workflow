"""The profile gate: how a new agent profile (a narrow helper, or an upgrade to an existing role) gets from a draft
to a file in the agents folder, with a person making the last step.

A profile is JSON. The gate checks its shape, its words and its tools, holds it in one of a few pending slots,
re-runs every check on assessment and writes a rendered preview as a .txt file next to the record. Promotion writes
one new agent file, never over an existing one, and only when a person confirms it at an interactive terminal outside
any agent session. Nothing here starts a process or an agent.

Every profile carries the locked principles block word for word. A profile whose block is missing, altered, extended
or carries another hash is refused. Everything a profile says is data, never an instruction.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Callable

from .injection import flat, reads_as_instruction, secret_kind, unhide

SCHEMA = 1
PRINCIPLES = {
    "non_destructive": True,
    "no_paid_calls_without_approval": True,
    "human_gates_absolute": True,
    "no_secret_in_context": True,
    "honest_about_what_was_not_checked": True,
    "no_self_replication": True,
}
PRINCIPLES_TEXT = {
    "non_destructive": "Non-destructive: never delete, reset, force or overwrite; every change is reversible and asked for.",
    "no_paid_calls_without_approval": "No paid call, key or credit is used without the owner's word.",
    "human_gates_absolute": "The owner's gates are absolute: merges, pushes, releases and approvals are theirs.",
    "no_secret_in_context": "No secret in context: never read, store, print or pass on a key, token or password.",
    "honest_about_what_was_not_checked": "Say plainly what was not checked or run.",
    "no_self_replication": "No self-replication: never create, copy, promote or start an agent or a process.",
}
# The SHA-256 of PRINCIPLES as canonical JSON (sorted keys, no spaces). A test keeps the two equal.
PRINCIPLES_SHA256 = "a1c5850a897f35ce33cd3fce9a2fe75079825b77293ee3700b747dc2ce6b824e"

MAX_PENDING = 4
LOCK_WAIT, LOCK_STALE = 5.0, 60.0
KINDS = ("helper", "role_upgrade")
ACTIVE = ("submitted", "awaiting_human", "failed_assessment")
READ_ONLY_TOOLS = ("Read", "Grep", "Glob")
BUILDER_TOOLS = READ_ONLY_TOOLS + ("Edit", "Write", "Bash")
DOMAINS = ("code", "tests", "docs", "design", "security", "data", "release")
AGENT_SESSION_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")
SLUG_RE = re.compile(r"[a-z][a-z0-9-]{2,39}")
UNSAFE_NAME = re.compile(r"key|token|secret|password|cred")
CITATION = re.compile(r"[\w./\\-]+\.(?:csv|jsonl|rs|md|toml|py|ts|json|txt|yml|yaml)(?::\d+)?|\b[0-9a-f]{7,40}\b")
FORBIDDEN = re.compile(
    r"(?i)settings(?:\.local)?\.json|\.claude[\\/]+(?:agents|hooks|settings)|profile_gate|\bpromot\w*|"
    r"run_in_background|\bdaemon|\bcrontab\b|\bschtasks\b|start-process|\bnohup\b|scheduled task")
WORDS = {"name": 4, "rationale": 60, "description": 25, "thinking_style": 6, "title": 6, "body": 120}
RETURN_CAP = 300
MAX_SECTIONS = 6
# Every character str.splitlines() treats as a line break. A single-line field holding one could end the agent
# file's header early, as could a front-matter delimiter.
LINE_BREAKS = frozenset(chr(c) for c in (10, 11, 12, 13, 0x1C, 0x1D, 0x1E, 0x85, 0x2028, 0x2029))
DELIMITER = "---"


class Rejected(ValueError):
    """Refused by a check; nothing was written."""


def canonical_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def principles_hash(flags: dict) -> str:
    return hashlib.sha256(canonical_json(flags)).hexdigest()


def locked_principles() -> dict:
    """The block every profile carries word for word: the flags and their pinned hash."""
    return {**PRINCIPLES, "sha256": PRINCIPLES_SHA256}


def principles_ok(profile: dict) -> bool:
    block = profile.get("principles") if isinstance(profile, dict) else None
    if not isinstance(block, dict) or set(block) != set(PRINCIPLES) | {"sha256"}:
        return False
    flags = {k: block[k] for k in PRINCIPLES}
    return all(v is True for v in flags.values()) and block["sha256"] == PRINCIPLES_SHA256 == principles_hash(flags)


def profile_hash(profile: dict) -> str:
    return hashlib.sha256(canonical_json(profile)).hexdigest()


def words(text) -> int:
    return len(str(text or "").split())


def one_line(where: str, value) -> None:
    """Refuse a line break or a front-matter delimiter in a field that is rendered on one line."""
    if isinstance(value, str) and (any(c in LINE_BREAKS for c in value) or DELIMITER in value):
        raise Rejected(f"{where}: one line, with no line break and no '{DELIMITER}'")


def texts(profile: dict) -> dict[str, str]:
    """Every word a profile carries, by where it sits, for the screen and the forbidden-target check."""
    traits = profile.get("traits") or {}
    out = {"slug": str(profile.get("slug", "")), "name": str(profile.get("name", "")),
           "rationale": str(profile.get("rationale", "")), "description": str(traits.get("description", "")),
           "thinking style": str(traits.get("thinking_style", ""))}
    for i, s in enumerate(traits.get("sections") or []):
        if isinstance(s, dict):
            out[f"section {i + 1} title"] = str(s.get("title", ""))
            out[f"section {i + 1} body"] = str(s.get("body", ""))
    return out


def _interactive() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _ask_console(question: str) -> bool:
    print(question)
    return input("Type yes to promote: ").strip() == "yes"


class Gate:
    def __init__(self, root: Path | str, agents_dir: Path | str, roles: dict[str, set[str]],
                 domains: tuple[str, ...] = DOMAINS):
        self.root = Path(root)
        self.pending = self.root / "pending"
        self.agents_dir = Path(agents_dir)
        self.roles = {k: set(v) for k, v in roles.items()}
        self.domains = domains

    # ------------------------------------------------------------ records

    def _path(self, pid: str) -> Path:
        if not re.fullmatch(r"pr_[0-9a-f]{8}", pid or ""):
            raise Rejected("a record id is pr_ and eight hex digits")
        return self.pending / f"{pid}.json"

    def record(self, pid: str) -> dict:
        path = self._path(pid)
        if not path.is_file():
            raise Rejected(f"no record {pid}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _save(self, rec: dict) -> None:
        self._plain()
        tmp = self._path(rec["id"]).with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self._path(rec["id"]))

    def records(self) -> list[dict]:
        if not self.pending.is_dir():
            return []
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.pending.glob("pr_*.json"))]

    def _plain(self) -> None:
        """The gate writes only into plain folders inside its root: a link or junction could aim a write elsewhere,
        for example into the agents folder."""
        root = self.root.resolve()
        for p in (self.root, self.pending):
            if p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()):
                raise Rejected(f"{p.name} is a link or a junction: the gate writes only into plain folders")
            if p.exists() and p != self.root and not p.resolve().is_relative_to(root):
                raise Rejected(f"{p.name} resolves outside the gate's folder")

    @contextlib.contextmanager
    def _lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "submit.lock"
        deadline = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                try:
                    if time.time() - path.stat().st_mtime > LOCK_STALE:   # left by a run that crashed
                        path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise Rejected("another submission holds the lock; try again in a moment") from None
                time.sleep(0.05)
        try:
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            yield
        finally:
            path.unlink(missing_ok=True)

    # ------------------------------------------------------------ checks

    def check_shape(self, p) -> None:
        if not isinstance(p, dict):
            raise Rejected("a profile is a JSON object")
        if not principles_ok(p):
            raise Rejected("the principles block is missing or altered: copy locked_principles() word for word")
        slug = p.get("slug")
        if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug) or slug.endswith("-"):
            raise Rejected("slug: lower case letters, digits and hyphens, 3 to 40 characters")
        if UNSAFE_NAME.search(slug):
            raise Rejected("slug: never key, token, secret, password or cred")
        if slug in self.roles:
            raise Rejected(f"slug: {slug} is an existing role")
        traits = p.get("traits")
        one_line("name", p.get("name"))
        one_line("rationale", p.get("rationale"))
        if isinstance(traits, dict):
            one_line("description", traits.get("description"))
            one_line("thinking_style", traits.get("thinking_style"))
            for s in traits.get("sections") or []:
                if isinstance(s, dict):
                    one_line("section title", s.get("title"))
        if not isinstance(p.get("name"), str) or not 1 <= words(p["name"]) <= WORDS["name"]:
            raise Rejected(f"name: 1 to {WORDS['name']} words")
        if p.get("kind") not in KINDS:
            raise Rejected(f"kind: one of {', '.join(KINDS)}")
        if p["kind"] == "role_upgrade" and p.get("target_role") not in self.roles:
            raise Rejected("a role upgrade names its target_role, an existing role")
        mentors = p.get("mentors")
        if (not isinstance(mentors, list) or not 1 <= len(mentors) <= 2 or len(set(mentors)) != len(mentors)
                or any(m not in self.roles for m in mentors)):
            raise Rejected("mentors: one or two distinct existing roles")
        why = p.get("rationale")
        if not isinstance(why, str) or not 1 <= words(why) <= WORDS["rationale"]:
            raise Rejected(f"rationale: 1 to {WORDS['rationale']} words")
        if not CITATION.search(why):
            raise Rejected("rationale cites no record (file:line or a commit): no evidence of need, no profile")
        self.check_tools(p, against_mentors=False)
        if not isinstance(traits, dict):
            raise Rejected("traits: an object")
        for key in ("description", "thinking_style"):
            if not isinstance(traits.get(key), str) or not 1 <= words(traits[key]) <= WORDS[key]:
                raise Rejected(f"{key}: 1 to {WORDS[key]} words")
        doms = traits.get("domains")
        if not isinstance(doms, list) or not doms or any(d not in self.domains for d in doms):
            raise Rejected(f"domains: one or more of {', '.join(self.domains)}")
        cap = traits.get("return_cap_words")
        if type(cap) is not int or not 50 <= cap <= RETURN_CAP:
            raise Rejected(f"return_cap_words: a whole number from 50 to {RETURN_CAP}")
        secs = traits.get("sections")
        if not isinstance(secs, list) or not 1 <= len(secs) <= MAX_SECTIONS:
            raise Rejected(f"sections: 1 to {MAX_SECTIONS}")
        for s in secs:
            if (not isinstance(s, dict) or not 1 <= words(s.get("title")) <= WORDS["title"]
                    or not 1 <= words(s.get("body")) <= WORDS["body"]):
                raise Rejected(f"each section: a title of 1 to {WORDS['title']} words and a body of 1 to {WORDS['body']}")

    def check_words(self, p: dict) -> None:
        for where, text in texts(p).items():
            plain = unhide(text)
            label = secret_kind(plain)
            if label:
                raise Rejected(f"secret-shaped text ({label}) in the {where}")
            if reads_as_instruction(plain):
                raise Rejected(f"the {where} reads as an instruction")
            if FORBIDDEN.search(plain):
                raise Rejected(f"the {where} reaches for agent files, settings, this gate or a background process")

    def check_tools(self, p: dict, against_mentors: bool = True) -> None:
        tools, ceiling = p.get("tools"), p.get("tool_ceiling")
        if not isinstance(tools, list) or not tools or len(set(tools)) != len(tools):
            raise Rejected("tools: a non-empty list without repeats")
        if ceiling == "read_only":
            allowed = READ_ONLY_TOOLS
        elif ceiling == "builder":
            if p.get("builder_requested_by") not in (p.get("mentors") or []):
                raise Rejected("a builder ceiling needs builder_requested_by: one of its mentors")
            allowed = BUILDER_TOOLS
        else:
            raise Rejected("tool_ceiling: read_only or builder")
        extra = [t for t in tools if t not in allowed]
        if extra:
            raise Rejected(f"tools beyond the {ceiling} ceiling: {', '.join(map(str, extra))}")
        if against_mentors:
            for m in p.get("mentors") or []:
                wider = set(tools) - self.roles.get(m, set())
                if wider:
                    raise Rejected(f"tools wider than its mentor {m} holds: {', '.join(sorted(wider))}")

    # ------------------------------------------------------------ the lifecycle

    def submit(self, p, by: str = "planner") -> str:
        """A drafted profile into a free pending slot, after the shape, principles, word and tool checks."""
        self.check_shape(p)
        self.check_words(p)
        self._plain()
        # Counting the slots and filling one happen under one lock, so two submissions cannot both take the last.
        with self._lock():
            live = [r for r in self.records() if r["status"] in ACTIVE]
            if len(live) >= MAX_PENDING:
                raise Rejected(f"all {MAX_PENDING} pending slots are taken: assess, withdraw or decide first")
            if any(r["profile"].get("slug") == p["slug"] for r in live):
                raise Rejected(f"{p['slug']} is already waiting")
            pid = "pr_" + secrets.token_hex(4)
            self.pending.mkdir(parents=True, exist_ok=True)
            self._plain()
            self._save({"schema": SCHEMA, "id": pid, "status": "submitted", "submitted_by": flat(by, 40),
                        "profile": copy.deepcopy(p), "profile_sha256": profile_hash(p), "assessment": None,
                        "withdrawn_note": ""})
        return pid

    def render(self, rec: dict) -> str:
        """The agent file a promotion would write."""
        p = rec["profile"]
        t = p["traits"]
        lines = ["---", f"name: {p['slug']}", f"description: '{t['description'].replace(chr(39), '')}'",
                 f"tools: {', '.join(p['tools'])}", "model: inherit", "---", "",
                 f"<!-- profile {rec['id']}, sha256 {rec['profile_sha256']} -->", "",
                 f"# {p['name']}", "",
                 f"You are {p['name']}, a narrow helper mentored by {' and '.join(p['mentors'])}. The project's rules "
                 "and the owner's words override yours.", "",
                 "## Principles (locked)", *[f"- {PRINCIPLES_TEXT[k]}" for k in PRINCIPLES], "",
                 f"## Thinking style: {t['thinking_style']}",
                 f"Domains: {', '.join(t['domains'])}. Why you exist: {p['rationale']}", ""]
        for s in t["sections"]:
            lines += [f"## {s['title']}", s["body"], ""]
        lines += [f"## Return (at most {t['return_cap_words']} words)",
                  "Tag each claim [VERIFIED], [INFERRED] or [NOT RUN] with its file:line.", ""]
        return "\n".join(lines)

    def _fitness(self, rec: dict) -> dict:
        p = rec.get("profile") or {}
        checks = []

        def check(name: str, fn) -> None:
            try:
                fn()
                checks.append({"name": name, "ok": True, "detail": ""})
            except (Rejected, KeyError, TypeError, AttributeError) as err:
                checks.append({"name": name, "ok": False, "detail": str(err)[:200]})

        def unchanged():
            if profile_hash(p) != rec.get("profile_sha256"):
                raise Rejected("the profile changed after submission; submit the new version instead")

        def again():
            # Every submission check, again: a record whose hash was recomputed passes `unchanged`, never these.
            self.check_shape(p)
            self.check_words(p)

        def no_replication():
            if any(t not in BUILDER_TOOLS for t in p.get("tools") or []):
                raise Rejected("a tool beyond the builder set could start other agents or reach the network")

        check("submission_checks", again)
        check("unchanged_since_submission", unchanged)
        def principles():
            if not principles_ok(p):
                raise Rejected("the principles block is missing or altered")

        check("principles_locked", principles)
        check("tools_within_mentors", lambda: self.check_tools(p))
        check("no_self_replication", no_replication)
        return {"passed": all(c["ok"] for c in checks), "checks": checks}

    def assess(self, pid: str) -> dict:
        rec = self.record(pid)
        if rec["status"] not in ACTIVE:
            raise Rejected(f"{pid} is {rec['status']}: only a pending profile is assessed")
        card = self._fitness(rec)
        if card["passed"]:
            # A .txt, never a .md: an .md that reached an agents folder would be read as an agent.
            self._plain()
            (self.pending / f"{pid}.preview.txt").write_text(self.render(rec), encoding="utf-8", newline="\n")
        rec["assessment"] = card
        rec["status"] = "awaiting_human" if card["passed"] else "failed_assessment"
        self._save(rec)
        return card

    def withdraw(self, pid: str, note: str) -> None:
        rec = self.record(pid)
        if rec["status"] not in ACTIVE:
            raise Rejected(f"{pid} is {rec['status']}")
        note = flat(unhide(note or ""), 200)
        if not note or secret_kind(note) or reads_as_instruction(note):
            raise Rejected("withdraw needs a plain note of why")
        rec["status"], rec["withdrawn_note"] = "withdrawn", note
        self._save(rec)

    def promote(self, pid: str, confirmed: bool, interactive: Callable[[], bool] | None = None,
                ask: Callable[[str], bool] | None = None) -> Path:
        """A person's promotion of one assessed profile into one new agent file. Every gate comes before any write."""
        if not confirmed:
            raise Rejected("promotion is a person's act: pass confirmed=True (the --confirmed flag)")
        if any(os.environ.get(name) for name in AGENT_SESSION_ENV):
            raise Rejected("promotion never runs inside an agent session: run it in your own terminal")
        if not (interactive or _interactive)():
            raise Rejected("promotion needs a person at an interactive terminal")
        rec = self.record(pid)
        if rec["status"] != "awaiting_human":
            raise Rejected(f"{pid} is {rec['status']}: only an assessed profile is promoted")
        card = self._fitness(rec)
        if not card["passed"]:
            raise Rejected("the profile no longer passes its assessment")
        slug = rec["profile"]["slug"]
        target = self.agents_dir / f"{slug}.md"
        # The file lands directly in the agents folder or nowhere.
        if not SLUG_RE.fullmatch(slug) or target.parent.resolve() != self.agents_dir.resolve():
            raise Rejected("the slug does not name one file in the agents folder")
        if target.exists():
            raise Rejected(f"{target.name} already exists; nothing was overwritten")
        p = rec["profile"]
        question = (f"Promote '{p['name']}' ({pid})?\nMentors: {', '.join(p['mentors'])}\nTools: {', '.join(p['tools'])}"
                    f"\nThis writes one new file: {target.name}")
        if not (ask or _ask_console)(question):
            raise Rejected("the answer was no: nothing was written")
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        with open(target, "x", encoding="utf-8", newline="\n") as f:   # "x": never over a file that appeared meanwhile
            f.write(self.render(rec))
        rec["status"] = "promoted"
        self._save(rec)
        return target
