"""The daemon: socket ingest -> state model -> renderer, and press -> focus.

``streamdeckd`` owns three things a hook must never touch: the (future) USB
device, the in-memory :class:`~streamdeckd.state.SessionModel`, and the gsm
:class:`~gsm.Manager` used to focus a surface. Hooks only ever write JSON lines
to the socket; all mutation happens here, serialized behind one lock.

Transport is a unix stream socket at ``~/.claudeStreamDeck/streamdeckd.sock``.
Each connection is one or more newline-delimited JSON messages
(:mod:`streamdeckd.protocol`). A message with a ``"press"`` integer is a control
command (focus that key) — the path a physical Stream Deck's HID callback will
call directly in M1, exposed on the socket so the headless build is drivable and
testable without hardware.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import socketserver
import threading
from pathlib import Path

from gsm.applescript import DeadSurface, Ghostty
from gsm.manager import Manager, UnknownTag
from gsm.registry import default_home

from .protocol import ProtocolError, parse_message
from .renderer import Renderer, VirtualDeck
from .state import ApplyResult, SessionModel, Slot

log = logging.getLogger("streamdeckd")


def default_socket_path() -> Path:
    return default_home() / "streamdeckd.sock"


def default_virtualdeck_dir() -> Path:
    return default_home() / "virtualdeck"


class Daemon:
    """Wires a :class:`SessionModel`, a :class:`Renderer`, and a gsm
    :class:`Manager`. Construct with fakes in tests; call
    :meth:`handle_line` / :meth:`press` directly, or :meth:`serve_forever` to
    run the real socket loop."""

    def __init__(
        self,
        *,
        manager: Manager | None = None,
        renderer: Renderer | None = None,
        model: SessionModel | None = None,
        socket_path: Path | str | None = None,
    ):
        self.manager = manager or Manager()
        self.renderer = renderer or VirtualDeck()
        self.model = model or SessionModel(self.renderer.key_count)
        if self.model.key_count != self.renderer.key_count:
            raise ValueError(
                "model.key_count and renderer.key_count must match "
                f"({self.model.key_count} != {self.renderer.key_count})"
            )
        self.socket_path = Path(socket_path) if socket_path else default_socket_path()
        self._lock = threading.RLock()
        self._server: socketserver.BaseServer | None = None

    # -- message handling --------------------------------------------------

    def handle_line(self, line: str) -> ApplyResult | None:
        """Process one socket line. Never raises on bad input — a broken hook
        must not take the daemon down."""
        text = line.strip()
        if not text:
            return None

        # Peek for a control command before treating the line as a state report.
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            log.warning("dropping non-JSON line: %r", text[:120])
            return None
        if isinstance(obj, dict) and "press" in obj:
            self._handle_press_command(obj)
            return None

        try:
            msg = parse_message(text)
        except ProtocolError as e:
            log.warning("dropping bad message: %s", e)
            return None

        with self._lock:
            result = self.model.apply(msg)
            self._mirror_registry(result)
            self._repaint()
        log.info(
            "%s session=%s key=%s state=%s",
            result.action,
            msg.session_id[:8],
            result.slot.key_index if result.slot else "-",
            result.slot.state.value if result.slot else "-",
        )
        if result.action == "overflow":
            log.warning(
                "no free key for session %s — %d keys all in use; session "
                "tracked but unpainted",
                msg.session_id[:8],
                self.model.key_count,
            )
        return result

    def _handle_press_command(self, obj: dict) -> None:
        try:
            key_index = int(obj["press"])
        except (TypeError, ValueError):
            log.warning("bad press command: %r", obj)
            return
        self.press(key_index)

    def _mirror_registry(self, result: ApplyResult) -> None:
        """Keep the gsm registry in step with the model so focus/prune work."""
        slot = result.slot
        if slot is None:
            return
        if result.released:
            self.manager.release(slot.session_id)
        elif slot.uuid:
            self.manager.bind(
                slot.session_id,
                slot.uuid,
                tty=slot.tty,
                working_directory=slot.cwd,
            )

    def _repaint(self) -> None:
        self.renderer.render(self.model.snapshot_keys())

    # -- keypress -> focus -------------------------------------------------

    def press(self, key_index: int) -> Slot | None:
        """Focus the session bound to a key. Returns the slot, or ``None`` if
        the key is blank or its surface has died (in which case the key is
        released and repainted)."""
        with self._lock:
            slot = self.model.session_for_key(key_index)
            if slot is None:
                log.debug("press on blank key %s", key_index)
                return None
            if not slot.uuid:
                log.info(
                    "press on key %s (session %s) has no resolved uuid — "
                    "cannot focus",
                    key_index,
                    slot.session_id[:8],
                )
                return slot
            self.manager.bind(
                slot.session_id, slot.uuid, tty=slot.tty, working_directory=slot.cwd
            )
            try:
                self.manager.focus(slot.session_id)
            except DeadSurface:
                log.info("surface for session %s died; releasing key %s",
                         slot.session_id[:8], key_index)
                self.model.remove(slot.session_id)
                self.manager.release(slot.session_id)
                self._repaint()
                return None
            except UnknownTag:
                # Registry lost it between bind and focus — treat as dead.
                self.model.remove(slot.session_id)
                self._repaint()
                return None
            return slot

    # -- socket server -----------------------------------------------------

    def serve_forever(self, *, install_signal_handlers: bool = True) -> None:
        """Open the socket and block, painting the initial (blank) frame first."""
        self._prepare_socket_path()
        daemon = self

        class _Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                for raw in self.rfile:  # newline-delimited
                    daemon.handle_line(raw.decode("utf-8", "replace"))

        server = _ThreadingUnixServer(str(self.socket_path), _Handler)
        os.chmod(self.socket_path, 0o600)  # only this user may talk to the deck
        self._server = server

        if install_signal_handlers:
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, lambda *_: self.shutdown())

        with self._lock:
            self._repaint()  # blank frame so a reader sees a coherent deck
        log.info("streamdeckd listening on %s (%d keys)",
                 self.socket_path, self.model.key_count)
        try:
            server.serve_forever()
        finally:
            self._teardown_socket()

    def shutdown(self) -> None:
        """Stop the server loop (safe to call from a signal handler)."""
        if self._server is not None:
            # serve_forever() returns; its finally block blanks + unlinks.
            threading.Thread(target=self._server.shutdown, daemon=True).start()

    def close(self) -> None:
        """Blank the deck and release the renderer."""
        try:
            self.renderer.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            log.exception("error blanking renderer on close")

    # -- socket housekeeping ----------------------------------------------

    def _prepare_socket_path(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            # A leftover socket from a crash, or another live daemon?
            if self._socket_is_live():
                raise RuntimeError(
                    f"another streamdeckd is already listening on {self.socket_path}"
                )
            self.socket_path.unlink()

    def _socket_is_live(self) -> bool:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(str(self.socket_path))
            return True
        except OSError:
            return False
        finally:
            s.close()

    def _teardown_socket(self) -> None:
        try:
            if self._server is not None:
                self._server.server_close()
        finally:
            self.close()
            try:
                self.socket_path.unlink()
            except FileNotFoundError:
                pass


class _ThreadingUnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True
