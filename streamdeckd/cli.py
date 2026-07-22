"""``streamdeckd`` entry point — run the headless daemon.

    streamdeckd [--socket PATH] [--keys N] [--out-dir DIR] [--no-png]
                [--target Ghostty] [-v]

With no physical deck attached this drives a :class:`VirtualDeck`: every key
change is written to ``<out-dir>/snapshot.json`` and ``<out-dir>/key_NN.png``,
so you can watch the deck's state from another terminal
(``watch -n1 cat <out-dir>/snapshot.json``) exactly as if it were hardware.
"""

from __future__ import annotations

import argparse
import logging

from gsm.applescript import Ghostty
from gsm.manager import Manager

from .daemon import Daemon, default_socket_path, default_virtualdeck_dir
from .renderer import VirtualDeck


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="streamdeckd",
        description="Headless Claude Code Stream Deck daemon. Auto-detects a "
        "physical Stream Deck and falls back to a virtual (file-backed) deck.",
    )
    p.add_argument(
        "--socket",
        default=None,
        help=f"unix socket path (default: {default_socket_path()})",
    )
    p.add_argument("--keys", type=int, default=15, help="virtual-deck key count (default 15)")
    # Deck selection: default auto-detects; these two force a choice.
    deck = p.add_mutually_exclusive_group()
    deck.add_argument(
        "--deck",
        action="store_true",
        help="require a physical Stream Deck: error out if none is found "
        "(quit the Elgato app first). Default is to auto-detect and fall back.",
    )
    deck.add_argument(
        "--virtual",
        action="store_true",
        help="force the virtual (file-backed) deck even if hardware is present",
    )
    p.add_argument(
        "--brightness",
        type=int,
        default=60,
        help="physical deck brightness percent (default 60)",
    )
    p.add_argument(
        "--no-animate",
        action="store_true",
        help="disable the pulsing 'needs you' animation on the physical deck",
    )
    p.add_argument(
        "--out-dir",
        default=None,
        help=f"virtual-deck output dir (default: {default_virtualdeck_dir()})",
    )
    p.add_argument(
        "--no-png",
        action="store_true",
        help="write only snapshot.json, skip per-key PNGs",
    )
    p.add_argument(
        "--target",
        default="Ghostty",
        help='Ghostty app name or .app path for focus (default: "Ghostty")',
    )
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return p


def _make_renderer(args, log):
    """Pick a renderer: auto-detect hardware unless forced.

    ``--virtual`` forces the file-backed deck; ``--deck`` requires hardware
    (raises if absent); the default tries the deck and falls back to virtual.
    Returns the renderer, or raises on ``--deck`` with no device attached.
    """
    if not args.virtual:
        from .streamdeck_renderer import StreamDeckRenderer

        try:
            renderer = StreamDeckRenderer.open_first(brightness=args.brightness)
            log.info("using the physical Stream Deck")
            return renderer
        except Exception as e:
            if args.deck:
                raise  # explicit --deck: surface the error, don't fall back
            log.info("no physical Stream Deck (%s); using the virtual deck", e)

    out_dir = args.out_dir if args.out_dir is not None else default_virtualdeck_dir()
    return VirtualDeck(
        key_count=args.keys, out_dir=out_dir, write_png=not args.no_png
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    log = logging.getLogger("streamdeckd")
    try:
        renderer = _make_renderer(args, log)
    except Exception as e:
        log.error("could not open Stream Deck: %s", e)
        return 1
    daemon = Daemon(
        manager=Manager(ghostty=Ghostty(args.target)),
        renderer=renderer,
        socket_path=args.socket,
        animate=not args.no_animate,
    )
    # A physical press must reach the same focus path as {"press": N}.
    if hasattr(renderer, "on_press"):
        renderer.on_press = daemon.press
    try:
        daemon.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover
        pass
    except RuntimeError as e:
        logging.getLogger("streamdeckd").error("%s", e)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
