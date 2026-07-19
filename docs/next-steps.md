# Next steps — the human/hardware-gated work

Everything buildable headless is done (M2 daemon, M3 hooks + correlation, M5
overflow/preview/launchd; 112 tests green). What's left needs **you at the
machine** (TCC grants, a real Claude session, visual checks) or the **physical
deck**. Ordered so the earliest items unblock the most.

Respect the destructive-test hazard in `CLAUDE.md` throughout: the manager and
hooks only ever *report* and *focus* — never `close` a surface. Don't spawn or
close throwaway windows near your real sessions.

---

## 1. Live smoke test the hooks + daemon (no hardware needed) ⏱️ ~15 min

This proves M3 end-to-end against real Claude Code in real Ghostty.

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

## 2. Confirm the correlation trick works live ⏱️ ~5 min (part of #1)

The riskiest unproven assumption: that the `SessionStart` hook's OSC
title-sentinel actually resolves the surface UUID (see
`docs/correlation-rationale.md`).

- [ ] After starting a session (step 1), check the mapping was resolved:
      `cat ~/.claudeStreamDeck/registry.json` — the session's entry should have
      a non-null `"uuid"`. (Or `gsm status`.)
- [ ] Watch for title flicker at session start — the sentinel should appear for
      a blink then get replaced by the repo name. If Claude Code clobbers the
      title before the lookup lands, the uuid will be null → tell me and I'll
      tune the retry window or hold the sentinel across attempts.

## 3. Test focus-by-keypress (M4) ⏱️ ~10 min

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

## 6. Hardware bring-up (M1) — needs the Stream Deck plugged in ⏱️ ~30 min

Everything above uses the VirtualDeck; this lights up the real board.

- [ ] **Quit the Elgato Stream Deck app** (it grabs the USB device exclusively).
- [ ] `brew install hidapi`, then `pip install streamdeck` into the venv.
- [ ] Grant **Input Monitoring** to the terminal/daemon if macOS asks.
- [ ] Run the M1 smoke script (enumerate deck → expect model 20GAA9902, 15 keys →
      set brightness → paint key 0 → print press callbacks). See
      `milestones/M1-hardware-smoke-test.md`.
- [ ] Once the deck enumerates, I'll build a `StreamDeckRenderer` behind the
      existing `Renderer` interface (formats the same `KeyAppearance` frames with
      PILHelper, wires HID key-press → `Daemon.press`). The daemon needs **zero**
      changes — just swap `VirtualDeck` for `StreamDeckRenderer` in `cli.py`.

---

## Decisions I need from you (not blocking, but shape what I build next)

- [ ] **Animation (M5):** want pulsing "needs you" / spinner "working" keys? That
      means giving the daemon a background render-tick loop (a thread that
      repaints animated keys a few times a second). Yes / no?
- [ ] **tmux for session survival:** UUIDs die when Ghostty restarts. Accept that
      (keys just re-resolve on the next session), or run sessions under tmux so
      they survive? (README open question.)
- [ ] **Build the `StreamDeckRenderer` now** as an untested-pending-hardware
      skeleton, or wait until the deck is in front of us at step 6?
- [ ] **Config file (M5 backlog):** move colors / Ghostty app name / socket path /
      overflow strategy into a config file, or keep them as CLI flags + env vars?

## What I can keep doing headless without you (say the word)

- Animation render-tick loop (if you say yes above) — testable with a fake clock.
- Config-file loading — pure logic, testable.
- A `streamdeckd status`/introspection CLI (dump the live model over the socket).
- A "focus the one session that needs you" master-key action (M5 backlog).
- Daemon restart reconciliation: repopulate keys from `registry.json` on startup.
