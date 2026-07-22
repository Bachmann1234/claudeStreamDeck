# Tier 0 Validation Findings

Empirical validation of `ghostty-focus-plan.md` §3 (Tier 0) and §6 (edge cases)
against **stock installed Ghostty**, before building anything or forking.

- **Date:** 2026-07-19
- **Machine:** macOS (Darwin 25.5.0), Apple Silicon
- **Ghostty:** **1.3.1** (stable channel) — `/Applications/Ghostty.app`
- **Method:** `osascript` Apple events against `application "Ghostty"`, plus
  `System Events` for window-state manipulation. No fork, no source build.

---

## TL;DR

The core Tier 0 primitive — **`focus terminal id "<uuid>"`** — works, and works
well: background, behind-another-app, and **minimized** targets are all raised
correctly. Two things the plan assumed are **wrong for the installed 1.3.1
release** and change the design:

1. 🔴 **`tty` and `pid` are NOT in the 1.3.1 AppleScript dictionary.** The
   `terminal` class exposes only `id`, `name`, and `working directory`. The
   plan's tty-based adoption path (`first terminal whose tty is …`, the core of
   milestones M3/M4) **cannot work on stock 1.3.1**. The plan was researched
   against `main` / `1.3.2-dev`, where tty/pid exist — they simply have not
   shipped in a stable release yet.
2. 🟢 **Minimized-window focus already works** (deminiaturizes on focus). The
   plan hypothesized this was broken and needed fork patch **B1** — **not needed
   on 1.3.1** for the minimize case.

The one real failure is **focusing *into* a native-fullscreen window on its own
Space** (see below) — that is the case that may justify a Space-handling fork
patch later.

---

## Step 1a — Version

```
$ ghostty --version
Ghostty 1.3.1  (channel: stable, Zig 0.15.2, ReleaseFast)
```

✅ ≥ 1.3.0. Matches the plan's recommended fork base (`v1.3.1` tag).

## Step 1b — Scripting API smoke test

- **TCC / Automation:** Apple events to Ghostty succeed with **no visible
  prompt** — the grant for this terminal/Claude Code process was already in
  place (or auto-granted). Every read/command below returned data, so
  `macos-applescript` is enabled (its default) and Automation is authorized.
- `get id of every terminal` → returns the surface UUIDs. ✅
- **Record-of-multiple-properties form fails:**
  `get {id, tty} of every terminal` → error **-1700** *"Can't make … into type
  specifier."* This is partly the missing-`tty` property (below) and partly an
  AppleScript idiom quirk — query one property per call, or build records in a
  `repeat` loop. Not a blocker; the manager reads properties individually.

### 🔴 The dictionary is smaller than the plan assumed

Ground truth from `/Applications/Ghostty.app/Contents/Resources/Ghostty.sdef`
(1.3.1). The `terminal` class has exactly three properties:

| Property            | Access | Notes                                   |
|---------------------|--------|-----------------------------------------|
| `id`                | r      | Stable surface UUID — the focus key     |
| `name`              | r      | Current terminal **title**              |
| `working directory` | r      | Current cwd of the terminal process     |

