"""Command-line front end for the Tier 0 Ghostty session manager.

    gsm spawn  <tag> [--command ...] [--cwd ...] [--env K=V ...] [--keep-open]
    gsm focus  <tag>
    gsm adopt  <tag> (--uuid U | --tty T | --cwd P | --title-contains S)
    gsm status [--prune] [--watch [SECONDS]]

Add ``--json`` before the subcommand for machine-readable output (the shape the
M2 daemon will consume).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict

from .applescript import DeadSurface, Ghostty, GhosttyNotRunning, GhosttyScriptError
from .manager import AdoptFailed, Manager, StatusReport, UnknownTag


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gsm",
        description="Tier 0 Ghostty Claude-session manager (spawn/focus/adopt/status).",
    )
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument(
        "--target",
        default="Ghostty",
        help='app name or absolute .app path to address (default: "Ghostty")',
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("spawn", help="spawn a tagged session")
    sp.add_argument("tag")
    sp.add_argument("--command", help="command to run instead of the shell")
    sp.add_argument("--cwd", help="initial working directory")
    sp.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="K=V",
        help="environment variable (repeatable)",
    )
    sp.add_argument(
        "--keep-open",
        action="store_true",
        help="keep the surface open after the command exits",
    )

    fp = sub.add_parser("focus", help="focus a tagged session")
    fp.add_argument("tag")

    ap = sub.add_parser("adopt", help="register an existing session")
    ap.add_argument("tag")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--uuid", help="Ghostty surface UUID (always works)")
    g.add_argument("--tty", help="tty path (needs a Ghostty that exposes tty)")
    g.add_argument("--cwd", help="match a live terminal by working directory")
    g.add_argument("--title-contains", help="match a live terminal by title substring")

    stp = sub.add_parser("status", help="list known sessions and focus state")
    stp.add_argument("--prune", action="store_true", help="drop dead sessions")
    stp.add_argument(
        "--watch",
        nargs="?",
        type=float,
        const=1.0,
        default=None,
        metavar="SECONDS",
        help="poll continuously (default 1.0s; keep >=0.5s per plan risk #4)",
    )
    return p


def _emit(obj, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, default=lambda o: asdict(o) if hasattr(o, "__dataclass_fields__") else str(o)))


def _print_status(report: StatusReport, as_json: bool) -> None:
    if as_json:
        _emit(report, True)
        return
    if not report.ghostty_running:
        print("Ghostty: not running")
    else:
        front = "frontmost" if report.app_frontmost else "background"
        print(f"Ghostty: running ({front}); focused surface: {report.focused_uuid or '-'}")
    if not report.sessions:
        print("  (no sessions tracked)")
        return
    for st in sorted(report.sessions, key=lambda s: s.session.tag):
        s = st.session
        mark = "*" if st.focused else ("+" if st.alive else "x")
        title = st.title or s.working_directory or s.command or ""
        title = (title[:40] + "…") if len(title) > 41 else title
        print(
            f"  [{mark}] {s.tag:<16} {s.uuid}  "
            f"{s.source:<8} {title}"
        )
    print("  legend: * focused  + alive  x dead")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    manager = Manager(ghostty=Ghostty(args.target))

    try:
        if args.cmd == "spawn":
            env = {}
            for pair in args.env:
                if "=" not in pair:
                    print(f"error: bad --env {pair!r}, expected K=V", file=sys.stderr)
                    return 2
                k, v = pair.split("=", 1)
                env[k] = v
            session = manager.spawn(
                args.tag,
                command=args.command,
                working_directory=args.cwd,
                env=env or None,
                keep_open=args.keep_open,
            )
            if args.json:
                _emit(session, True)
            else:
                print(f"spawned {args.tag} -> {session.uuid}")
            return 0

        if args.cmd == "focus":
            try:
                session = manager.focus(args.tag)
            except UnknownTag:
                print(f"error: unknown tag {args.tag!r}", file=sys.stderr)
                return 4
            except DeadSurface:
                print(
                    f"error: surface for {args.tag!r} is gone; pruned from registry",
                    file=sys.stderr,
                )
                return 5
            if args.json:
                _emit(session, True)
            else:
                print(f"focused {args.tag} -> {session.uuid}")
            return 0

        if args.cmd == "adopt":
            try:
                session = manager.adopt(
                    args.tag,
                    uuid=args.uuid,
                    tty=args.tty,
                    cwd=args.cwd,
                    title_contains=args.title_contains,
                )
            except AdoptFailed as e:
                print(f"error: {e}", file=sys.stderr)
                return 6
            if args.json:
                _emit(session, True)
            else:
                print(f"adopted {args.tag} -> {session.uuid} ({session.source})")
            return 0

        if args.cmd == "status":
            if args.watch is not None:
                interval = max(0.5, args.watch)
                try:
                    while True:
                        report = manager.status(prune=args.prune)
                        if not args.json:
                            print("\x1b[2J\x1b[H", end="")  # clear screen
                        _print_status(report, args.json)
                        sys.stdout.flush()
                        time.sleep(interval)
                except KeyboardInterrupt:
                    return 0
            report = manager.status(prune=args.prune)
            _print_status(report, args.json)
            return 0

    except GhosttyNotRunning:
        print("error: Ghostty is not running", file=sys.stderr)
        return 3
    except GhosttyScriptError as e:
        print(f"error: AppleScript call failed: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
