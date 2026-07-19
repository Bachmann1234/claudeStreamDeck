# M5 — Polish: icons, animation, overflow

**Goal:** Make it pleasant and robust for daily use.

**Depends on:** M1–M4 (working end-to-end).

## Tasks
- [ ] **Visual design** of key states: clear icons + color, readable labels
      (repo name / short session label). Legible at the deck's small key size.
- [ ] **Animation** for "working" (spinner/pulse) and "needs you" (attention
      pulse). Daemon needs a render tick loop for animated keys only.
- [ ] **Overflow handling** for >15 concurrent sessions:
  - [ ] Decide: paging (a key toggles page), or LRU-evict finished sessions'
        keys, or reserve key 14 as an "overflow/more" indicator.
- [ ] **Reserved keys / controls** (optional): e.g. a key to blank/reset, a key
      to cycle pages, a "most recent attention" jump key.
- [ ] **Resilience:**
  - [ ] Daemon auto-recovers if the deck is unplugged/replugged.
  - [ ] Reconcile state on daemon restart (sessions may already be running —
        rebuild from a state file or just start fresh and repopulate on next
        hook events).
- [ ] **Run as a service:** launchd plist (macOS) so `streamdeckd` starts on
      login and restarts on crash.

## Done when
- It looks good, survives unplug/replug and daemon restarts, handles more than
  15 sessions sanely, and starts automatically on login.

## Nice-to-haves (backlog)
- Sound/notification when a session needs attention.
- Per-key brightness to dim idle sessions.
- Config file for colors, terminal app name, socket path, overflow strategy.
- A "focus the one session that needs you" master key.
