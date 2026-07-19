"""Tier 0 manager core: spawn / focus / adopt / status over a tag registry.

This is the piece milestones M2 and M4 will import into ``streamdeckd``. It ties
the persistent :class:`~gsm.registry.Registry` to the live Ghostty state via
:class:`~gsm.applescript.Ghostty`, and it is the single place that knows how to
recover from a surface dying (prune the tag) and how adoption degrades on a
Ghostty that doesn't expose ``tty``.
"""

from __future__ import annotations

from dataclasses import dataclass

from .applescript import DeadSurface, Ghostty, TtyUnsupported
from .registry import Registry, Session


class ManagerError(RuntimeError):
    pass


class UnknownTag(ManagerError):
    pass


class AdoptFailed(ManagerError):
    pass


@dataclass
class SessionStatus:
    """A registry session joined with its current live state."""

    session: Session
    alive: bool
    focused: bool
    title: str | None = None
    working_directory: str | None = None


@dataclass
class StatusReport:
    ghostty_running: bool
    app_frontmost: bool
    focused_uuid: str | None
    sessions: list[SessionStatus]


class Manager:
    def __init__(self, ghostty: Ghostty | None = None, registry: Registry | None = None):
        self.ghostty = ghostty or Ghostty()
        self.registry = registry or Registry()

    # -- spawn -------------------------------------------------------------

    def spawn(
        self,
        tag: str,
        *,
        command: str | None = None,
        working_directory: str | None = None,
        env: dict[str, str] | None = None,
        keep_open: bool = False,
    ) -> Session:
        """Spawn a tagged session and persist ``tag -> uuid``.

        A ``CC_SESSION`` env var is injected so the session can self-identify
        (useful once hooks exist in M3). Overwriting an existing tag is allowed
        (last-writer-wins, matching the plan's §6 duplicate-tag guidance).
        """
        merged_env = dict(env or {})
        merged_env.setdefault("CC_SESSION", tag)
        uuid = self.ghostty.spawn_window(
            command=command,
            working_directory=working_directory,
            env=merged_env,
            keep_open=keep_open,
        )
        session = Session(
            tag=tag,
            uuid=uuid,
            source="spawned",
            working_directory=working_directory,
            command=command,
        )
        self.registry.upsert(session)
        return session

    # -- bind / release ----------------------------------------------------

    def bind(
        self,
        tag: str,
        uuid: str,
        *,
        source: str = "adopted",
        tty: str | None = None,
        working_directory: str | None = None,
    ) -> Session:
        """Persist ``tag -> uuid`` *without* a liveness check.

        Unlike :meth:`adopt`, this trusts a caller that already resolved the
        UUID itself (the M3 hook does this via the OSC title-sentinel trick, so
        by the time the daemon binds, no AppleScript round-trip is needed and a
        transient poll miss can't reject a genuinely live surface). Focus still
        goes through :meth:`focus`, which prunes on a dead surface — so a stale
        UUID is corrected at press time, not bind time.
        """
        session = Session(
            tag=tag,
            uuid=uuid,
            source=source,
            tty=tty,
            working_directory=working_directory,
        )
        self.registry.upsert(session)
        return session

    def release(self, tag: str) -> bool:
        """Drop ``tag`` from the registry (the surface itself is never closed)."""
        return self.registry.remove(tag)

    # -- focus -------------------------------------------------------------

    def focus(self, tag: str) -> Session:
        """Focus the surface bound to ``tag``.

        On a dead surface the tag is pruned from the registry and
        :class:`DeadSurface` is re-raised so callers (a Stream Deck key) can
        blank themselves.
        """
        session = self.registry.get(tag)
        if session is None:
            raise UnknownTag(tag)
        try:
            self.ghostty.focus(session.uuid)
        except DeadSurface:
            self.registry.remove(tag)
            raise
        self.registry.touch_focused(tag)
        return session

    # -- adopt -------------------------------------------------------------

    def adopt(
        self,
        tag: str,
        *,
        uuid: str | None = None,
        tty: str | None = None,
        cwd: str | None = None,
        title_contains: str | None = None,
    ) -> Session:
        """Register a session the manager did not spawn.

        Exactly one selector must be given. ``tty`` is the plan's intended
        mechanism but is **unsupported on stock Ghostty 1.3.1** (the dictionary
        has no ``tty`` property); it raises :class:`AdoptFailed` with a clear
        message there, and starts working automatically on a Ghostty that
        exposes ``tty``. ``uuid`` / ``cwd`` / ``title_contains`` work today.
        """
        selectors = {
            "uuid": uuid,
            "tty": tty,
            "cwd": cwd,
            "title_contains": title_contains,
        }
        given = {k: v for k, v in selectors.items() if v is not None}
        if len(given) != 1:
            raise AdoptFailed(
                "adopt needs exactly one of: --uuid, --tty, --cwd, --title-contains"
            )

        resolved: str | None
        if uuid is not None:
            if not self.ghostty.terminal_exists(uuid):
                raise AdoptFailed(f"no live terminal with id {uuid!r}")
            resolved = uuid
        elif tty is not None:
            try:
                resolved = self.ghostty.resolve_by_tty(tty)
            except TtyUnsupported as e:
                raise AdoptFailed(
                    "this Ghostty does not expose `tty` over AppleScript "
                    "(confirmed on 1.3.1 stable). Adopt by --uuid, --cwd, or "
                    "--title-contains instead. See docs/tier0-validation-findings.md."
                ) from e
        elif cwd is not None:
            resolved = self.ghostty.resolve_by_working_directory(cwd)
        else:
            resolved = self.ghostty.resolve_by_title_contains(title_contains or "")

        if not resolved:
            raise AdoptFailed(f"no live terminal matched for tag {tag!r}")

        session = Session(
            tag=tag,
            uuid=resolved,
            source="adopted",
            tty=tty,
            working_directory=cwd,
        )
        self.registry.upsert(session)
        return session

    # -- status ------------------------------------------------------------

    def status(self, *, prune: bool = False) -> StatusReport:
        """Join the registry with live Ghostty state (a single poll).

        Cheap enough for the 1–2 Hz polling the plan recommends; do not call it
        faster than that (Apple-event latency, plan risk #4).
        """
        running = self.ghostty.is_running()
        sessions = self.registry.all()

        if not running:
            statuses = [
                SessionStatus(session=s, alive=False, focused=False)
                for s in sessions.values()
            ]
            return StatusReport(
                ghostty_running=False,
                app_frontmost=False,
                focused_uuid=None,
                sessions=statuses,
            )

        live = {t.uuid: t for t in self.ghostty.list_terminals()}
        focused_uuid = self.ghostty.focused_terminal_id()
        app_frontmost = self.ghostty.frontmost()

        statuses: list[SessionStatus] = []
        dead_tags: list[str] = []
        for tag, s in sessions.items():
            term = live.get(s.uuid)
            alive = term is not None
            if not alive:
                dead_tags.append(tag)
            statuses.append(
                SessionStatus(
                    session=s,
                    alive=alive,
                    focused=alive and s.uuid == focused_uuid,
                    title=term.title if term else None,
                    working_directory=term.working_directory if term else None,
                )
            )

        if prune:
            for tag in dead_tags:
                self.registry.remove(tag)
            statuses = [st for st in statuses if st.session.tag not in dead_tags]

        return StatusReport(
            ghostty_running=True,
            app_frontmost=app_frontmost,
            focused_uuid=focused_uuid,
            sessions=statuses,
        )
