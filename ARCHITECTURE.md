# Architecture

Four modules with no dependencies on each other except that `memory` and `profile_gate` both use `injection`.

```
plankit.py        plan (Markdown) -> tasks -> blocks -> git apply --index -> compare with an expected diff
injection.py      text -> secret kind or None; text -> instruction-shaped or not
memory.py         remember -> screen -> SQLite + FTS5 -> recall (ranked, capped, advisory)
profile_gate.py   draft -> checks -> pending slot -> assessment -> preview -> a person promotes
```

## plankit

A plan is Markdown. Each task starts with a `### Task T<n>` heading. A code change is either a fenced `diff` block
introduced by "Save this as `...-red.diff`" (or `-green`), or a fenced block introduced by "Create `path`" holding
the whole file. Anything else in a task is reported as MANUAL, so a planner can see which steps a tool cannot
replay.

Decisions:

- **Git does the work.** `git apply --check --index` before each block, stopping at the first failure. A replay is
  a fresh detached worktree plus every block, compared file by file with an expected diff. It finds a broken plan in
  seconds, before anyone compiles anything.
- **New worktrees, never reset ones.** `newtree` refuses a name that exists. Resetting, cleaning or stashing a tree
  another agent may be using is how work gets lost.
- **An empty snapshot is an error, not a pass.** A wrong `--expect` path must not read as "0 mismatches".
- **The look-back for "Create `x`" stops at the previous block.** Otherwise a prose block straight after a create
  block is taken for a second create.

## injection

Two functions over one piece of text, after zero-width and soft characters are removed: `secret_kind` names the
kind of secret-shaped text it found (never the text), and `reads_as_instruction` reports instruction-shaped text.
It is a screen for common phrasings, not a defence: paraphrase, other languages and encodings get through. Whatever
reads recalled text must still treat it as data.

## memory

One SQLite file. A memory has a kind, a title of at most 12 words, a summary of at most 65, its evidence and a trust
level (provisional, verified, canonical, superseded, quarantined).

Decisions:

- **Refuse secrets, quarantine instructions.** A secret is refused and logged by kind only. Instruction-shaped text
  is stored as quarantined so it can be counted and inspected, and is never recalled.
- **Evidence for claims.** Invariants, pitfalls and lessons need a file:line, a commit or a test.
- **Canonical is a person's call.** Agents write provisional or verified.
- **Recall is small and says what it is.** At most 22 lines and 1,000 characters, and its second line says it is
  advice that project rules override. A memory and its tension or dependency line are kept or dropped together.
- **Score:** trust multiplier x (bm25 + 0.3 x salience). Provisional memories halve in salience every 30 days
  without a recall; verified and canonical ones do not fade.

## profile_gate

A profile is JSON describing a narrow helper agent or an upgrade to an existing role: its tools, its mentors (the
existing roles it derives from), word budgets, its domains, a cited reason it should exist, and a locked principles
block with a pinned hash.

Decisions:

- **A tool ceiling.** Read-only by default. A builder ceiling (edit, write, shell) must be asked for by a mentor,
  and the tools may never be wider than any mentor's. No tool that could start another agent or reach the network.
- **Words are checked too.** Secret-shaped text, instruction-shaped text and text that reaches for agent settings,
  this gate or a background process are refused.
- **Assessment repeats everything.** It re-runs every submission check as well as the hash check, and writes the
  rendered agent file as a `.txt` preview inside the gate's folder.
- **Promotion is a person's act.** It needs an explicit confirmation flag, an interactive terminal outside any agent
  session and a yes to a question. It writes one new file with exclusive-create mode and never overwrites.

## What review caught

The first four were found by read-only adversarial reviews of the private versions and fixed there before this
rewrite. The fifth was found by the same kind of review of this repository before it was published. Each fix has a
test here. The test for the second needs the right to create symbolic links, so it is skipped on Windows accounts
without it; the Linux CI job runs it.

1. **Two submissions could both take the last pending slot.** Counting the slots and writing the record now happen
   under one lock file. The test starts two threads at once with one slot left and slows the count so that, without
   the lock, both would see the slot free; exactly one must succeed.
2. **A link at the gate's folder could aim a write elsewhere.** A symbolic link or junction there could send a
   record or a preview into the agents folder. The gate now writes only into plain folders that resolve inside its
   own root.
3. **A recomputed hash passed the "unchanged" check.** Someone who edited a pending record and rewrote its hash got
   through. Assessment now re-runs every submission check, not only the hash.
4. **"Ignore all of the previous rules" got past the screen.** The pattern expected the verb and its object next to
   each other. It now allows a few words between them.
5. **A line break in a description could rewrite the agent file's header.** The description is rendered inside the
   header, so a line break followed by `tools: ...` would have added a key. Single-line fields now refuse line breaks
   of every kind and the `---` delimiter.
