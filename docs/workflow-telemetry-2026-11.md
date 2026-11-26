# A month of agent work, counted

27 October to 26 November 2026. Claude Code's `/insights` command reads the session logs on my PC and sums them
up; I ran it on 26 November 2026. This note keeps the counts I can stand behind, says what each one means, and lists
what I left out.

The counts cover every Claude Code session on that PC. Most were on private projects: a deterministic Rust game
engine, a local AI workstation, a Tauri desktop dashboard and the tools around them. Some were website and PC-setup
work. I have not re-derived the numbers from the raw logs.

## The counts

The figures are as the report gives them. It ties the commit and agent counts to the 107 sessions it analysed.

| What | Count | What it means |
|---|---|---|
| Days with sessions | 27 of 31 | |
| Sessions | 212, of which the report analysed 107 | |
| Commits made in those sessions | 382 | Agents wrote most of them; I decide what merges |
| Subagents started | 350 | A lead session handing one task to a fresh agent |
| Messages between agents | 320 | Mostly a lead session and its builders or reviewers |
| Sessions running in parallel | 66 sessions, in 85 overlaps | 37% of my messages went out while another session was running |
| File activity, by language | Python 1,691, Markdown 1,312, Rust 775, TypeScript 230, JSON 53, HTML 49 | The report's language counts, not lines of code. The Markdown is mostly my specs, plans and review records |

## What I left out, and why

- **Lines changed.** The report shows 208,939 added. Agents wrote nearly all of them, and many are specs, tests and
  generated files, so the figure says little about my own work.
- **Hours.** Parallel sessions overlap, so summed session time counts the same hour more than once.
- **Satisfaction and outcome scores.** A model estimates these from the transcripts. They are not measurements.
- **Anything personal**, and anything about third-party accounts, my network or unreleased work.

## Caught before merge

On 24 November 2026 two read-only reviewer agents ran in parallel, one on an interface and one on the security of
the shell guard and vault that sit in front of my private secrets store, the same idea as the public
[mcp-secret-broker](https://github.com/harveyjenkins03-coder/mcp-secret-broker). Among the security findings:

- the guard missed a flag glued to its value with `=`, and a module name built by joining two strings;
- a 30-second stale-lock break could fire while a confirmation dialog was still open inside the lock;
- the reply censor failed open if its sealed store vanished.

Each code fix started as a test that was seen failing first: 23 new cases on the Python side.

## Friction that became guardrails

Most of the friction in the report came from agents breaking files through the shell on Windows. I changed the
setup on 26 November 2026, the day the report came out.

| What kept going wrong | What changed |
|---|---|
| Heredocs and PowerShell rewrites mangled source files: a `\r` in a path became a carriage return, CRLF crept into LF files, and bulk regex edits missed lines that rustfmt had reflowed | A hook refuses shell-written edits to source, config and docs, so agents have to use the Edit and Write tools. A second hook flags mixed line endings after each edit. 22 tests pass |
| Long runs stalled at the last step because the app under test was still open or a third-party credential had expired | A preflight check before long runs: running processes, one harmless auth call per external service and a scan of the brief for unfilled placeholders, ending in GO or BLOCKED |
| Sessions that ran out of context mid-gate were slow to pick up again | A safe-stop checkpoint at each milestone, with test counts and the exact command to resume |
| Subagents ignored rules the lead session followed | Each builder brief now names its write paths and its test bar, and a separate reviewer reads the diff before merge |
| An agent planned to copy decisions into a second file instead of citing the original | Agents cite the canonical record by path and line and never copy it |

## What I am building next

None of this runs yet as described (26 November 2026). I already use parallel read-only reviewers; the rest is new.
The ideas come from the report's suggestions and fit how I already work.

- **Unattended sprints against red tests.** A frozen task queue worked overnight in headless mode: failing tests
  committed first, then the code that passes them, on a staging branch the run can never push or merge. Anything
  that needs my ruling goes into a decision queue, and the run ends with a resume note.
- **A review swarm before each merge.** Read-only reviewers run in parallel, each on one concern such as guard
  bypasses, concurrency or claims in the docs. Every finding has to arrive as a failing test, and a fixer agent makes
  it pass without weakening it.
- **Write boundaries between parallel sessions.** Each session declares the paths it may write. A hook logs every
  write and refuses one that crosses another session's boundary.

## How this was made

Claude drafted this note from the report and the review record. I checked each number against both and cut anything
personal or private before publishing.
