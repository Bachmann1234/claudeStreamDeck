# Prompt: Ghostty fork implementation plan (for a Fable instance in plan mode)

Run this in a Fable session whose **working directory is the Ghostty fork source
tree**, in **plan mode**. Outcome: an implementation plan (no code) for adding an
identity-based focus API to Ghostty, so the Stream Deck manager can jump to an
exact session/surface. See [`../README.md`](../README.md) and
[`M4-tmux-jump.md`](./M4-tmux-jump.md) for why this beats the tmux/Ghostty-tab
approaches.

---

```
You are helping design a surgical fork of the Ghostty terminal emulator. Your
working directory is the Ghostty source tree. Produce an **implementation plan
only** — do not write code. Explore the actual source and ground every claim in
real files, functions, and line references.

## Research to do FIRST (before reading much code)
Read these discussions and extract any maintainer-preferred design direction,
prior art, or constraints — then align the plan with them (or note explicitly
where and why you diverge):
- **Scripting API for Ghostty (primary — this is the API design conversation):**
  https://github.com/ghostty-org/ghostty/discussions/2353
- Switch to or create tab by ID:
  https://github.com/ghostty-org/ghostty/discussions/10652
Summarize what you learned from these at the top of your plan.

## Background / motivation
I'm building a Stream Deck-based Claude Code session manager. Each running
session should map to a Stream Deck key; pressing that key must bring the
terminal to that exact session. The blocker: no terminal reliably exposes
"focus THIS specific surface by stable identity." Ghostty's `goto_tab` is
positional (index 1-9 only) and drifts as tabs open/close/reorder. tmux can
switch its own window but has zero authority over which Ghostty tab/window is
actually frontmost, so "which surface is visible" stays ambiguous.

The fix I want to scope: add a small, **identity-based focus API** to Ghostty so
an external process can focus an exact surface regardless of tab order or which
window is in front. A forked/patched Ghostty owns both the "session" concept and
window/tab/split focus, so "focus session X" becomes one authoritative operation.

## The feature to design (keep it minimal and upstreamable)
1. **Tag a surface with a stable, caller-supplied ID at spawn.** e.g.
   `ghostty +new-window --tag=<opaque-string>` (and/or the equivalent for new
   tab / new split). Investigate the cleanest injection point — CLI arg, env
   var, and/or an OSC/escape sequence a running program can emit to self-tag.
2. **Focus-by-tag IPC command.** e.g. `ghostty +focus --tag=<id>` that resolves
   the tagged surface and raises its window, selects its tab, and focuses its
   split — creating a correct, authoritative focus. Primary target platform is
   **macOS** (my daily driver); note what Linux/GTK would additionally need but
   don't let it block the macOS design.
3. **(Optional / stretch) active-surface-changed events** emitted over the same
   IPC channel, so the external manager always knows what's currently focused.

## Constraints and things to figure out from the code
- **Reuse existing machinery.** Ghostty already has surface/window/tab
  management, `goto_tab`, split navigation, and a growing IPC surface
  (`+new-window` via native IPC today; socket IPC and commands like
  `+toggle-quick-terminal` have been added). Find where IPC commands are defined
  and dispatched, and where surfaces are created — the patch should call
  existing internal actions, not reinvent them.
- **Map the layers.** Core is Zig; the macOS app is Swift/AppKit and owns
  windows/tabs. Identify exactly which layer must hold the tag→surface registry,
  which handles focus/raise, and how the Zig core and Swift apprt communicate
  across the C ABI boundary.
- **Keep the patch surgical** — ideally a self-contained module + a few small
  hooks — to minimize merge-conflict surface against a fast-moving upstream.

## Deliverable: the plan must contain
1. **Research summary** — key takeaways from discussions #2353 and #10652 and
   how they shape the design.
2. **Architecture** — where the tag registry lives, its lifecycle (spawn →
   focus → surface close/cleanup), and the Zig↔Swift data flow, with real file
   references.
3. **Change list** — each file to touch, what changes there, and why, ordered as
   implementable steps. Distinguish "new code" from "hook into existing X".
4. **The three pieces** (tag-on-spawn, focus-by-tag, optional events) scoped
   separately so the events piece can be deferred.
5. **IPC surface details** — exact command/flag shapes, the transport actually
   used on macOS, and how a tag is passed and resolved.
6. **Edge cases** — duplicate tags, tag on a closed/dead surface, surface in a
   background window vs. minimized vs. another Space, multiple windows.
7. **Testing strategy** — how to exercise the API manually and any automated
   coverage that fits Ghostty's existing test setup.
8. **Build & run from source on macOS** — toolchain/Zig version, how to build a
   runnable dev app, code-signing/entitlement gotchas, and the edit→build→test
   loop. (Assume this hasn't been done yet — it's the first real hurdle.)
9. **Upstreaming vs. fork-only** — assessment of whether this can land upstream
   as-is, what maintainers would likely want changed, and the fork-maintenance
   cost if it can't.
10. **Risks / unknowns** — anything that could make this bigger than it looks,
    flagged explicitly.

Start with the research step, then orient in the codebase — IPC command
dispatch, surface creation, and the Zig/Swift apprt boundary — and cite what you
find. Ask me if anything about the intended workflow is ambiguous before
finalizing.
```
