# M4 — Focus by UUID via AppleScript

**Goal:** Pressing a key focuses the exact Ghostty surface for that session.

**Depends on:** M2 (daemon reads presses), M3 (hook reports each session's `tty`).

**Authoritative reference:** [`../ghostty-focus-plan.md`](../ghostty-focus-plan.md)
— this milestone implements its **Tier 0** (no fork, stock Ghostty ≥ 1.3.0).

## Why this replaced "tmux jump"
Stock Ghostty already ships identity-based focus over AppleScript. `focus
terminal id "<uuid>"` raises the window, selects the tab, focuses the split, and
activates the app — one authoritative operation. That sidesteps the "which
Ghostty tab is active" ambiguity that made the tmux approach leaky, and needs no
fork. tmux is now only relevant for session survival across restarts (optional).

## Tasks
- [ ] **Resolve session → UUID.** On the `SessionStart` message (which carries
      `tty`), the daemon runs AppleScript to find the surface and cache its UUID:
      ```applescript
      tell application "Ghostty" to get id of (first terminal whose tty is "/dev/ttys004")
      ```
      Store `session_id → uuid`. (Use `osascript`, or JXA `osascript -l
      JavaScript` for cleaner data out.)
- [ ] **Focus on keypress.** Key-press callback → session → uuid → run:
      ```applescript
      tell application "Ghostty" to focus terminal id "<uuid>"
      ```
- [ ] **Handle a stale/dead surface.** A closed surface makes `focus` error
      ("Terminal surface is no longer available") and `whose` queries stop
      returning it. On that error, drop the key / mark the session ended.
- [ ] **Handle edge cases** (see plan §6): pressing a blank key (no-op);
      minimized window (may need the deminiaturize workaround — see below);
      surface on another Space / fullscreen (verify empirically).
- [ ] Address Ghostty **by name** normally; address by absolute path only when
      testing a from-source dev build (see plan §7).

## Done when
- Pressing a session's key brings that exact Claude session to the foreground in
  one press, from anywhere — regardless of tab order or which window was front.

## Notes / gotchas (from ghostty-focus-plan.md)
- **TCC / Automation:** the first Apple event triggers a one-time Automation
  prompt attributed to whichever process sends it (the Python daemon, or a
  helper). Decide that owner deliberately — it's where the grant lands. A bundled
  native helper needs `NSAppleEventsUsageDescription`.
- **`macos-applescript`** must not be disabled in Ghostty config (default on).
- **Minimized window** likely isn't reliably raised by focus today — the plan
  flags a `deminiaturize` fix (its patch B1) as a candidate upstream
  contribution; worth an early manual test against stock behavior.
- **Latency:** Apple events are ~tens of ms/call — fine for keypress focus. Do
  **not** poll focus state through AppleScript faster than ~1–2 Hz (see M5 /
  plan Tier 2 for a push-events alternative).
- **App restart:** UUIDs die with the process — re-resolve every known session
  by `tty` on the daemon's next `SessionStart` messages (or on reconnect).

## Optional stretch (do NOT block M4 on this)
- **Tier 1 fork** — a Swift-only Ghostty patch adding a caller-supplied `tag`
  property so you can `focus (first terminal whose tag is "…")` and skip the
  UUID bookkeeping. Fully scoped in `ghostty-focus-plan.md` §3–4; genuinely
  upstreamable. Only pursue if the tty/UUID correlation proves annoying in real
  use.