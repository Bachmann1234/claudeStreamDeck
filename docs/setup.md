# Running `streamdeckd` headless (no Stream Deck required)

This is the M2 (headless half) + M3 setup: a long-running daemon that ingests
state from Claude Code hooks and paints a **virtual deck** (a JSON snapshot plus
one PNG per key). No USB hardware is involved — the physical deck (M1) plugs in
behind the same `Renderer` interface later.

## 1. Install

```bash
cd ~/code/claudeStreamDeck
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'      # pulls in Pillow for PNG key rendering
pytest                        # 100+ tests, all with a fake Ghostty — no live app
```

This installs two console scripts into `.venv/bin/`:

- `streamdeckd` — the daemon.
- `claudestreamdeck-hook` — the reporter Claude Code runs on each event.

## 2. Start the daemon

```bash
streamdeckd            # listens on ~/.claudeStreamDeck/streamdeckd.sock
```

Useful flags:

| Flag              | Default                                   | Purpose                                  |
|-------------------|-------------------------------------------|------------------------------------------|
| `--socket PATH`   | `~/.claudeStreamDeck/streamdeckd.sock`    | where hooks connect                      |
| `--deck`          | auto                                      | **require** a physical deck; error if absent (see §2a) |
| `--virtual`       | auto                                      | **force** the virtual deck even if a deck is attached |
| `--brightness N`  | `60`                                      | physical deck brightness %               |
| `--no-animate`    | off                                       | disable the pulsing "needs you" animation |
| `--out-dir DIR`   | `~/.claudeStreamDeck/virtualdeck`         | where the virtual deck is written        |
| `--keys N`        | `15`                                      | virtual-deck key count (hardware reports its own) |
| `--no-png`        | off                                       | write only `snapshot.json`, skip PNGs    |
| `--target NAME`   | `Ghostty`                                 | Ghostty app name/path for focus          |
| `-v`              | off                                       | debug logging                            |

The daemon refuses to start if another instance is already listening on the
socket, and cleans up a stale socket file left by a crash.

## 2a. Drive a real Stream Deck (auto-detected)

By default `streamdeckd` **auto-detects**: if a Stream Deck is attached it drives
the physical board, otherwise it falls back to the virtual (file-backed) deck.
`--deck` forces hardware (erroring if none is found); `--virtual` forces the
file deck even when hardware is present. The daemon logic is identical either
way — only the `Renderer` swaps.

```bash
# 1. Quit the Elgato Stream Deck app first — it holds the USB device
#    exclusively and the daemon will find no deck while it runs.
# 2. Install the HID bits (one time):
brew install hidapi
pip install streamdeck            # into the project venv

streamdeckd -v                    # auto-detects; opens the deck if present,
                                  # paints keys, forwards presses to focus
```

- On the physical deck, `ATTENTION` ("needs you") keys **pulse** — the fill
  breathes ~1.3 s/cycle down to a quarter brightness. `--no-animate` turns it
  off. The virtual deck stays static (a still PNG can't breathe; the white ring
  already reads as attention).
- A **physical key press** takes the exact same path as `{"press": N}` on the
  socket: it focuses that session's Ghostty surface (needs a resolved UUID —
  see §6). No extra wiring.
- Hardware notes for the tested board (Stream Deck Original, 15-key): keys are
  **72×72 JPEG, flipped both axes** — handled automatically by the library's
  `PILHelper`, so nothing downstream hard-codes the size. Presses did **not**
  require an Input-Monitoring grant on the test machine.
- If `--deck` prints `could not open Stream Deck`, the Elgato app is probably
  still running, or the deck is unplugged.

## 3. Watch the virtual deck

Every state change rewrites the output directory:

```bash
# live text view of all 15 keys
watch -n1 'cat ~/.claudeStreamDeck/virtualdeck/snapshot.json'

# glanceable picture of the whole 3×5 board (updates every render)
open ~/.claudeStreamDeck/virtualdeck/deck.png

# or a single key
open ~/.claudeStreamDeck/virtualdeck/key_00.png
```

`deck.png` is a composite of all keys laid out like the physical board, each
tile stamped with its key index (so key `3` maps to `{"press": 3}`).

`snapshot.json` looks like:

```json
{
  "key_count": 15,
  "keys": [
    {"index": 0, "state": "working", "color": [0, 90, 200], "label": "repo-x", "pulse": false},
    {"index": 1, "state": "attention", "color": [235, 185, 0], "label": "api", "pulse": true},
    {"index": 2, "state": "empty", "color": [0, 0, 0], "label": "", "pulse": false}
  ]
}
```

State → color: `starting` dim grey · `working` blue · `attention` pulsing
yellow · `done` green · `empty` black. (See the README table.)

## 4. Drive it by hand (no hooks yet)

The socket speaks newline-delimited JSON. A session's whole life in four lines:

```bash
S=~/.claudeStreamDeck/streamdeckd.sock
printf '{"session_id":"demo","event":"SessionStart","uuid":"","cwd":"'"$PWD"'"}\n' | nc -U "$S"
printf '{"session_id":"demo","event":"UserPromptSubmit"}\n' | nc -U "$S"   # -> blue
printf '{"session_id":"demo","event":"Notification"}\n'    | nc -U "$S"   # -> pulsing yellow
printf '{"session_id":"demo","event":"Stop"}\n'            | nc -U "$S"   # -> green
printf '{"session_id":"demo","event":"SessionEnd"}\n'      | nc -U "$S"   # -> blank
```

