# M2 — Daemon skeleton (`streamdeckd`)

**Goal:** A long-running Python daemon that owns the USB connection, tracks
session→key assignments, and repaints keys on demand.

**Depends on:** M1 (hardware confirmed working).

## Tasks
- [x] Process that opens the deck once and stays running (the deck can only be
      held by one process — the daemon owns it, hooks never touch USB).
      *Done as `streamdeckd` (`streamdeckd/daemon.py`): single-owner (refuses a
      second instance on a live socket), long-running. The USB half is behind
      the `Renderer` interface — a hardware `StreamDeckRenderer` drops in at M1;
      headless runs use `VirtualDeck`.*
- [x] A local transport for receiving state updates. Start with a **unix domain
      socket** at a fixed path (e.g. `~/.claudeStreamDeck/streamdeckd.sock`).
      Message shape (JSON, one per line):
      ```json
      {"session_id": "abc123", "tty": "/dev/ttys004", "state": "working", "label": "repo-x"}
      ```
      (`tty` only needs to arrive on `SessionStart`; the daemon resolves it to a
      Ghostty surface UUID once and caches it — see M4.)
      *Done (`streamdeckd/protocol.py`, threaded `AF_UNIX` server in
      `daemon.py`). Message is a superset: also carries `event` and a
      hook-resolved `uuid` (correlation moved into the hook — see
      `docs/correlation-rationale.md`, since 1.3.1 has no `tty`).*
- [x] Session→key allocation (`streamdeckd/state.py :: SessionModel`):
  - [x] Assign the next free key on `SessionStart` / first message for a session.
  - [x] Release the key on `SessionEnd`.
  - [x] Keep a stable mapping so a session doesn't jump keys mid-life.
- [x] Render function: state → color/label → key image.
      *`VirtualDeck` renders a PNG per key (Pillow) plus a JSON snapshot; M1's
      hardware renderer will format the same `KeyAppearance` frames with
      PILHelper.*
- [x] Graceful shutdown: blank all keys, reset brightness, close device.
      *SIGINT/SIGTERM → blank frame, `renderer.close()`, socket unlinked.
      Brightness reset is a hardware-renderer concern (M1).*

## Done when
- Sending JSON lines to the socket paints the right keys, and lifecycle
  messages claim/release keys correctly. (Test with a `nc`/`socat` one-liner or
  a tiny `send.py` helper.)
  *✅ Met. Covered by `tests/test_daemon.py` (incl. a real-socket end-to-end
  test) and the manual `nc -U` recipe in `docs/setup.md §4`.*

## Design notes
- Keep the state model dead simple: an in-memory `dict[session_id -> Session]`
  where `Session = {key_index, tty, surface_uuid, state, label}`.
- Repaint is idempotent — always render from current state, no incremental diffs.
