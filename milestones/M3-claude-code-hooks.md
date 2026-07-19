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
- [x] Determine the `tty` from inside the hook — reported best-effort via
      `os.ttyname(/dev/tty)`. **But 1.3.1 exposes no `tty`, so tty is not the
      correlation key.** Instead the hook resolves the surface **UUID itself** on
      `SessionStart` via an OSC title sentinel and reports `(session_id, uuid)`;
      the daemon caches it directly. Full evaluation of tty vs. cwd vs.
      manager-spawns vs. sentinel in
      [`../docs/correlation-rationale.md`](../docs/correlation-rationale.md).
  - [x] Report it on **`SessionStart`**; other events send only
        `session_id` + `state`.
- [x] Add hook config to settings.json mapping each event → the reporter script.
      *Provided as [`../hooks/settings.snippet.json`](../hooks/settings.snippet.json);
      wiring documented in `docs/setup.md §5`.*
- [ ] Verify: start a session, watch keys change as you prompt / get asked /
      finish / exit. *Not run live in this session (the destructive-test hazard
      in CLAUDE.md — spawning/closing real Ghostty sessions is risky, and this
      was an unattended run). Verified instead via the full unit suite + a
      real-socket integration test + the manual `nc -U` recipe. Live smoke test
      is the one remaining human step — see `docs/setup.md §5`.*

## Done when
- Real Claude Code activity drives the deck live: a fresh session lights a key,
  it goes blue while working, yellow when it needs you, green when done, blank
  on exit. *Wiring + daemon proven headless; the live confirmation is the open
  checkbox above.*

## Gotchas
- Hooks run for *every* session — the reporter must be idempotent and cheap.
- Don't let a dead daemon break Claude: connect with a tiny timeout, ignore
  failures.
- "Working" cleanly starting is fuzzy — approximate with `UserPromptSubmit` and
  clear on `Stop`/`Notification`.
