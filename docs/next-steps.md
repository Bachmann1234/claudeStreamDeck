# Next steps — the human/hardware-gated work

Everything buildable headless is done (M2 daemon, M3 hooks + correlation, M5
overflow/preview/launchd; 112 tests green). What's left needs **you at the
machine** (TCC grants, a real Claude session, visual checks) or the **physical
deck**. Ordered so the earliest items unblock the most.

Respect the destructive-test hazard in `CLAUDE.md` throughout: the manager and
hooks only ever *report* and *focus* — never `close` a surface. Don't spawn or
close throwaway windows near your real sessions.

---

## 1. Live smoke test the hooks + daemon (no hardware needed) — ✅ DONE 2026-07-19

Proven end-to-end against real Claude Code + Ghostty 1.3.1: session lit a key,
dim → blue → green, and `SessionEnd` blanked it. The live run also exposed the
`/dev/tty` constraint that drove the correlation pivot (steps 2–3). Original
checklist kept below for reference / re-runs.

- [ ] Start the daemon in a spare terminal: `streamdeckd -v`
- [ ] Watch the deck in another: `open ~/.claudeStreamDeck/virtualdeck/deck.png`
      (re-open, or `watch -n1 cat ~/.claudeStreamDeck/virtualdeck/snapshot.json`)
- [ ] Wire the hook: copy `hooks/settings.snippet.json` into
      `~/.claude/settings.json`, replacing the command path with
      `which claudestreamdeck-hook` (the venv path). Restart Claude Code so it
      reloads settings.
- [ ] Start a normal `claude` session in a Ghostty window. **Approve the macOS
      Automation (TCC) prompt** for the hook on first `SessionStart`.
- [ ] Confirm the key lifecycle on the deck preview:
      dim (start) → blue (you prompt) → **yellow** (when it asks you something)
      → green (when it finishes) → blank (on exit).

**Expected pass:** a key lights within a second of each event. If keys never
change, run the daemon with `-v` and check the socket path matches on both sides
(`GSM_HOME` / `STREAMDECKD_SOCKET`).

## 2. Confirm the correlation works live — ✅ DONE 2026-07-19

The `SessionStart` hook resolved the surface UUID and bound it in
`registry.json`. **The original OSC title-sentinel design failed** (hooks have
no `/dev/tty`); pivoted to focused-surface + cwd cross-check over read-only
`osascript`, which correctly picked the newly-started window out of two
same-cwd sessions. See the rewritten `docs/correlation-rationale.md`.

- [ ] After starting a session (step 1), check the mapping was resolved:
      `cat ~/.claudeStreamDeck/registry.json` — the session's entry should have
      a non-null `"uuid"`. (Or `gsm status`.)
- [ ] Watch for title flicker at session start — the sentinel should appear for
      a blink then get replaced by the repo name. If Claude Code clobbers the
      title before the lookup lands, the uuid will be null → tell me and I'll
      tune the retry window or hold the sentinel across attempts.

## 3. Test focus-by-keypress (M4) — ✅ DONE 2026-07-19 (core)

`{"press":0}` raised the exact bound surface (focused flipped from this
conversation's window to the target session's), and `last_focused_at` updated.
Remaining M4 edge cases (cross-Space / fullscreen) are step 4. Original
checklist below.

With a resolved uuid from #2:

- [ ] From another terminal, focus that session's surface:
      `printf '{"press":0}\n' | nc -U ~/.claudeStreamDeck/streamdeckd.sock`
      (key index from the `deck.png` tile). **Approve the second TCC prompt** —
      this one is attributed to `streamdeckd`.
- [ ] Confirm it raises the exact window/tab/split and activates Ghostty.
- [ ] Edge cases from `docs/tier0-validation-findings.md`: background window ✅,
      minimized ✅ (both already validated) — just confirm they still hold.

## 4. The one un-scriptable check from the findings doc ⏱️ ~5 min

`docs/tier0-validation-findings.md` flagged this as needing a human:

- [ ] Put a Ghostty window on a **second (non-fullscreen) Space**, switch away,
      then `{"press":N}` that session. Does macOS switch Spaces to it?
- [ ] Toggle **System Settings → Desktop & Dock → "When switching to an
      application, switch to a Space with open windows"** and retest.
