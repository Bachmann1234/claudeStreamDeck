# claudeStreamDeck

Turn an Elgato Stream Deck into a live Claude Code session manager.

Each running Claude Code session claims a key on the deck. The key's color
shows what that session is doing (working, needs you, done). Pressing a key
jumps your terminal straight to that session — window raised, tab selected,
split focused. No deck attached? The same daemon paints a file-backed
**virtual deck** you can watch as JSON + PNGs.

Works against **stock Ghostty ≥ 1.3.0** on macOS — no terminal fork, no tmux.

![The deck live: two sessions working (teal, spinner), one waiting on a human
(coral ?), and the + launcher key](./docs/images/deck-live.jpg)

## How it works

```
┌───────────────────┐  JSON lines over a     ┌──────────────────────┐
│ Claude Code hooks │  unix socket           │ streamdeckd (daemon) │
│ (one reporter     │ ─────────────────────> │  • session → key     │
│  script, wired to │                        │  • paints the deck   │
│  every lifecycle  │                        │  • on key press:     │
│  event)           │                        │    AppleScript focus │
└───────────────────┘                        │    by surface UUID   │
                                             └──────────────────────┘
```

- **The hook** (`claudestreamdeck-hook`) runs on each Claude Code lifecycle
  event and writes one JSON line to `~/.claudeStreamDeck/streamdeckd.sock`.
  On `SessionStart` it also resolves *which Ghostty surface the session lives
  in* — the focused surface, cross-checked against the session's cwd (stock
  Ghostty exposes no `tty`/`pid` over AppleScript, so this is the reliable
  route; see [`docs/correlation-rationale.md`](./docs/correlation-rationale.md)).
  If that was ambiguous, it re-resolves on your next prompt.
- **The daemon** (`streamdeckd`) owns the USB device (or the virtual deck),
  allocates a key per session, repaints on every state change, and on a key
  press focuses that session's surface via `focus terminal id "<uuid>"`.
  Background maintenance keeps the deck honest: a **reaper** blanks keys whose
  surfaces were closed, and a **watchdog** clears a "working" key after a user
  interrupt (which fires no hook).
- **When the deck is full**, an urgent session evicts the least-recently-active
  lower-priority one (ATTENTION > WORKING > STARTING > DONE); parked sessions
  get a key back as soon as one frees up.

## State → key mapping

| Claude Code hook                 | Meaning                         | Key appearance      |
|----------------------------------|---------------------------------|---------------------|
| `SessionStart`                   | claim a free key                | cream, branch label |
| `UserPromptSubmit`, `PreToolUse` | working                         | teal + spinner      |
| `Notification`                   | needs you (question/permission) | coral, blinking `?` |
| `Stop`                           | response finished / done        | amber               |
| `SessionEnd`                     | release the key                 | blank               |

A `Notification` only lights the `?` when it actually needs a human
(permission prompt, question); idle-waiting notifications show `done` instead.

## Hardware

- Elgato Stream Deck (tested: the Original 15-key 3×5 board, model 20GAA9902).
  It's a plain USB HID device, driven directly via
  [python-elgato-streamdeck](https://github.com/abcminiuser/python-elgato-streamdeck)
  — quit the Elgato app first; it holds the device exclusively.
- Entirely optional: without hardware the daemon writes `snapshot.json`,
  per-key PNGs, and a composite `deck.png` to `~/.claudeStreamDeck/virtualdeck/`.

## Setup

The short version — the full guide (flags, hardware, launchd, troubleshooting)
is [`docs/setup.md`](./docs/setup.md).

```bash
git clone git@github.com:Bachmann1234/claudeStreamDeck.git && cd claudeStreamDeck
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest                    # the whole suite runs against fakes — no live Ghostty

# physical deck (optional):
brew install hidapi
pip install -e '.[deck]'

streamdeckd               # auto-detects a deck, falls back to the virtual one
```

Then wire the hooks: merge
[`hooks/settings.snippet.json`](./hooks/settings.snippet.json) into
`~/.claude/settings.json`, replacing the placeholder path with the output of
`which claudestreamdeck-hook`. Start a Claude Code session in Ghostty and watch
a key light up.

One-time macOS grants:

- **Automation (TCC)** — prompted on first use, once for the hook and once for
  the daemon. Until approved, keys still paint; only focus is unavailable.
- **Accessibility** — only if you want the `+` launcher key to open *tabs*
  (it synthesizes `Cmd-T`); otherwise it opens windows, no grant needed.

To run the daemon at login, install the launchd template in
[`service/`](./service/) (see `docs/setup.md` §9).

## CLI tools

- `streamdeckd` — the daemon. See `streamdeckd --help` for flags (launcher key,
  working timeout, brightness, virtual-deck output, …).
- `gsm` — spawn/focus/adopt/status for tagged Ghostty sessions from the shell,
  sharing the same registry (`gsm status --watch` is a handy live view).

## Requirements & limits

- macOS with Ghostty ≥ 1.3.0 and `macos-applescript` enabled (the default).
- Cross-Space focus needs the default Dock setting "when switching to an
  application, switch to a Space with open windows" left **on**;
  native-fullscreen windows on their own Space won't focus across Spaces.
- A user interrupt (Esc) fires no hook, so the watchdog clears that key's
  spinner after `--working-timeout` seconds (default 60).

## More docs

- [`docs/setup.md`](./docs/setup.md) — full setup and troubleshooting.
- [`docs/correlation-rationale.md`](./docs/correlation-rationale.md) — how a
  session finds its Ghostty surface, and the alternatives that were rejected.
- [`docs/tier0-validation-findings.md`](./docs/tier0-validation-findings.md) —
  what stock Ghostty actually exposes over AppleScript. **Read this before
  touching the AppleScript layer.**
- [`milestones/`](./milestones/) and
  [`ghostty-focus-plan.md`](./ghostty-focus-plan.md) — the original roadmap and
  focus-API research, kept for provenance.
