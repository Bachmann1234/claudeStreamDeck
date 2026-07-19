# M1 — Hardware smoke test

**Goal:** Prove the 20GAA9902 works with this machine's HID drivers, and that we
can paint a key and read presses — before building anything real.

**Why first:** De-risks the entire project. If the deck won't enumerate or the
HID backend fights macOS, we want to know in 5 minutes, not after building a
daemon.

## Tasks
- [ ] Create a Python virtualenv (`python -m venv .venv`).
- [ ] `pip install streamdeck pillow` and the HID backend
      (`hidapi` — on macOS: `brew install hidapi`, then the `hid`/`libusb`
      backend the library expects). Note exact steps in `docs/setup.md`.
- [ ] Run a script that:
  - [ ] Enumerates connected decks; prints model, serial, key count, key image
        format (expect 15 keys).
  - [ ] Sets panel brightness.
  - [ ] Paints key 0 a solid color and key 1 with text.
  - [ ] Registers a press/release callback that prints the key index.
- [ ] Confirm presses register and images render right-side up / correct size.

## Done when
- The deck is detected, we can light up specific keys, and key presses print to
  the console.

## Notes / gotchas to watch for
- macOS may need Input Monitoring / permissions for HID access.
- Image format is device-specific (size, rotation, flip, BGR vs RGB). The
  library has a `PILHelper` to format images correctly — use it.
- The MK-series may hold the USB device exclusively; make sure Elgato's own
  Stream Deck app is **quit** or it'll grab the device.
