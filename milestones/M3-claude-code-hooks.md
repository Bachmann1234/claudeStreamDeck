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
- [ ] Write one small reporter script (e.g. `hooks/report.py` or a shell script)
      that:
  - [ ] Reads the event JSON from stdin.
  - [ ] Extracts `session_id`, cwd (for a human label), and the session's
        **`tty`** (see below).
  - [ ] Maps the triggering event → state.
  - [ ] Writes one JSON line to the daemon's unix socket. Must be fast and
        **never block Claude** — fire-and-forget, short timeout, swallow errors.
- [ ] Determine the `tty` from inside the hook. This is the correlation key the
      daemon uses to resolve the session's Ghostty surface UUID over AppleScript
      (`first terminal whose tty is "/dev/ttysNNN"` — see M4 and
      [`../ghostty-focus-plan.md`](../ghostty-focus-plan.md) §3). Get it via:
  - `tty` command, or `ps -o tty= -p $PPID`, normalized to `/dev/ttysNNN`.
  - Report it on **`SessionStart`** (so the daemon can resolve the UUID once and
    cache `session_id → uuid`); other events only need `session_id` + `state`.
- [ ] Add hook config to settings.json mapping each event → the reporter script.
- [ ] Verify: start a session, watch keys change as you prompt / get asked /
      finish / exit.

## Done when
- Real Claude Code activity drives the deck live: a fresh session lights a key,
  it goes blue while working, yellow when it needs you, green when done, blank
  on exit.

## Gotchas
- Hooks run for *every* session — the reporter must be idempotent and cheap.
- Don't let a dead daemon break Claude: connect with a tiny timeout, ignore
  failures.
- "Working" cleanly starting is fuzzy — approximate with `UserPromptSubmit` and
  clear on `Stop`/`Notification`.
