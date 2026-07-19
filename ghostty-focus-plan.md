# Implementation Plan: Identity-Based Surface Focus for Ghostty

Plan for a surgical Ghostty fork (or no fork at all) enabling a Stream Deck-based
Claude Code session manager to focus an exact Ghostty surface by stable identity.

Researched against the Ghostty source tree at `~/code/ghostty` (main,
`1.3.2-dev`, 2026-07-19) and GitHub discussions #2353 and #10652.

---

## TL;DR — the feature mostly already exists upstream

**Ghostty v1.3.0 shipped a full AppleScript dictionary and App Intents suite
that already implements identity-based focus on macOS.** Every surface has a
stable UUID, and `focus terminal id "<uuid>"` raises the window, selects the
tab, focuses the split, and activates the app — the exact "authoritative focus"
operation needed. What upstream does *not* have:

1. a caller-supplied tag,
2. a `+focus` CLI verb on macOS,
3. push events for focus changes.

Two of those three have zero-fork workarounds. The plan is therefore tiered:
**Tier 0 works today with stock Ghostty ≥ 1.3.0 and no fork at all**; the fork
(Tiers 1–2) is a small, Swift-only quality-of-life overlay.

---

## 1. Research summary (#2353, #10652)

**#2353 (Scripting API — the design conversation).** Mitchell Hashimoto
initially leaned toward a redis/memcached-style single-line text protocol, but
by mid-2025 explicitly pivoted: *"I think scoping down to specific use cases and
introducing the IPC necessary to do that is the way to go"* — i.e.
**platform-specific IPC**: AppleScript/App Intents on macOS (shipped via PR
#7634), D-Bus on Linux (PR #7679), unified socket protocol deferred
indefinitely. Two hard constraints matter for this design:

- **Escape-sequence remote control was rejected on security grounds** (anything
  `cat`-able could drive the terminal). So "self-tag via OSC sequence" is a
  known upstream no-go; it's dropped from the recommended design (noted as a
  fork-only option in §4).
- **Scoped commands over general APIs** — programs should get specific
  capabilities, not "an API that can do anything." A narrow "focus surface by
  stable ID" fits this stated scope precisely; a new socket transport does not.

**#10652 (switch/create tab by ID).** A collaborator answered by pointing at
#2353/#9084: positional `goto_tab` will not grow arbitrary IDs; programmatic
identity-based control is expected to arrive via the scripting API. No
commitment, no rejection. This confirms: don't extend `goto_tab` — ride the
scripting surface.

**How this shapes the plan:** anything built should be an extension of the
existing AppleScript/App Intents layer (upstream-aligned, Swift-only, small),
not a new IPC transport or a Zig-core change.

## 2. What the codebase actually provides (verified)

**Two separate "action" systems** (easy to conflate):

- `apprt.ipc.Action` (`src/apprt/ipc.zig:56`) — cross-*process* IPC used by
  `ghostty +new-window`. It has an authoritative "how to add an action"
  checklist at `src/apprt/ipc.zig:57-71`. **But on macOS its `performIpc` is a
  deliberate no-op** — `src/apprt/embedded.zig:331-339` returns `false` for
  every action; only GTK implements it, over D-Bus
  (`src/apprt/gtk/ipc/DBus.zig`). So `ghostty +focus` as a Zig CLI verb would
  have no transport behind it on macOS.
- `apprt.Action` (`src/apprt/action.zig`) — in-*process* Zig→Swift dispatch over
  the C ABI (`ghostty_runtime_action_cb`, `include/ghostty.h:1015`; Swift
  receiver `Ghostty.App.swift:481`). This is `goto_tab`/`goto_split` machinery —
  no changes needed there.

**The macOS scripting layer** (`macos/Sources/Features/AppleScript/`, dictionary
in `macos/Ghostty.sdef`, landed 2026-03-05, in v1.3.0):

- Object model: `application` → `windows` → `tabs` → `terminals`, all with
  stable string IDs. A terminal's ID is the `SurfaceView` UUID
  (`ScriptTerminal.swift:35-39`), plus readable `title`, `working directory`,
  `pid`, and **`tty`** (`ScriptTerminal.swift:66-70`).
