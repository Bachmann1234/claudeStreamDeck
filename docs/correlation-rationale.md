# Session ↔ Surface correlation (M3/M4) — rationale

**Problem.** A Claude Code hook knows the `session_id` and `cwd`. The daemon
needs the session's **Ghostty surface UUID** so a key press can run
`focus terminal id "<uuid>"`. Nothing common links the two: the hook process and
the Ghostty surface share a terminal, but stock **Ghostty 1.3.1 exposes no
`tty` and no `pid`** over AppleScript — only `id`, `name` (title), and
`working directory` (see [`tier0-validation-findings.md`](./tier0-validation-findings.md)).
So the plan's original mechanism (`first terminal whose tty is "/dev/ttysNNN"`)
**cannot work on the shipped release**. We had to pick a different bridge.

## Candidates evaluated

### A. tty matching (the plan's original) — ❌ blocked
`ps -o tty= -p $PPID` → `/dev/ttysNNN`, then
`first terminal whose tty is …`. Clean and unique **if Ghostty exposed `tty`**.
It doesn't on 1.3.1; the query errors out. We still *emit* the tty in every
message (cheap, useful diagnostics) so this lights up for free on a future
Ghostty that adds the property — but it can't be the mechanism today.

### B. cwd matching — ❌ not unique
`first terminal whose working directory is "<cwd>"`. The property exists, but
**two Claude sessions open in the same repo collide**, and cwd changes as you
`cd` around, so a later re-resolve can match the wrong surface. Fine as a
last-ditch fallback, unacceptable as the primary key. (The daemon still carries
`cwd` and could offer a `--resolve-by-cwd` mode, but it's off the hot path.)

### C. Manager-spawns-everything — ❌ too restrictive
If the manager spawns every session with `gsm spawn`, it captures the UUID
directly from `new window` — no correlation needed (this already works, see the
findings doc). But it only covers sessions *the manager launched*. Any session
you started yourself (the overwhelmingly common case — you open Ghostty and type
`claude`) is invisible. A session manager that can't see sessions it didn't
spawn isn't the tool we're building.

### D. Title-sentinel self-resolution — ✅ chosen
On `SessionStart`, the hook — running **inside the target surface** — writes a
**unique title sentinel** to its own controlling terminal via an OSC 2 escape
(`ESC ] 2 ; ⟦gsm:<session_id>⟧ BEL` → `/dev/tty`), then asks Ghostty:

```applescript
tell application "Ghostty" to get id of ¬
  (first terminal whose name contains "⟦gsm:<session_id>⟧")
```

The returned `id` is this session's surface UUID. The hook reports
`(session_id, uuid)` to the daemon, which just stores the mapping — **no
AppleScript in the daemon's hot path at all.** Finally the hook rewrites the
title to a friendly label (the repo basename) so the sentinel never lingers.

## Why D wins — and how it contains the race

Setting a title then reading it back is inherently racy: another process could
overwrite the title in between, or the terminal could match a *stale* value. The
sentinel design defuses both:

1. **The set and the read live in one process.** The hook writes the sentinel
   and immediately queries for it — the window between the two is microseconds
   of the hook's own execution, not a cross-process handoff. Nothing schedules a
   title change into that gap under normal use.
2. **The sentinel is globally unique** (it embeds `session_id`). Even if ten
   sessions call `SessionStart` at the same instant, each writes a *different*
   sentinel, so `name contains "⟦gsm:<mine>⟧"` matches **only that session's own
   surface** — never a sibling's. This is the property cwd matching lacks.
3. **Latency is absorbed by a short retry loop.** Ghostty needs a beat to
   process the escape and update `name`; the resolver polls a handful of times
   (~50 ms apart) before giving up. Terminal title updates are effectively
   instant, so this converges on the first or second try in practice.
4. **It degrades quietly.** No controlling tty, Ghostty not scriptable, or TCC
   not yet granted → the resolver returns `None`, the hook reports without a
   UUID, and the daemon still allocates and paints the key. Only *focus* is
   unavailable until a UUID is known; the deck is never wrong, just incomplete.
   (A future daemon-side re-resolve by cwd could fill the gap.)

Two further points in D's favor:

- **The daemon stays AppleScript-free on the hot path.** Correlation cost is
  paid once, in the hook, at session start — not on every key press and not in
  the daemon's poll loop (which the plan caps at 1–2 Hz for Apple-event
  latency). The daemon only calls AppleScript when you actually *press a key*.
- **The TCC grant lands on the right process.** The Apple event that resolves
  the UUID is sent by the hook (a child of the Claude Code process in the
  terminal). The one that *focuses* is sent by the daemon. Both are the user's
  own processes; the one-time Automation prompt is expected on first use of
  each. See [`setup.md`](./setup.md).

## Where this lives in the code

- `streamdeckd/hook.py :: resolve_uuid_via_sentinel` — the set-title-then-query
  resolver, with `write_title` / `run_osascript` / `sleep` injected so it's unit
  tested with zero real terminals or osascript (`tests/test_hook.py`).
- `streamdeckd/hook.py :: build_line` — assembles the reported message; only
  `SessionStart` pays the resolution cost, other events are a bare
  `(session_id, state)`.
- `streamdeckd/daemon.py :: Daemon._mirror_registry` — stores the hook-resolved
  `session_id → uuid` in the gsm `Registry` via `Manager.bind`, so a press goes
  straight to `Manager.focus` (which already prunes a dead surface).

## Residual risks / future work

- **Claude Code owning the title.** If a future Claude Code build continuously
  rewrites the terminal title, it could clobber the sentinel before the query
  lands. Mitigation if that appears: shrink the retry delay, or have the hook
  hold the sentinel across a couple of query attempts before restoring. Not
  observed today.
- **UUIDs die with the Ghostty process.** After a Ghostty restart every UUID is
  stale. Re-resolution happens naturally on the next `SessionStart` (e.g. a
  `--resume`); the daemon prunes the dead mapping on the first failed focus.
- **When Ghostty ships `tty`.** Candidate A becomes viable and is arguably
  cleaner (no title flicker at all). The tty is already on the wire, so the
  daemon could prefer it with no hook change.
