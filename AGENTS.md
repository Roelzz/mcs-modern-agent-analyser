# AGENTS.md — agent working rules for this repo

Project-scoped instructions for any AI agent (Copilot CLI, etc.) working here.
These reinforce Roel's global rules and record decisions specific to this project.

## Git workflow (NON-NEGOTIABLE)

- **Never do feature work directly on `master`/`main`.** Every change set goes on a
  `feature/…`, `fix/…`, or `docs/…` branch.
- **Prefer git worktrees for isolation** (global rule #10). In the Copilot app, the
  native equivalent is a dedicated **worktree session** (`create_session`) — use that
  for new feature work instead of editing in the in-place `master` session.
- **In-place session caveat:** if the app hands you an in-place session on `master`,
  do NOT silently work on it. Stop and either (a) bootstrap a branch/worktree, or
  (b) flag the conflict to Roel and get a decision. Do not just proceed on `master`.
- **Worktrees need a commit.** A fresh repo with an unborn HEAD (zero commits) cannot
  have a worktree. First action on a new repo = an initial scaffold commit on `master`,
  then branch/worktree from there.
- **Commits only when asked.** Don't commit/push on your own initiative. Use conventional
  commits, imperative mood, atomic. Run `git diff` after changes.

## Bootstrapping state (2026-06)

This repo was built in an in-place `master` session and currently has **zero commits**
(everything untracked). Before any further feature work, establish git history:
`master` = scaffold/baseline, then feature branches/worktrees on top.

## Tooling

- Python 3.12, **UV** (`uv sync` / `uv run`), **Ruff**, **Pytest**. Run `.venv/bin/python`
  (system python lacks `pyyaml`).
- Reflex app: dev = ports 3000/8000, prod = single port **2009** (do not change ports).
- Constraints honored so far: heuristics-only (no LLM), zero new Python deps beyond
  pyyaml. Keep it that way unless Roel approves otherwise.