**Absent in 1.3.1:** `tty`, `pid`, `title` (it's `name`), `tag`, `kind`.
Probing each individually returns **-1700** ("into type specifier"), confirming
they are not in the shipped dictionary — not just unset.

Working read paths that the manager relies on instead:

- Focused surface: `id of focused terminal of selected tab of front window` ✅
- App focus state: `frontmost` (of `application "Ghostty"`) ✅
- Correlation fallbacks (since no tty): `first terminal whose working directory
  is "…"` ✅ and `first terminal whose name contains "…"` ✅
- `surface configuration` record for spawn supports: `command`,
  `initial working directory` (note: **not** `working directory`),
  `initial input`, `environment variables` (KEY=VALUE list), `font size`,
  `wait after command`. ✅ So env vars *can* be injected at spawn.

## Step 1c — Focus edge cases (plan §6)

Each case: manipulate a spawned test window into the target state, run
`focus terminal id "<uuid>"`, then assert `frontmost` **and**
`id of focused terminal of selected tab of front window == <uuid>`.

| Case                                         | Result | Notes |
|----------------------------------------------|:------:|-------|
| Target window **in background** (behind another Ghostty window) | ✅ PASS | Raised + app activated |
| Target while a **different app** is frontmost (Finder) | ✅ PASS | Ghostty brought frontmost |
| Target window **minimized**                  | ✅ PASS | `focus` **deminiaturized** it — contradicts plan's B1 hypothesis; B1 not needed on 1.3.1 |
| Focus **away** from a native-fullscreen window | ✅ PASS | Switching to a normal-Space terminal works even with a fullscreen window present |
| Focus **into** a native-fullscreen window (own Space) | 🔴 **FAIL** | Space did **not** switch; the fullscreen target never came to front (front stayed on the other-Space window). Same measurement method as the PASS row above, so the asymmetry is real. |
| Target on **another user-created Space** (non-fullscreen) | ✅ PASS *(setting-dependent)* | **Verified manually 2026-07-21.** With `com.apple.dock workspaces-auto-swoosh` **ON** (the macOS default), `focus terminal id` **switched to the other Space and raised the window**. With it **OFF**, focus stayed put — no Space switch. See the dedicated note below. |

### Cross-Space focus depends on one Dock setting (manual test, 2026-07-21)

Put a Ghostty window on a second **non-fullscreen** Space, switched away, then
ran the exact `focus terminal id "<uuid>"` a keypress uses:

| `com.apple.dock workspaces-auto-swoosh` | Result |
|:----------------------------------------|:-------|
| **ON** (unset default) | ✅ macOS switched to the target's Space and raised the window |
| **OFF** (`-bool false`, `killall Dock`) | ❌ stayed on the current Space; window not brought across |

This is the System Settings toggle **Desktop & Dock → "When switching to an
application, switch to a Space with open windows."** It is **ON by default**, so
**cross-Space focus works out of the box** for regular Spaces — no fork, no
Space-handling code needed. The daemon should simply document this setting as a
prerequisite (like the Automation/TCC grant). Only **native-fullscreen** targets
(row above) remain a hard failure; that is the sole case the deferred Space fork
would address, and it's niche enough to stay deferred.

### Dead-surface error signatures (for the prune path)

A surface only truly dies when **closed** — a surface spawned via AppleScript
does **not** auto-close when its `command` exits (even without
`wait after command`). Once actually closed, `focus` errors, and the signature
depends on the addressing form:

| Addressing form used to focus a dead surface        | Error |
|-----------------------------------------------------|-------|
| `focus terminal id "X"` (object specifier — M4's form) | **-1728** "Can't get terminal id …" |
| `focus (first terminal whose id is "X")`            | **-1719** "… Invalid index" |

The manager treats **-1728**, **-1719**, and any *"no longer available"* message
as a dead-surface signal → prune the mapping. (The plan cited the
*"Terminal surface is no longer available"* string; on 1.3.1 the closed-surface
case surfaces as the two `-172x` errors above, because the specifier resolves to
nothing rather than to a live object with a nil weak `surfaceView`.)

### Other observed behaviors — `close` raises a blocking modal ⚠️

- `close` on a surface with a **running process** raises Ghostty's
  **"Close Window? All terminal sessions in this window will be terminated"**
  confirmation. This is an **app-modal alert window**, *not* an `AXSheet`
  attached to the terminal window — so a System-Events check for sheets on the
  window finds nothing (an earlier draft of this doc wrongly concluded no
  confirmation appears). The alert **blocks all further Apple events** until
  dismissed — the exact "don't trigger modal dialogs" hazard. The "async close"
  behavior seen in testing was really *a pending confirmation dialog going
  unnoticed*.
- **Compounding hazard — native tab-merging:** windows spawned via `new window`
  can be folded into an existing Ghostty window as native macOS tabs (per the
  system "prefer tabs" setting). A window-level `close` (or a confirmed
  "Close Window") then terminates **every** tab in that shared window. Test
  harnesses must spawn/close carefully and never issue window-level closes near
  real sessions.
- **Manager impact: none.** Tier 0's hot path never calls `close` — `focus`
  prunes only the registry *mapping*, never the real surface. This hazard is
  relevant only to test cleanup and any future "close from the deck" feature,
  which must expect the modal (and should target the specific terminal, never
  the window, ideally with `confirm-close-surface` implications understood).

---

## What this means for the build

1. **Spawn + focus + status: fully work today on 1.3.1.** Spawn captures the
   surface UUID directly from `new window`; focus by `id`; status via
   `front window` / `selected tab` / `focused terminal`. Build these now.
2. **Adopt-by-tty is blocked on stock 1.3.1.** The manager still exposes
   `adopt --tty`, but it detects the missing-property `-1700` and fails with a
   clear message; the working resolvers today are `--uuid`, `--cwd`, and
   `--title-contains`. When a Ghostty that exposes `tty` is installed,
   `adopt --tty` lights up with no code change. **This is the finding that most
   affects M3/M4** — the hooks cannot correlate purely by tty against 1.3.1.
3. **Fork patch B1 (deminiaturize) is NOT needed on 1.3.1** — minimize already
   works. If a fork patch is ever justified for focus robustness, it's the
   **fullscreen-on-own-Space / cross-Space switch**, which is the one confirmed
   failure. Verify the non-fullscreen cross-Space case manually before scoping
   any patch.

## Manual check — DONE 2026-07-21 ✅

Ran exactly as scoped: a Ghostty window on a second (non-fullscreen) Space,
switched away, then `focus terminal id "<uuid>"`. **Result: cross-Space focus
works when `com.apple.dock workspaces-auto-swoosh` (System Settings → Desktop &
Dock → "When switching to an application, switch to a Space with open windows")
is ON — the macOS default — and fails when it's OFF.** That setting is the lever;
it's on by default, so no fork is needed for regular Spaces. Full table in *Step
1c* above.
