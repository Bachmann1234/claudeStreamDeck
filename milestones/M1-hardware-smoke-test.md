# M1 — Hardware smoke test

**Goal:** Prove the 20GAA9902 works with this machine's HID drivers, and that we
can paint a key and read presses — before building anything real.

**Why first:** De-risks the entire project. If the deck won't enumerate or the
HID backend fights macOS, we want to know in 5 minutes, not after building a
daemon.

## Tasks
- [x] Create a Python virtualenv (`python -m venv .venv`).
- [x] `pip install streamdeck pillow` and the HID backend
      (`hidapi` — on macOS: `brew install hidapi`, then the `hid`/`libusb`
      backend the library expects). Note exact steps in `docs/setup.md`.
      *Both installed; `streamdeck 0.9.8`, `hidapi 0.15.0` (brew). No extra
      backend shim needed — the lib found `libhidapi` on its own.*
- [x] Run a script that:
  - [x] Enumerates connected decks; prints model, serial, key count, key image
        format (expect 15 keys).
  - [x] Sets panel brightness.
  - [x] Paints key 0 a solid color and key 1 with text.
  - [x] Registers a press/release callback that prints the key index.
- [x] Confirm presses register and images render right-side up / correct size.

## Verified live 2026-07-21
Ran against the attached board (`scripts/m1_smoke.py`, then the real daemon):

- **Enumerated:** type reports as **"Stream Deck Original"** (not the box
  label 20GAA9902), 15 keys, layout (3, 5), serial `AL50I2C01764`,
  firmware `1.02.004`.
- **Key image format:** `size=(72, 72)`, `format='JPEG'`, `flip=(True, True)`,
  `rotation=0`. **The keys are 72 px, not the 96 px the VirtualDeck PNGs use** —
  `PILHelper.create_image`/`to_native_format` handle the size + flip, so the
  renderer never hard-codes it.
- **Paint + presses:** brightness set to 60 %; keys painted solid colors and
  centered text; every physical press printed its index (0–8 exercised) and the
  press-to-yellow feedback rendered instantly.
- **No permission wall:** macOS granted HID access without an Input-Monitoring
  prompt. (The Elgato Stream Deck app was confirmed quit first — it holds the
  USB device exclusively.)
- **End-to-end through the daemon:** `streamdeckd --deck` opened the board,
  drove a full session lifecycle over the socket (grey → blue → yellow-ring →
  green → blank), and a **physical key press reached `Daemon.press`** (logged
  "no resolved uuid" only because the sessions were synthetic — the focus path
  itself is wired). See `streamdeckd/streamdeck_renderer.py` + its tests.

## Done when
- The deck is detected, we can light up specific keys, and key presses print to
  the console. *✅ Met — verified live (see above).*

## Notes / gotchas to watch for
- macOS may need Input Monitoring / permissions for HID access.
- Image format is device-specific (size, rotation, flip, BGR vs RGB). The
  library has a `PILHelper` to format images correctly — use it.
- The MK-series may hold the USB device exclusively; make sure Elgato's own
  Stream Deck app is **quit** or it'll grab the device.
