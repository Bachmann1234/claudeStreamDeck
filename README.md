# claudeStreamDeck

Turn an Elgato Stream Deck into a live Claude Code session manager.

Each running Claude Code session claims a key on the deck. The key's color/icon
reflects what that session is doing (working, waiting on you, done). Pressing a
key jumps your terminal straight to that session by focusing its exact Ghostty
surface.

> **Direction note (2026-07-19):** research into a Ghostty fork turned up that
> **stock Ghostty ≥ 1.3.0 already exposes identity-based focus** over AppleScript
> (`focus terminal id "<uuid>"` raises the window, selects the tab, focuses the
> split, activates the app). So this is a **no-fork project** — no tmux tab
> ambiguity to fight, no custom terminal. The full analysis is in
> [`ghostty-focus-plan.md`](./ghostty-focus-plan.md); the optional Swift-only
> fork that adds caller-supplied tags is a deferred stretch track, not the
> mainline.

## Hardware
- **Elgato Stream Deck, model 20GAA9902** — 15-key (3×5) standard board. Each key
  is a programmable LCD with a physical press.
- The device is a plain USB HID gadget. We drive it directly, bypassing Elgato's
  software, using [python-elgato-streamdeck](https://github.com/abcminiuser/python-elgato-streamdeck).

## How it fits together

```
┌─────────────────┐     writes state      ┌──────────────────┐
│ Claude Code      │  ──────────────────>  │  streamdeckd     │
│ hooks (per sess) │   (unix socket /      │  (python daemon) │
│  Notification    │    JSON in watched    │  - owns USB HID  │
│  Stop            │    dir, keyed by      │  - session→key   │
│  PreToolUse      │    session id +       │  - session→uuid  │
│  SessionStart/End│    tty)               │  - paints keys   │
└─────────────────┘                        │  - on keypress:  │
                                           │    AppleScript   │
                                           │    focus by uuid │
                                           └──────────────────┘
```

- **streamdeckd** — a long-running Python daemon that owns the USB connection,
  allocates a key per session, paints keys, and on a key press focuses that
  session's Ghostty surface via AppleScript.
- **Hooks** — small scripts wired into Claude Code's hook system. They fire on
  session lifecycle events and report `(session_id, tty, state)` to the daemon.
- **tty → surface correlation** — the daemon resolves a session's reported `tty`
  to a stable Ghostty surface **UUID** via AppleScript
  (`first terminal whose tty is "/dev/ttysNNN"`), then stores `session → uuid`.
  A keypress runs `focus terminal id "<uuid>"`. See
  [`ghostty-focus-plan.md`](./ghostty-focus-plan.md) §3 (Tier 0).

## State → key mapping

| Claude Code hook            | Meaning                        | Key appearance     |
|-----------------------------|--------------------------------|--------------------|
| `SessionStart`              | claim a free key               | dim / labeled      |
| `UserPromptSubmit`, `PreToolUse` | working                   | blue (or animated) |
| `Notification`              | needs you (question/permission)| pulsing yellow     |
| `Stop`                      | response finished / done       | green              |
| `SessionEnd`                | release the key                | blank              |

**Note:** `Notification` (needs attention) and `Stop` (done) are the reliable
signals. There's no perfectly clean "started thinking" event, so "working" is
inferred from `UserPromptSubmit` and cleared by `Stop`/`Notification`.

## Environment
- Terminal is **Ghostty ≥ 1.3.0** on macOS. Focus is done over AppleScript
  against the surface UUID — no tmux switching, no Ghostty-tab ambiguity.
  Requires `macos-applescript` enabled in Ghostty config (default `true`) and a
  one-time Automation (TCC) grant for whatever process sends the Apple events.
- **tmux is now optional** — only relevant for session *survival* across a
  Ghostty restart (a surface UUID dies with the process). If that matters, run
  sessions under tmux-in-Ghostty and re-resolve UUIDs by tty after a restart.
  Not needed for the core focus feature.
- Development on macOS (Darwin). Use a virtualenv for all Python work.

## Milestones
See [`milestones/`](./milestones/). Build them in order — each is a de-risking
step toward the next.

1. [M1 — Hardware smoke test](./milestones/M1-hardware-smoke-test.md)
2. [M2 — Daemon skeleton](./milestones/M2-daemon-skeleton.md)
3. [M3 — Claude Code hooks](./milestones/M3-claude-code-hooks.md)
4. [M4 — Focus by UUID via AppleScript](./milestones/M4-focus-by-uuid.md)
5. [M5 — Polish: icons, animation, overflow](./milestones/M5-polish.md)

## Related docs
- [`ghostty-focus-plan.md`](./ghostty-focus-plan.md) — deep-dive research into
  Ghostty's focus API (the authoritative reference for M4). Includes the
  optional Tier-1 fork design and build-from-source notes.
- [`milestones/ghostty-fork-plan-prompt.md`](./milestones/ghostty-fork-plan-prompt.md)
  — the prompt that produced the plan above. Kept for provenance; the fork it
  scopes is now an **optional stretch track**, not required for the core tool.

## Open questions / decisions to revisit
- ~~Transport between hooks and daemon~~ **Decided: unix socket** at
  `~/.claudeStreamDeck/streamdeckd.sock`, newline-delimited JSON. Built in M2
  (`streamdeckd/daemon.py`).
- ~~Which process sends the Apple events~~ **Decided: split.** The **hook**
  resolves its own surface UUID once on `SessionStart` (title-sentinel trick —
  see [`docs/correlation-rationale.md`](./docs/correlation-rationale.md)); the
  **daemon** sends the focus event on a key press. The one-time TCC Automation
  grant therefore lands on both, each on first use.
- What to do when there are more than 15 concurrent sessions. **Current
  behavior:** the 16th session is tracked but gets no key (logged as
  `overflow`); a key frees up only when some session ends (no auto-reassign
  yet). Paging / LRU-evict is still open (M5).
- Whether to depend on tmux for session survival across Ghostty restarts, or
  accept that a UUID (and its key) dies with the surface. **Still open** — today
  the daemon prunes a dead mapping on the first failed focus and re-resolves on
  the session's next `SessionStart`.