Focus a session's Ghostty surface (what a physical key press will do):

```bash
printf '{"press":0}\n' | nc -U "$S"     # focus the surface bound to key 0
```

## 5. Wire up Claude Code hooks

The reporter maps `hook_event_name` → deck state itself, so **one script is
wired to every event**. Copy [`../hooks/settings.snippet.json`](../hooks/settings.snippet.json)
into `~/.claude/settings.json` (or a project `.claude/settings.json`) and
replace the placeholder command path with your venv's script:

```bash
which claudestreamdeck-hook
# -> /Users/you/code/claudeStreamDeck/.venv/bin/claudestreamdeck-hook
```

The hooks are `async` with a short timeout and swallow every error, so a stopped
daemon never slows down or breaks Claude.

Now start a Claude Code session in Ghostty and watch `snapshot.json`: a fresh
session lights a key (dim), it goes blue while working, pulsing yellow when it
needs you, green when done, and blank when you exit.

## 6. Session ↔ surface correlation & the one-time macOS grant

On `SessionStart` the hook resolves its own Ghostty surface UUID (via read-only
`osascript` — the focused surface, cross-checked against cwd; full rationale in
[`correlation-rationale.md`](./correlation-rationale.md)) and reports it, so a
key press can focus the exact surface. The `SessionStart` hook is wired
**synchronously** (no `async`) so it queries while your new window is still
focused. Two things to know:

- **Automation (TCC) prompt.** The first Apple event each process sends triggers
  a one-time macOS "allow control of Ghostty" prompt — once for the hook (at the
  first `SessionStart`), once for the daemon (at the first key press you
  trigger). Approve both. Until approved, the deck still lights up correctly;
  only focus is unavailable.
- **`macos-applescript` must stay enabled** in Ghostty (it is by default).
- **Cross-Space focus needs one Dock setting.** To make a key press pull you to a
  session that's on **another Space**, leave **System Settings → Desktop & Dock →
  "When switching to an application, switch to a Space with open windows"** ON
  (the macOS default). With it off, focus won't switch Spaces — it silently stays
  put. Verified 2026-07-21; details in `docs/tier0-validation-findings.md`.
  (Native-**fullscreen** windows on their own Space are the one case that still
  won't switch regardless — a deferred, niche limitation.)

## 7. Environment variables

| Variable              | Effect                                                         |
|-----------------------|----------------------------------------------------------------|
| `STREAMDECKD_SOCKET`  | override the socket path (hook + daemon must agree)            |
| `STREAMDECKD_GHOSTTY` | Ghostty app name/path the **hook** resolves against            |
| `GSM_HOME`            | move `~/.claudeStreamDeck` (registry, socket, virtual deck)    |

## 8. Shut down

`Ctrl-C` (or `SIGTERM`) blanks the deck, closes the renderer, and unlinks the
socket cleanly.

## 9. Run on login (launchd)

To start `streamdeckd` automatically and keep it alive, install the LaunchAgent
template [`../service/com.claudestreamdeck.streamdeckd.plist`](../service/com.claudestreamdeck.streamdeckd.plist):

```bash
cp service/com.claudestreamdeck.streamdeckd.plist ~/Library/LaunchAgents/
# Edit the copy: replace every /ABSOLUTE/PATH...
#   ProgramArguments  -> your venv's streamdeckd  (`which streamdeckd`)
#   Standard*Path     -> $HOME/.claudeStreamDeck/streamdeckd.log
launchctl load ~/Library/LaunchAgents/com.claudestreamdeck.streamdeckd.plist
```

`RunAtLoad` starts it at login; `KeepAlive` restarts it on crash (throttled to
every 10 s). Logs land in `~/.claudeStreamDeck/streamdeckd.log`. To stop and
remove it:

```bash
launchctl unload ~/Library/LaunchAgents/com.claudestreamdeck.streamdeckd.plist
```

> **Note:** the daemon sends Apple events to focus surfaces, so the first key
> press after install triggers the one-time Automation (TCC) prompt attributed
> to `streamdeckd` — approve it once.

## Troubleshooting

- **Keys never change.** Is the daemon running and pointed at the same socket as
  the hook? Check `STREAMDECKD_SOCKET`/`GSM_HOME` match on both sides. Run
  `streamdeckd -v` and watch the log as you send a line.
- **Keys light up but pressing does nothing.** The session had no resolved UUID.
  Check `~/.claudeStreamDeck/hook.log` — it records each `SessionStart`
  resolution (`… -> uuid='…'` on success, `-> uuid=None` on a miss) — and
  `~/.claudeStreamDeck/registry.json` for the stored mapping. A `None` usually
  means the Automation grant for the hook is missing, or the session was started
  in a non-focused window with another same-cwd session open (see
  `docs/correlation-rationale.md`). Focus works once a UUID is known.
- **`AF_UNIX path too long`.** The socket path exceeds macOS's 104-byte limit —
  keep `GSM_HOME` short.
