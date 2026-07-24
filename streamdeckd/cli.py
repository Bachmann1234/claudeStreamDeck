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
from logging.handlers import RotatingFileHandler

from gsm.applescript import Ghostty
from gsm.manager import Manager
from gsm.registry import default_home

from .daemon import Daemon, default_socket_path, default_virtualdeck_dir
from .renderer import VirtualDeck

# Cap the daemon's own log so a long-lived launchd install can't grow it without
# bound (launchd's StandardOutPath is never rotated — that was the 7 MB-in-two-
# days footgun). 2 MB × 3 backups keeps a useful window at a trivial disk cost.
_LOG_MAX_BYTES = 2 * 1024 * 1024
_LOG_BACKUPS = 3


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
    launch = p.add_mutually_exclusive_group()
    launch.add_argument(
        "--launcher-key",
        type=int,
        default=None,
        help="key index reserved as a '+' launcher that opens a new Ghostty "
        "window (default: the last key)",
    )
    launch.add_argument(
        "--no-launcher",
        action="store_true",
        help="don't reserve a launcher key; every key is available to sessions",
    )
    p.add_argument(
        "--launch-command",
        default=None,
        help="command the launcher runs in the new window (default: none — just "
        "a shell, so you can cd and start claude yourself)",
    )
    p.add_argument(
        "--launch-cwd",
        default=None,
        help="working directory for a launched window (default: Ghostty's default)",
    )
    p.add_argument(
        "--no-reap",
        action="store_true",
        help="don't auto-blank keys whose Ghostty surfaces have been closed",
    )
    p.add_argument(
        "--working-timeout",
        type=float,
        default=60.0,
        help="drop a 'working' key to 'done' after this many idle seconds — the "
        "only cleanup for a user interrupt, which fires no hook (0 disables)",
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


def _configure_logging(verbose: bool) -> None:
    """Log to stderr *and* a size-capped rotating file.

    launchd captured the daemon's stderr into ``streamdeckd.log`` but never
    rotated it, so on a long-lived install that file grew unbounded (7 MB in two
    days). Now the full INFO log goes to a size-capped
    :class:`RotatingFileHandler`; stderr (which launchd still captures, into a
    separate boot log) carries only WARNING+ so that file stays tiny while
    startup/crash output — Python tracebacks write to stderr directly — is still
    caught."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)

    # Under launchd (no -v) stderr carries only WARNING+, so routine INFO doesn't
    # flow to the boot log and reintroduce the unbounded-growth bug the rotating
    # file fixes. Running manually with -v, the user wants the full stream on the
    # console, so honor that.
    stream = logging.StreamHandler()
    stream.setLevel(level if verbose else logging.WARNING)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    try:
        log_path = default_home() / "streamdeckd.log"
        log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        rotating = RotatingFileHandler(
            log_path, maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUPS
        )
        rotating.setFormatter(fmt)
        root.addHandler(rotating)
    except OSError:
        # A logging file we can't open must never stop the daemon starting —
        # stderr (which launchd captures) is enough on its own.
        logging.getLogger("streamdeckd").warning(
            "could not open rotating log file; logging to stderr only"
        )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    _configure_logging(args.verbose)

    log = logging.getLogger("streamdeckd")
    try:
        renderer = _make_renderer(args, log)
    except Exception as e:
        log.error("could not open Stream Deck: %s", e)
        return 1
    if args.no_launcher:
        launcher_key = None
    elif args.launcher_key is not None:
        launcher_key = args.launcher_key
    else:
        launcher_key = renderer.key_count - 1  # default: the last key
    daemon = Daemon(
        manager=Manager(ghostty=Ghostty(args.target)),
        renderer=renderer,
        socket_path=args.socket,
        animate=not args.no_animate,
        launcher_key=launcher_key,
        launch_command=args.launch_command,
        launch_cwd=args.launch_cwd,
        reap=not args.no_reap,
        working_timeout=args.working_timeout,
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
