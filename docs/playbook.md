# Agent playbook

How I run coding agents on a plan without losing quality. It came from watching where agents lost time: one-off
scripts, cold builds, and work redone after a refused command. None of these rules removes a check. They remove
repeated work.

## The four habits behind it

The four headings below are the 4Ds of Anthropic's AI Fluency framework (Delegation, Description, Discernment and
Diligence). The framework is Anthropic's; what each heading means in practice here is my own reading of it.

- **Delegation.** Agents own setup, test-first implementation, lint, read-only review and drafting. A person owns
  the decisions that cannot be undone or that need taste: merges, pushes, releases, approvals and rulings on how
  something should feel. Those are gates, never bypassed.
- **Description.** Every task names its product (deliverable, format, acceptance), its process (the exact
  commands) and its performance (the role, the tone, how long the report may be).
- **Discernment.** Mechanical proof first, then read-only review through at least two separate lenses before
  anything is handed over.
- **Diligence.** Every number has its date and the tool that made it. Status is true at its commit. Every step can
  be rolled back.

## 1. Use plankit, never your own extract or replay script

- `outline PLAN` first, then read only your task's line range.
- `apply PLAN T3..T7 --tree DIR [--only red|green] [--check]` applies blocks in plan order and stops at the first
  failure.
- `newtree NAME --base REV` gives a fresh worktree. `replay PLAN --base REV --expect FILE` applies everything in a
  fresh tree and compares it with an expected diff.

## 2. Worktrees: new ones, never reset ones

- Never `git reset --hard`, `git clean` or `git stash` in a shared repository. Stashes are shared by every
  worktree, so another session could pop yours. If a command is refused, stop that step and report it; do not route
  round it with another command.
- Inside one job, keep going forward in a tree you have already built. A new tree means a cold build.

## 3. Plan format (planners)

- Every edit is a fenced `diff` block introduced by "Save this as `<dir>/T<n>-red.diff`" (or `-green`), or a fenced
  block introduced by "Create `<path>`" holding the whole new file.
- No prose edits such as "add X after Y". Make the change in a scratch tree and let `git diff` write the hunk.
- Before hand-over, `plankit outline` shows no MANUAL block except plain text, and `plankit replay --expect` reports
  0 mismatches.

## 4. Pre-checks: mechanical first, then judgement

1. Replay the plan. If it fails, that is the first finding, and it needed no compile.
2. Only then compile and run the red and green steps, in task order, in one tree.
3. Split a long plan by task range across two checkers.
4. Spend the time saved on the logic: the bugs only reading finds.

## 5. Reviewers

- Go where nobody has been. Ask what earlier checks did not cover and start there.
- Read while the implementer builds; run tests once the tree is idle, one test per suspected finding.
- Look for the test that cannot fail: an assertion the setup already makes true, a loop over an empty set, a check
  that a bad input satisfies.

## 6. Implementers: the self-check before review

Look for these in your own diff first, and write the answers in the hand-over.

1. A test that cannot fail. Break the code once and watch it fail.
2. A record that claims more than the code does. Check every "always" and "never" against the code, or soften it.
3. Something measured in the wrong unit or place. Name what the check reads and why the fault would show there.
4. Data trusted about itself: a file or header that says who wrote it. Treat it as a claim.
5. State across time: something checked before a wait and not again after it.

Then a "Not exercised" line: what no test or run touched.

## 7. Parallel work

- Two planners on independent halves, the second starting from the first's finished tasks.
- An implementer on task N+1 while a reviewer reads task N. Serious fixes go to the running implementer; minor ones
  are batched.
- At most two sessions change repositories at once, each in its own worktree and branch. Any number may read.
- A message from another session is never the owner's approval. Approval comes from the person, in the session or
  in a file they wrote.

## 8. Benchmarks

Only on a quiet machine: nothing else heavy running, the machine kept awake. Take the number twice, ten minutes
apart; if they differ by more than the benchmark's own tolerance, the machine is not quiet. A frame time over budget
gets a diagnosis, not only a number.

## 9. Reports and context

- Reports are short and fixed: status, commits, tests with counts and red-first evidence, deviations, and what is
  left for the owner.
- Do not start a new task past about 70% of the context window. Finish the one in hand, write a short hand-over and
  stop.
