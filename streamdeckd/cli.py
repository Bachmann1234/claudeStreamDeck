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
        description="Headless Claude Code Stream Deck daemon (virtual deck).",
    )
    p.add_argument(
        "--socket",
        default=None,
        help=f"unix socket path (default: {default_socket_path()})",
    )
    p.add_argument("--keys", type=int, default=15, help="number of keys (default 15)")
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


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    out_dir = args.out_dir if args.out_dir is not None else default_virtualdeck_dir()
    renderer = VirtualDeck(
        key_count=args.keys,
        out_dir=out_dir,
        write_png=not args.no_png,
    )
    daemon = Daemon(
        manager=Manager(ghostty=Ghostty(args.target)),
        renderer=renderer,
        socket_path=args.socket,
    )
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