- Commands: `new window` / `new tab` with a `surface configuration` record
  (`command`, `initial input`, `environment variables`, `working directory`,
  `font size` — sdef lines 107-122), `split`, **`focus`** ("bringing its window
  to the front"), `close`, `select tab`, `activate window`, `input text`,
  `send key`, mouse events.
- `focus` → `BaseTerminalController.focusSurface`
  (`BaseTerminalController.swift:273-285`): `Ghostty.moveFocus(to:)`
  (split-level first-responder focus, `SurfaceView.swift:1141`),
  `window.makeKeyAndOrderFront(nil)` (raises the window; with native tabs each
  tab is an NSWindow, so this also selects the tab — same pattern `onGotoTab`
  uses at `TerminalController.swift:1546`), and
  `NSApp.activate(ignoringOtherApps: true)`.
- Enumeration is derived, not a registry: `allSurfaceViews` flat-maps every
  `BaseTerminalController`'s `surfaceTree`
  (`AppDelegate+AppleScript.swift:319-327`). Weak refs everywhere → no cleanup
  bookkeeping to add.
- Gated by `macos-applescript` config, **default `true`**
  (`src/config/Config.zig:3426`).
- Parallel App Intents (Shortcuts) surface: `TerminalEntity` (UUID id, title,
  pwd, pid, tty, screenshot), `FocusTerminalIntent`, `NewTerminalIntent`
  (returns the created `TerminalEntity`), `GetTerminalDetailsIntent`.

**Identity in the Zig core:** each core surface has a random `u64` id exported
to the child process as `GHOSTTY_SURFACE_ID` (`src/Surface.zig:62`, `582-588`,
`644-646`) — but there's no C-API accessor for it and it is *not* correlated to
the Swift UUID. The usable correlation key today is **`tty`**: visible both
inside the session (`tty` command) and via AppleScript.

---

## 3. The tiered design

### Tier 0 — no fork: run the manager against stock Ghostty ≥ 1.3.0

The tag→surface registry lives **in the Stream Deck manager**, not in Ghostty:

1. **Spawn + capture identity.** `new window with configuration {command:…,
   working directory:…, environment variables:{"CC_SESSION=mytag"}}` returns a
   `tab`; read `id of focused terminal of` the result. Store `mytag → uuid` in
   the manager.
2. **Focus.** `osascript -e 'tell app "Ghostty" to focus terminal id "<uuid>"'`
   — full authoritative focus via `focusSurface`.
3. **Adopt sessions the manager didn't spawn** (self-registration): a Claude
   Code hook (e.g. SessionStart) reports its `tty` to the manager; the manager
   resolves `first terminal whose tty is "/dev/ttysNNN"` and records the UUID.
   This replaces the OSC self-tag idea with something upstream already supports.
4. **Focus-state (poll).** `focused terminal of front window` + `frontmost of
   application` at 1–2 Hz is plenty for Stream Deck key highlighting.
5. On Ghostty restart, UUIDs are gone — re-resolve every known session by `tty`
   (ttys persist as long as the shells do; if Ghostty itself died, the sessions
   did too unless they're under tmux, in which case re-spawn + reattach and
   re-capture).

**Recommendation: build the manager against Tier 0 first** — it validates the
whole workflow before committing to fork maintenance, and everything in Tiers
1–2 is additive.

### Tier 1 — thin fork: first-class caller-supplied tags (Swift-only, upstreamable)

Makes the tag durable inside Ghostty so the manager doesn't have to keep a
mapping, and makes `whose tag is` queries possible. **No Zig, no C-ABI, no
`ghostty.h` changes** — the tag lives entirely on the Swift `SurfaceView`.

### Tier 2 — fork: push events for focus changes (deferrable)

Distributed-notification broadcast on focused-surface change, replacing polling.

---

## 4. Change list (ordered, per piece)

### Piece A — tag-on-spawn (Tier 1)

| Step | File | Change | Kind |
|---|---|---|---|
| A1 | `macos/Sources/Ghostty/Surface View/SurfaceView_AppKit.swift` | Add `var tag: String?` stored property on `SurfaceView`; accept it in the initializer (near the existing `uuid: UUID?` param at line 222) | new code |
| A2 | `macos/Sources/Ghostty/Surface View/SurfaceView.swift` | Add `tag: String?` to `struct SurfaceConfiguration` (line 629). It does **not** go into `withCValue` — it never crosses the C ABI; `BaseTerminalController` passes it to the view at creation (`BaseTerminalController.swift:142`) | hook into existing |
| A3 | `macos/Sources/Features/AppleScript/ScriptSurfaceConfiguration.swift` + `macos/Ghostty.sdef` | Add a `tag` key to the `surface configuration` record (follow the existing `environment variables` key pattern; respect the sdef ordering rules in `macos/AGENTS.md`) | hook into existing |
| A4 | `macos/Sources/Features/AppleScript/ScriptTerminal.swift` + sdef `terminal` class | Expose `tag` as a **read/write** property (`@objc(tag)` getter/setter following the `title` pattern at lines 42-46). Writable ⇒ retroactive tagging: `set tag of (first terminal whose tty is "…") to "mytag"` | new code |
| A5 (optional) | `macos/Sources/Features/App Intents/NewTerminalIntent.swift`, `Entities/TerminalEntity.swift`, `FocusTerminalIntent.swift` | Mirror the tag as an intent parameter/entity property for Shortcuts parity | hook into existing |

Lifecycle: no registry map, so no cleanup path — a tag dies with its
`SurfaceView`, and all lookups filter live views via the existing
`allSurfaceViews` (`AppDelegate+AppleScript.swift:319`). Tags are intentionally
*not* persisted across restarts (session identity is process identity).

### Piece B — focus-by-tag (Tier 1)

With A4 in place this needs **zero new commands**:
`focus (first terminal whose tag is "mytag")`. Two robustness patches:

| Step | File | Change | Kind |
|---|---|---|---|
| B1 | `macos/Sources/Features/Terminal/BaseTerminalController.swift:273-285` | Harden `focusSurface`: `if window.isMiniaturized { window.deminiaturize(nil) }` before `makeKeyAndOrderFront` (see §6 — minimized windows are likely broken today; this patch is upstreamable on its own) | hook into existing |
| B2 (skip, recommended) | — | A `ghostty +focus --tag=X` Zig CLI verb. **Recommend not building it**: `performIpc` is intentionally stubbed on macOS (`embedded.zig:338`) and adding an Apple-events client to the Zig CLI is a big, unupstreamable patch. Instead ship a 3-line `ghostty-focus` shell wrapper around `osascript`, or have the Stream Deck plugin speak Apple events directly (`NSAppleScript`/JXA) | — |

Linux/GTK note (don't block on it): GTK already registers a **`present-surface`**
gio action (`src/apprt/gtk/class/application.zig:1422`) reachable over D-Bus —
an identity-based present already exists there, keyed on the core surface id. A
tag version would follow the `src/apprt/ipc.zig:57` checklist + a new D-Bus
action; entirely parallel to the macOS work.

### Piece C — focus events (Tier 2, deferrable)

| Step | File | Change | Kind |
|---|---|---|---|
| C1 | `macos/Sources/Features/Terminal/BaseTerminalController.swift` | In the `focusedSurface` change path (`syncFocusToSurfaceTree`, line 300, is called on every surface/window focus change), post `DistributedNotificationCenter.default()` notification `com.mitchellh.ghostty.surfaceDidFocus` with userInfo `{id, tag}` — **no title/pwd** (distributed notifications are a system-wide broadcast; don't leak session content). Debounce, since sync runs per window-key change | new code |
| C2 | Manager side | Subscribe via `DistributedNotificationCenter`; fall back to Tier 0 polling if absent | — |

This piece is the least likely to be upstreamable as-is (see §9) — keep it
isolated in one small extension file so it rebases trivially.

---

## 5. IPC surface details (macOS transport)

Transport is **Apple events** (Cocoa scripting against `Ghostty.sdef`), the
maintainer-chosen macOS mechanism. Concrete shapes:

```applescript
-- Spawn tagged (Tier 1) / untagged (Tier 0):
tell application "Ghostty"
  set t to new window with configuration {command:"claude", working directory:"/Users/bachmann/proj", tag:"proj-main"}
  get id of focused terminal of t          -- capture UUID (Tier 0)
end tell

-- Focus:
tell application "Ghostty" to focus (first terminal whose tag is "proj-main")    -- Tier 1
tell application "Ghostty" to focus terminal id "6F1C4C64-…"                     -- Tier 0
tell application "Ghostty" to focus (first terminal whose tty is "/dev/ttys004") -- adoption path

-- State poll:
tell application "Ghostty" to get {id, tty} of focused terminal of front window
```

From the manager: `osascript` (simplest), JXA (`osascript -l JavaScript`,
better for JSON-ish data), or `NSAppleScript`/ScriptingBridge if the manager is
a native app. The App Intents route (`shortcuts run …`) also works but
parameter passing is clumsier; treat it as secondary.

**TCC:** the first Apple event triggers an Automation prompt attributed to the
*calling* process's responsible app (for a Stream Deck plugin, that's Elgato's
plugin host) — a one-time grant, and a bundled native manager needs
`NSAppleEventsUsageDescription` in its Info.plist. Also requires
`macos-applescript` not disabled in Ghostty config.

## 6. Edge cases

- **Duplicate tags:** with `whose` filtering the ambiguity is the caller's:
  `first terminal whose…` picks one (window order). Recommend last-writer-wins
  on `set tag` with no uniqueness enforcement in Ghostty, and the manager
  treating tags as unique by convention — matches AppleScript idioms, keeps the
  patch small. Document it.
- **Dead/closed surface:** `ScriptTerminal.surfaceView` is weak; `focus` errors
  with "Terminal surface is no longer available"
  (`ScriptTerminal.swift:139-143`), and `whose` queries only see live views.
  Manager maps AppleScript error → remove key from Stream Deck.
- **Minimized window:** `makeKeyAndOrderFront` does not reliably deminiaturize —
  this is patch B1, and a good first manual test against stock behavior.
- **Other Space / fullscreen:** `NSApp.activate` + `makeKeyAndOrderFront`
  normally triggers a Space switch, but behavior interacts with System Settings
  → Desktop & Dock → "When switching to an application, switch to a Space with
  open windows." Verify empirically; may need `collectionBehavior` handling in
  B1. Flagged as risk, not designed around yet.
- **Quick terminal:** `QuickTerminalController` overrides `focusSurface`
  (`QuickTerminalController.swift:255`) and its surfaces are `kind: quick` —
  exclude them from tag lookup or test explicitly.
- **App restart:** UUIDs and (Tier 1) tags are gone with the process. Manager
  re-resolves via `tty` or respawns. Never persist tags in Ghostty.
- **Multiple windows/tab groups:** handled by design — `makeKeyAndOrderFront`
  on the surface's own NSWindow both raises the right window and selects the
  right tab (proven pattern, `TerminalController.swift:1546`).

## 7. Testing strategy

- **Manual loop (documented in `macos/AGENTS.md`):** build, then drive with
  `osascript` **targeting the app by absolute path**
  (`tell application "/Users/bachmann/code/ghostty/macos/build/Debug/Ghostty.app"`)
  so tests don't hit an installed Ghostty. Script a checklist: spawn-with-tag →
  query `whose tag is` → focus from background/minimized/other-Space/
  other-window → close → verify error on stale focus.
- **Automated:** Swift unit tests live in `macos/Tests`
  (`macos/build.nu --action test`); cover `SurfaceConfiguration` tag plumbing
  and any sdef record parsing. Cocoa-scripting handlers are hard to unit-test
  headlessly — lean on a small shell/osascript integration script checked into
  the fork instead (UI tests are deliberately skipped per `macos/AGENTS.md`).
  No Zig tests needed (core untouched).

## 8. Build & run from source (macOS)

- **Toolchain:** Zig ≥ 0.15.2 (`build.zig.zon:6`); **Xcode 26 + macOS 26 SDK**
  (+ iOS SDK + Metal Toolchain) for main-branch builds — works on macOS 15 with
  Xcode 26 installed; `sudo xcode-select --switch /Applications/Xcode.app`.
  Known Zig 0.15.x link failure with **Xcode 26.4** — use
  `brew install zig@0.15` or the Nix flake (both patched), or stay on Xcode
  26.3 (`HACKING.md:63-76`).
- **Build:** debug is the default (`zig build`, no `-Doptimize`). For the app:
  `zig build -Demit-macos-app=false` (builds the core lib) then
  `macos/build.nu --configuration Debug` →
  **`macos/build/Debug/Ghostty.app`**. Swift-only changes (all of Tier 1) only
  need the `build.nu` step — a fast edit→build→osascript loop.
- **Signing/entitlements:** dev builds use `GhosttyDebug.entitlements`; local
  signing is handled by the Xcode build — no Apple Developer account needed to
  run locally. Gotcha: the dev app shares the `com.mitchellh.ghostty` bundle ID
  with any installed Ghostty, so (a) always address it by path in scripts, and
  (b) TCC/Automation grants are per-bundle-ID and can get confused if both run —
  consider quitting the installed copy during dev.
- **Logs:** `sudo log stream --level debug --predicate
  'subsystem=="com.mitchellh.ghostty"'`; debug builds also log to stderr.
- **Fork base:** branch from the `v1.3.1` tag rather than `main` for a stable
  daily driver; the AppleScript feature is fully present there.

## 9. Upstreaming vs. fork-only

- **B1 (deminiaturize fix):** straightforwardly upstreamable if testing
  confirms the bug. Send first — it builds credibility and shrinks the fork.
- **Pieces A + tag property (Tier 1):** good upstream odds. It's exactly the
  #2353 scope ("specific use case" — external session managers), Swift-only,
  rides the maintainer-chosen surface, follows existing sdef conventions, and
  is gated by `macos-applescript`. Likely maintainer asks: naming (`tag` vs
  `custom id`), App Intents parity (do A5), docs, and possibly "why not just
  use `id`/`tty`?" — the answer being human-meaningful, caller-controlled,
  retroactively settable identity.
- **Piece C (distributed notifications):** least likely upstream — #2353
  explicitly deferred events, and a system-wide broadcast has privacy/design
  questions. Keep it fork-only in one isolated file.
- **Fork-maintenance cost:** low. Tier 1 touches ~5 Swift files with additive
  changes, zero Zig/C-ABI surface. Main churn risk is the young AppleScript
  feature directory itself; branching from release tags and rebasing per
  release keeps conflicts rare.

## 10. Risks / unknowns

1. **Space/minimize/fullscreen focus behavior** is the only part of the core
   primitive not verifiable statically — test first, before writing any code;
   it determines whether B1 grows.
2. **`whose` clauses on a custom property** should work like `title` (plain
   Cocoa-scripting KVC) but must be verified; fallback is client-side filtering
   over `every terminal`, which is fine at Stream-Deck scale.
3. **TCC prompt attribution** from inside the Elgato plugin host may be
   confusing or (worst case) suppressed in some contexts — verify early with a
   hello-world plugin that sends one Apple event.
4. **Apple-event latency** (~tens of ms/call) is fine for keypress-driven
   focus, but don't poll state at high frequency through it; 1–2 Hz max, or do
   Tier 2.
5. **Upstream churn** in `macos/Sources/Features/AppleScript/` while the
   feature is young.
6. Sessions surviving Ghostty restarts fundamentally need tmux underneath; the
   focus API can't fix that — worth deciding early whether the manager assumes
   tmux-in-Ghostty or plain surfaces.

---

## Recommended sequencing

1. Build the Stream Deck manager against **Tier 0 today with stock Ghostty** —
   no fork, no build toolchain, immediate validation.
2. Empirically test the focus edge cases (minimized, other Space, fullscreen)
   against stock behavior.
3. Only then take on the fork (Tier 1), once the workflow proves out and it's
   clear which ergonomics actually hurt.
4. Defer Tier 2 (events) until polling demonstrably falls short.
