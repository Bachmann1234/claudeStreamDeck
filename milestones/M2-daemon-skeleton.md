# M2 — Daemon skeleton (`streamdeckd`)

**Goal:** A long-running Python daemon that owns the USB connection, tracks
session→key assignments, and repaints keys on demand.

**Depends on:** M1 (hardware confirmed working).

## Tasks
- [ ] Process that opens the deck once and stays running (the deck can only be
      held by one process — the daemon owns it, hooks never touch USB).
- [ ] A local transport for receiving state updates. Start with a **unix domain
      socket** at a fixed path (e.g. `~/.claudeStreamDeck/streamdeckd.sock`).
      Message shape (JSON, one per line):
      ```json
      {"session_id": "abc123", "tty": "/dev/ttys004", "state": "working", "label": "repo-x"}
      ```
      (`tty` only needs to arrive on `SessionStart`; the daemon resolves it to a
      Ghostty surface UUID once and caches it — see M4.)
- [ ] Session→key allocation:
  - [ ] Assign the next free key on `SessionStart` / first message for a session.
  - [ ] Release the key on `SessionEnd`.
  - [ ] Keep a stable mapping so a session doesn't jump keys mid-life.
- [ ] Render function: state → color/label → key image (reuse M1's PILHelper code).
- [ ] Graceful shutdown: blank all keys, reset brightness, close device.

## Done when
- Sending JSON lines to the socket paints the right keys, and lifecycle
  messages claim/release keys correctly. (Test with a `nc`/`socat` one-liner or
  a tiny `send.py` helper.)

## Design notes
- Keep the state model dead simple: an in-memory `dict[session_id -> Session]`
  where `Session = {key_index, tty, surface_uuid, state, label}`.
- Repaint is idempotent — always render from current state, no incremental diffs.
