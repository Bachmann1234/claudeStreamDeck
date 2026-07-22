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
import time
from pathlib import Path

from gsm.applescript import DeadSurface, Ghostty
from gsm.manager import Manager, UnknownTag
from gsm.registry import default_home

from .animation import animate_frame, has_animation
from .protocol import ProtocolError, parse_message
from .renderer import Renderer, VirtualDeck
from .state import ApplyResult, KeyState, SessionModel, Slot, appearance_for

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
        animate: bool = True,
        launcher_key: int | None = None,
        launch_command: str | None = None,
        launch_cwd: str | None = None,
        reap: bool = True,
        reap_interval: float = 8.0,
        working_timeout: float = 60.0,
    ):
        self.manager = manager or Manager()
        self.renderer = renderer or VirtualDeck()
        # A launcher key (if any) is reserved so the model never assigns a
        # session to it; the daemon paints it and handles its press itself.
        self.launcher_key = launcher_key
        if self.launcher_key is not None and not (
            0 <= self.launcher_key < self.renderer.key_count
        ):
            self.launcher_key = None  # out of range -> just disable it
        self.launch_command = launch_command
        self.launch_cwd = launch_cwd
        reserved = frozenset() if self.launcher_key is None else frozenset({self.launcher_key})
        self.model = model or SessionModel(self.renderer.key_count, reserved=reserved)
        self._launcher_appearance = appearance_for(KeyState.LAUNCHER)
        if self.model.key_count != self.renderer.key_count:
            raise ValueError(
                "model.key_count and renderer.key_count must match "
                f"({self.model.key_count} != {self.renderer.key_count})"
            )
        self.socket_path = Path(socket_path) if socket_path else default_socket_path()
        self._lock = threading.RLock()
        self._server: socketserver.BaseServer | None = None
        # Animation: only meaningful for a renderer that opts in (the hardware
        # deck; the VirtualDeck's frames are files, so it stays static).
        self._animate = animate and getattr(self.renderer, "animated", False)
        self._anim_stop = threading.Event()
        self._anim_thread: threading.Thread | None = None
        self._started_at = time.monotonic()
        # Reaper: periodically blank keys whose surfaces have died (an abrupt
        # tab/window close fires no SessionEnd), so a key never lingers on a
        # session that's gone.
        self._reap = reap
        self._reap_interval = reap_interval
        self._reap_stop = threading.Event()
        self._reap_thread: threading.Thread | None = None
        # Watchdog: a user *interrupt* fires no hook, so a WORKING key would spin
        # forever. If a session sees no activity for this long, drop it to DONE.
        # 0 disables. Monotonic last-activity per session, stamped in handle_line.
        self._working_timeout = working_timeout
        self._activity: dict[str, float] = {}

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
            self._activity[msg.session_id] = time.monotonic()  # for the watchdog
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
                "no key for session %s — all %d keys held by equal/higher "
                "priority sessions; tracked but unpainted (%d parked)",
                msg.session_id[:8],
                self.model.key_count,
                len(self.model.parked()),
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

    def _base_frame(self) -> list:
        """The current frame from the model, with the launcher key stamped in."""
        keys = self.model.snapshot_keys()
        if self.launcher_key is not None:
            keys[self.launcher_key] = self._launcher_appearance
        return keys

    def _repaint(self) -> None:
        self.renderer.render(self._base_frame())

    # -- animation ---------------------------------------------------------

    def _tick_animation(self, elapsed: float | None = None) -> bool:
        """Render one animated frame if any key is animating. Returns whether it
        did (so the loop can idle when nothing needs motion). ``elapsed`` (secs
        since start) is injectable for tests; otherwise read from the monotonic
        clock."""
        with self._lock:
            keys = self._base_frame()
            if not has_animation(keys):
                return False
            if elapsed is None:
                elapsed = time.monotonic() - self._started_at
            self.renderer.render(animate_frame(keys, elapsed))
            return True

    def _animation_loop(self) -> None:
        """Background ticker: ~12 fps while a key animates, a lazy poll otherwise
        so a newly-active key starts moving within a fraction of a second."""
        active_dt, idle_dt = 1 / 12, 0.2
        while not self._anim_stop.is_set():
            try:
                animating = self._tick_animation()
            except Exception:  # pragma: no cover - a paint hiccup must not kill the loop
                log.exception("animation tick failed")
                animating = False
            if self._anim_stop.wait(active_dt if animating else idle_dt):
                return

    def _start_animation(self) -> None:
        if not self._animate or self._anim_thread is not None:
            return
        self._anim_stop.clear()
        self._anim_thread = threading.Thread(
            target=self._animation_loop, name="streamdeckd-animator", daemon=True
        )
        self._anim_thread.start()
        log.info("animation ticker started (pulsing 'needs you' keys)")

    def _stop_animation(self) -> None:
        self._anim_stop.set()
        thread, self._anim_thread = self._anim_thread, None
        if thread is not None:
            thread.join(timeout=1.0)

    # -- reaper: blank keys for dead surfaces ------------------------------

    def _reap_dead_surfaces(self) -> int:
        """Blank keys whose bound surface is no longer alive. Returns the count.

        Conservative on purpose: it only reaps a session when Ghostty is
        confirmed running *and* that session's UUID is absent from the live
        surface list — a positive "this one is gone", never an assumption. If
        Ghostty can't be reached (quit, or a permission hiccup) it does nothing,
        so a transient blip can't wrongly blank live sessions. Sessions with no
        resolved UUID yet are left alone (nothing to check)."""
        ghostty = self.manager.ghostty
        try:
            if not ghostty.is_running():
                return 0  # can't confirm liveness without launching Ghostty -> skip
            live = {t.uuid for t in ghostty.list_terminals()}
        except Exception:
            return 0  # transient query failure -> don't reap this cycle
        with self._lock:
            dead = [s.session_id for s in self.model.slots()
                    if s.uuid and s.uuid not in live]
            for sid in dead:
                self.model.remove(sid)
                self.manager.release(sid)
            if dead:
                self._repaint()
        if dead:
            log.info("reaper: blanked %d key(s) whose surfaces are gone", len(dead))
        return len(dead)

    def _downgrade_stale_working(self) -> int:
        """Drop any WORKING key with no activity for ``working_timeout`` to DONE.
        This is the only cleanup for a user *interrupt*, which fires no hook.
        Returns how many keys were downgraded."""
        if not self._working_timeout:
            return 0
        now = time.monotonic()
        changed = []
        with self._lock:
            for slot in self.model.slots():
                if slot.state is KeyState.WORKING:
                    last = self._activity.get(slot.session_id, self._started_at)
                    if now - last > self._working_timeout and self.model.force_state(
                        slot.session_id, KeyState.DONE
                    ):
                        changed.append(slot.session_id)
            if changed:
                self._repaint()
        if changed:
            log.info("watchdog: %d stale 'working' key(s) -> done (idle > %.0fs)",
                     len(changed), self._working_timeout)
        return len(changed)

    def _reconcile_loop(self) -> None:
        """Background maintenance tick: the working-state watchdog, then the
        dead-surface reaper. Only queries Ghostty when a key is bound to a UUID —
        an idle deck costs nothing."""
        while not self._reap_stop.wait(self._reap_interval):
            try:
                self._downgrade_stale_working()
                if self._reap:
                    with self._lock:
                        has_bound = any(s.uuid for s in self.model.slots())
                    if has_bound:
                        self._reap_dead_surfaces()
            except Exception:  # pragma: no cover - a hiccup must not kill the loop
                log.exception("maintenance tick failed")

    def _start_reaper(self) -> None:
        if self._reap_thread is not None:
            return
        if not self._reap and not self._working_timeout:
            return  # nothing for the maintenance loop to do
        self._reap_stop.clear()
        self._reap_thread = threading.Thread(
            target=self._reconcile_loop, name="streamdeckd-maintenance", daemon=True
        )
        self._reap_thread.start()
        bits = []
        if self._working_timeout:
            bits.append(f"idle 'working' -> done after {self._working_timeout:.0f}s")
        if self._reap:
            bits.append("blank keys for closed surfaces")
        log.info("maintenance started (%s; every %.0fs)",
                 ", ".join(bits), self._reap_interval)

    def _stop_reaper(self) -> None:
        self._reap_stop.set()
        thread, self._reap_thread = self._reap_thread, None
        if thread is not None:
            thread.join(timeout=1.0)

    # -- keypress -> focus -------------------------------------------------

    def press(self, key_index: int) -> Slot | None:
        """Focus the session bound to a key. Returns the slot, or ``None`` if
        the key is blank or its surface has died (in which case the key is
        released and repainted). Pressing the launcher key spawns a new
        session instead (returns ``None``)."""
        if key_index == self.launcher_key:
            self._launch()
            return None
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

    def _launch(self) -> None:
        """Open a new session for the user to work in.

        Default (plain shell): a **new tab** in Ghostty's front window if one is
        open, else a new window. A configured ``launch_command`` always gets its
        own window (a Cmd-T tab can't carry a command). Runs off the lock — it
        only touches Ghostty, not the model — so a slow spawn never blocks state
        updates. The new session registers its own key when its ``SessionStart``
        hook fires; the daemon doesn't track it here."""
        ghostty = self.manager.ghostty
        if not self.launch_command:
            try:
                if ghostty.has_open_window():
                    ghostty.open_new_tab()
                    log.info("launcher: opened a new tab in Ghostty")
                    return
            except Exception:
                log.info("launcher: couldn't open a tab (grant Accessibility to "
                         "enable tabs) — opening a new window instead")
        try:
            uuid = ghostty.spawn_window(
                command=self.launch_command, working_directory=self.launch_cwd
            )
            log.info("launcher: opened a new window (%s)%s -> surface %s",
                     self.launch_command or "shell",
                     f" in {self.launch_cwd}" if self.launch_cwd else "",
                     (uuid or "?")[:8])
        except Exception:  # pragma: no cover - never crash the press/HID thread
            log.exception("launcher: failed to open a new window")

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
        self._start_animation()
        self._start_reaper()
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
        self._stop_animation()
        self._stop_reaper()
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
