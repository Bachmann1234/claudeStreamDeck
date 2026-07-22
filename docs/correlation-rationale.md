# Session ↔ Surface correlation (M3/M4) — rationale

**Problem.** A Claude Code hook knows the `session_id` and `cwd`. The daemon
needs the session's **Ghostty surface UUID** so a key press can run
`focus terminal id "<uuid>"`. Nothing common links the two: the hook process and
the Ghostty surface share a terminal, but stock **Ghostty 1.3.1 exposes no
`tty` and no `pid`** over AppleScript — only `id`, `name` (title), and
`working directory` (see [`tier0-validation-findings.md`](./tier0-validation-findings.md)).
So the plan's original mechanism (`first terminal whose tty is "/dev/ttysNNN"`)
**cannot work on the shipped release**. We had to pick a different bridge.

> **Two hard constraints, one found empirically (2026-07-19 live test):**
> 1. Ghostty 1.3.1 has **no `tty`/`pid`** in its scripting dictionary.
> 2. Claude Code runs hooks with **no controlling terminal** — `open("/dev/tty")`
>    fails with *"Device not configured"*. So a hook **cannot write to its own
>    terminal** (no OSC escapes), only read Ghostty state via `osascript`.
> What *does* work from a hook: **read-only `osascript`** against Ghostty
> (TCC-permitting). The chosen design uses only that.

## Candidates evaluated

### A. tty matching (the plan's original) — ❌ blocked
`ps -o tty= -p $PPID` → `/dev/ttysNNN`, then `first terminal whose tty is …`.
Clean and unique **if Ghostty exposed `tty`**. It doesn't on 1.3.1. We still
emit the (best-effort) tty in messages so it lights up for free on a future
Ghostty, but it can't be the mechanism today.

### B. OSC title-sentinel self-resolution — ❌ blocked by constraint 2
The *original* choice: on `SessionStart` the hook writes a unique title sentinel
to its own terminal via an OSC 2 escape, then asks Ghostty for the surface whose
`name` contains it. Elegant — the set-and-read live in one process and the
sentinel is unique per session. **But it needs a writable controlling terminal,
and hooks don't have one** (constraint 2, found the first time we ran it live:
the sentinel was never written, so nothing resolved). Dead in this environment.

### C. cwd matching alone — ❌ not unique
`first terminal whose working directory is "<cwd>"`. The property exists and the
query works from a hook, but **two Claude sessions in the same repo collide** —
confirmed live: both open surfaces reported the identical `working directory`.
Unusable on its own; but it's a strong *disambiguation input* (see E).

### D. Manager-spawns-everything — ❌ too restrictive
Spawning every session captures the UUID from `new window` with no correlation,
but only covers sessions the manager launched — not the common case of typing
`claude` in a terminal you opened yourself.

### E. Focused-surface + cwd cross-check — ✅ chosen
On `SessionStart` (wired **synchronously**, so it runs while the new window is
still frontmost) the hook, using only read-only `osascript`:

1. reads the **focused front surface** — `id of focused terminal of selected
   tab of front window`;
2. reads the surfaces whose **`working directory`** equals the session `cwd`;
3. returns the focused id when it also matches cwd (or when Ghostty reports no
   cwd matches at all); else a *unique* cwd match; else **`None`** (won't guess).

The hook reports `(session_id, uuid)`; the daemon stores it. No AppleScript in
the daemon's hot path.

## Why E wins

- **It only uses the channel that actually works from a hook** — read-only
  `osascript`. No `/dev/tty`, no title writes.
- **It disambiguates the same-cwd collision.** At `SessionStart` the window the
  user just typed `claude` into *is the focused one*; cwd matching alone can't
  tell two same-repo sessions apart, but "focused **and** matches cwd" can.
  Verified live: with two sessions in the identical repo, it bound the
  newly-started (focused) surface, not its sibling.
- **Synchronous timing makes "focused" reliable.** The `SessionStart` hook is
  wired without `async`, so it queries before focus can wander. (Other events
  stay `async` — they carry no correlation and must never block Claude.)
- **It never binds the wrong window.** Anything ambiguous returns `None`: the
  session still gets a key and paints correctly; only *focus* waits until a
  future re-resolve. A missing binding is a safe failure; a wrong one isn't.
- **Daemon stays AppleScript-free on the hot path.** Correlation is paid once,
  in the hook, at session start. The daemon calls `osascript` only on an actual
  key press.

## Where this lives in the code

- `streamdeckd/hook.py :: resolve_uuid` — the focused+cwd resolver, with
  `run_osascript` injected so it's unit-tested with no real Ghostty
  (`tests/test_hook.py`); logs each `SessionStart` outcome to
  `~/.claudeStreamDeck/hook.log`.
- `streamdeckd/hook.py :: build_line` — only `SessionStart` pays the resolution
  cost; other events are a bare `(session_id, state)`.
- `streamdeckd/daemon.py :: Daemon._mirror_registry` — stores the resolved
  `session_id → uuid` in the gsm `Registry` via `Manager.bind`, so a press goes
  straight to `Manager.focus` (which prunes a dead surface).
- `.claude/settings.local.json` / `hooks/settings.snippet.json` — `SessionStart`
  is intentionally **not** `async`, for the timing guarantee above.

## Residual risks / future work

- **Starting a session in a non-focused window.** If you launch `claude` in a
  background Ghostty window and there are ≥2 same-cwd surfaces, step 3 returns
  `None` (no wrong bind). Recovery: a manual `gsm adopt <tag> --uuid …`, or a
  future daemon-side re-resolve. The single-session and distinct-cwd cases are
  unaffected.
- **UUIDs die with the Ghostty process.** After a restart every UUID is stale;
  the daemon prunes on the first failed focus and re-resolves on the session's
  next `SessionStart`.
- **When Ghostty ships `tty`.** Candidate A becomes viable and needs no title
  write — arguably the cleanest of all. The tty is already on the wire.
