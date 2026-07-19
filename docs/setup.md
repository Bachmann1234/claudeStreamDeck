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
| `--out-dir DIR`   | `~/.claudeStreamDeck/virtualdeck`         | where the virtual deck is written        |
| `--keys N`        | `15`                                      | key count (the 20GAA9902 is a 3×5 = 15)  |
| `--no-png`        | off                                       | write only `snapshot.json`, skip PNGs    |
| `--target NAME`   | `Ghostty`                                 | Ghostty app name/path for focus          |
| `-v`              | off                                       | debug logging                            |

The daemon refuses to start if another instance is already listening on the
socket, and cleans up a stale socket file left by a crash.

## 3. Watch the virtual deck

Every state change rewrites the output directory:

```bash
# live text view of all 15 keys
watch -n1 'cat ~/.claudeStreamDeck/virtualdeck/snapshot.json'

# or open the per-key PNGs
open ~/.claudeStreamDeck/virtualdeck/key_00.png
```

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

On `SessionStart` the hook resolves its own Ghostty surface UUID (via a title
sentinel — full rationale in [`correlation-rationale.md`](./correlation-rationale.md))
and reports it, so a key press can focus the exact surface. Two things to know:

- **Automation (TCC) prompt.** The first Apple event each process sends triggers
  a one-time macOS "allow control of Ghostty" prompt — once for the hook (at the
  first `SessionStart`), once for the daemon (at the first key press you
  trigger). Approve both. Until approved, the deck still lights up correctly;
  only focus is unavailable.
- **`macos-applescript` must stay enabled** in Ghostty (it is by default).

## 7. Environment variables

| Variable              | Effect                                                         |
|-----------------------|----------------------------------------------------------------|
| `STREAMDECKD_SOCKET`  | override the socket path (hook + daemon must agree)            |
| `STREAMDECKD_GHOSTTY` | Ghostty app name/path the **hook** resolves against            |
| `GSM_HOME`            | move `~/.claudeStreamDeck` (registry, socket, virtual deck)    |

## 8. Shut down

`Ctrl-C` (or `SIGTERM`) blanks the deck, closes the renderer, and unlinks the
socket cleanly.

## Troubleshooting

- **Keys never change.** Is the daemon running and pointed at the same socket as
  the hook? Check `STREAMDECKD_SOCKET`/`GSM_HOME` match on both sides. Run
  `streamdeckd -v` and watch the log as you send a line.
- **Keys light up but pressing does nothing.** The session had no resolved UUID
  (grep `snapshot.json` for `"uuid"` via the registry, or run `gsm status`).
  Usually the Automation grant for the hook is missing, or Ghostty wasn't
  running when the session started. Focus works once a UUID is known.
- **`AF_UNIX path too long`.** The socket path exceeds macOS's 104-byte limit —
  keep `GSM_HOME` short.