- [ ] Report which setting makes cross-Space focus work — determines whether the
      deferred fullscreen/Space fork patch is ever worth it.

## 5. Install as a login service (optional) ⏱️ ~5 min

- [ ] `cp service/com.claudestreamdeck.streamdeckd.plist ~/Library/LaunchAgents/`,
      fill the `/ABSOLUTE/PATH` placeholders, then
      `launchctl load ~/Library/LaunchAgents/com.claudestreamdeck.streamdeckd.plist`
      (full steps in `docs/setup.md §9`).

## 6. Hardware bring-up (M1) — ✅ DONE 2026-07-21

Everything above uses the VirtualDeck; this lit up the real board.

- [x] **Quit the Elgato Stream Deck app** (it grabs the USB device exclusively).
- [x] `brew install hidapi` (already present, 0.15.0), `pip install streamdeck`
      (0.9.8) into the venv.
- [x] No Input-Monitoring grant was needed — HID access worked immediately.
- [x] Ran the M1 smoke script: enumerated **Stream Deck Original**, 15 keys,
      serial `AL50I2C01764`, fw `1.02.004`, format 72×72 JPEG flip-both; set
      brightness; painted keys; all presses printed. See
      `milestones/M1-hardware-smoke-test.md`.
- [x] Built `StreamDeckRenderer` behind the existing `Renderer` interface
      (`streamdeckd/streamdeck_renderer.py` — formats `KeyAppearance` frames via
      PILHelper, change-detects to skip redundant USB writes, wires HID
      key-press → `Daemon.press`). The daemon needed **zero** changes; `cli.py`
      gained a `--deck` flag that swaps `VirtualDeck` for it. Unit-tested against
      a `FakeDeck` (9 tests) and verified live end-to-end (state lifecycle +
      physical press reached the daemon).

### Remaining hardware polish (optional, deck-in-hand)
- [x] **Labels on keys — DONE 2026-07-21.** Keys show the **git branch**, single
      line, no wrap, no ellipsis, hard-capped at 7 chars (`format_branch_label`:
      last `/`-segment, clipped — `feat/1234-auth` → `1234-au`). The
      `SessionStart` hook resolves it via `git rev-parse --abbrev-ref HEAD` and
      sends a `branch` field; the model prefers it over the repo basename. Size
      calibrated on the physical deck (`draw_label` auto-fits one line). Falls
      back to the repo basename (same clip) outside a repo / on detached HEAD.
- [x] **Animation — DONE 2026-07-21.** ATTENTION keys pulse on the physical
      deck: a background ticker (`streamdeckd/animation.py`, ~12 fps) breathes
      the fill ~1.3 s/cycle down to 25 % brightness. Depth tuned live. The
      virtual deck stays static (files can't breathe). `--no-animate` disables.
- [x] **Auto-detect at startup — DONE 2026-07-21.** `streamdeckd` (no flag)
      tries `StreamDeckRenderer.open_first()` and falls back to VirtualDeck.
      `--deck` forces hardware (errors if absent); `--virtual` forces the file
      deck.

---

## Decisions I need from you (not blocking, but shape what I build next)

- [x] ~~**Animation (M5):**~~ **Done** — pulsing "needs you" keys, via a daemon
      render-tick thread. (A "working" spinner is still possible later if wanted.)
- [ ] **tmux for session survival:** UUIDs die when Ghostty restarts. Accept that
      (keys just re-resolve on the next session), or run sessions under tmux so
      they survive? (README open question.)
- [x] ~~**Build the `StreamDeckRenderer` now**~~ **Done** — built and validated
      live against the real board (2026-07-21).
- [ ] **Config file (M5 backlog):** move colors / Ghostty app name / socket path /
      overflow strategy into a config file, or keep them as CLI flags + env vars?

## What I can keep doing headless without you (say the word)

- Animation render-tick loop (if you say yes above) — testable with a fake clock.
- Config-file loading — pure logic, testable.
- A `streamdeckd status`/introspection CLI (dump the live model over the socket).
- A "focus the one session that needs you" master-key action (M5 backlog).
- Daemon restart reconciliation: repopulate keys from `registry.json` on startup.
