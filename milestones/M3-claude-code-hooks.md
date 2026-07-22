# M3 — Claude Code hooks

**Goal:** Wire Claude Code's hook system to report each session's state to the
daemon.

**Depends on:** M2 (daemon accepts state messages).

## Background
Claude Code fires hooks on lifecycle events. Each hook runs a shell command and
receives event JSON on stdin (including the session id and cwd). Configured in
`~/.claude/settings.json` (or project `.claude/settings.json`) under `hooks`.

Relevant events:
- `SessionStart` — claim a key.
- `UserPromptSubmit` / `PreToolUse` — working.
- `Notification` — needs attention (question / permission prompt).
- `Stop` — response finished.
- `SessionEnd` — release the key.

## Tasks
- [x] Write one small reporter script (`streamdeckd/hook.py`, exposed as the
      `claudestreamdeck-hook` console script — one script wired to every event)
      that:
  - [x] Reads the event JSON from stdin.
  - [x] Extracts `session_id`, cwd (for a human label), and the session's
        **`tty`** (see below).
  - [x] Maps the triggering event → state (`hook_event_name` → state).
  - [x] Writes one JSON line to the daemon's unix socket. Must be fast and
        **never block Claude** — fire-and-forget, short timeout, swallow errors.
        *`send_line` uses a 0.25 s timeout and returns `False` on any error;
        `main()` always exits 0. Hooks are also wired `async` in settings.*
- [x] Determine the correlation key from inside the hook. **1.3.1 exposes no
      `tty`, and live testing found hooks have no controlling terminal
      (`/dev/tty` fails), so neither tty-matching nor the OSC title-sentinel
      works.** The hook instead resolves the surface **UUID itself** on
      `SessionStart` via read-only `osascript` — the **focused front surface,
      cross-checked against `cwd`** — and reports `(session_id, uuid)`; the
      daemon caches it. Full evaluation (tty vs. sentinel vs. cwd vs.
      focused+cwd) in
      [`../docs/correlation-rationale.md`](../docs/correlation-rationale.md).
  - [x] Resolve on **`SessionStart`** (wired synchronously for focus timing);
        other events send only `session_id` + `state`.
- [x] Add hook config to settings.json mapping each event → the reporter script.
      *Provided as [`../hooks/settings.snippet.json`](../hooks/settings.snippet.json);
      wiring documented in `docs/setup.md §5`.*
- [x] Verify: start a session, watch keys change as you prompt / get asked /
      finish / exit. *Verified live 2026-07-19 against real Claude Code + Ghostty
      1.3.1: a fresh session lit a key (dim → blue → green), the UUID resolved
      and bound in `registry.json`, and a `{"press":0}` focused the exact
      surface (confirmed it raised the correct same-cwd sibling, not the wrong
      window). This live run is what surfaced the `/dev/tty` constraint and drove
      the sentinel → focused+cwd pivot.*

## Done when
- Real Claude Code activity drives the deck live: a fresh session lights a key,
  it goes blue while working, yellow when it needs you, green when done, blank
  on exit. *✅ Met — verified live (see above).*

## Gotchas
- Hooks run for *every* session — the reporter must be idempotent and cheap.
- Don't let a dead daemon break Claude: connect with a tiny timeout, ignore
  failures.
- "Working" cleanly starting is fuzzy — approximate with `UserPromptSubmit` and
  clear on `Stop`/`Notification`.
