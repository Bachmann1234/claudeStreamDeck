# claudeStreamDeck — Agent Guide

Turn an Elgato Stream Deck into a live Claude Code session manager: each session
claims a key; pressing it focuses that session's exact Ghostty surface. See
`README.md` for the full picture.

## Project layout
- This project lives here (`~/code/claudeStreamDeck`). **Do not touch
  `~/code/ghostty`** — that's the terminal's source repo, unrelated to this work.
- `gsm/` — the Tier 0 manager core (built & tested): `applescript.py` (Ghostty
  Apple-event bridge), `registry.py` (persistent tag→session store),
  `manager.py` (spawn/focus/adopt/status), `cli.py`. Importable as `gsm`,
  runnable as `python -m gsm` or the `gsm` console script.
- `milestones/` — the roadmap (M1–M5); treat these as the spec.
- `docs/tier0-validation-findings.md` — empirical findings about stock Ghostty.
  **Read it before doing Ghostty/AppleScript work.**

## Setup & test
```
cd ~/code/claudeStreamDeck
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest
```
Always work inside the `.venv`. Prefer unit tests with a fake Ghostty over
live-app testing.

## Hard constraints
- **Tier 0 only — no fork.** Do not fork, clone, or build Ghostty. The Tier 1
  Swift fork (a caller-supplied `tag` property) is an explicitly deferred stretch
  track; do not start it unless a prompt says so. Everything works against stock
  Ghostty ≥ 1.3.0 via AppleScript.
- **Ghostty 1.3.1 does NOT expose `tty` or `pid`** over AppleScript — only `id`,
  `name` (title), and `working directory`. Any session↔surface correlation must
  go through those. (The plan assumed tty; that only exists on unreleased
  `main`. See the findings doc.)
- Spawned sessions capture their surface UUID directly from `new window`; no
  correlation needed for those. Correlation only matters for *adopting* sessions
  the manager didn't spawn.

## ⚠️ Destructive-test hazard (learned the hard way)
- `close` on a Ghostty surface with a running process raises a **blocking
  app-modal "Close Window?" alert** that freezes ALL Apple events until
  dismissed. It is an alert window, not an AXSheet, so sheet-detection misses it.
- Spawned windows can merge into existing windows as **native macOS tabs**; a
  window-level close then terminates every tab in that shared window.
- Therefore: when testing against live Ghostty, prefer spawning throwaway
  surfaces that self-exit, **never issue window-level closes**, and never
  spawn/close near the user's real sessions. The `gsm` manager's own hot path
  never closes surfaces (focus only prunes the registry mapping) — keep it that
  way. When unsure whether an action could disrupt open sessions, ask first.

## Conventions
- Python, standard library first; keep dependencies minimal.
- Match the existing style in `gsm/` (dataclasses, small focused modules,
  docstrings that explain *why*).
- Never create a GitHub issue or PR.
