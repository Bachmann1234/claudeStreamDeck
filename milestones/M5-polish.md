# M5 — Polish: icons, animation, overflow

**Goal:** Make it pleasant and robust for daily use.

**Depends on:** M1–M4 (working end-to-end).

## Tasks
- [ ] **Visual design** of key states: clear icons + color, readable labels
      (repo name / short session label). Legible at the deck's small key size.
- [ ] **Animation** for "working" (spinner/pulse) and "needs you" (attention
      pulse). Daemon needs a render tick loop for animated keys only.
- [x] **Overflow handling** for >15 concurrent sessions:
  - [x] Decide: paging (a key toggles page), or LRU-evict finished sessions'
        keys, or reserve key 14 as an "overflow/more" indicator.
        *Chosen: **priority-based LRU eviction** (`SessionModel`,
        `evict_finished_when_full`, default on). When the deck is full, a
        new/urgent session evicts the least-recently-active *lower-priority*
        keyed session (ATTENTION > WORKING > STARTING > DONE) and parks it;
        a freed key promotes the best-ranked parked session back. Paging is
        still a possible future addition for very high session counts.*
- [ ] **Reserved keys / controls** (optional): e.g. a key to blank/reset, a key
      to cycle pages, a "most recent attention" jump key.
- [ ] **Resilience:**
  - [ ] Daemon auto-recovers if the deck is unplugged/replugged.
  - [ ] Reconcile state on daemon restart (sessions may already be running —
        rebuild from a state file or just start fresh and repopulate on next
        hook events).
- [x] **Run as a service:** launchd plist (macOS) so `streamdeckd` starts on
      login and restarts on crash.
      *Template at `service/com.claudestreamdeck.streamdeckd.plist`
      (RunAtLoad + KeepAlive + throttle); install steps in `docs/setup.md §9`.*

## Done when
- It looks good, survives unplug/replug and daemon restarts, handles more than
  15 sessions sanely, and starts automatically on login.

## Nice-to-haves (backlog)
- Sound/notification when a session needs attention.
- Per-key brightness to dim idle sessions.
- Config file for colors, terminal app name, socket path, overflow strategy.
- A "focus the one session that needs you" master key.
