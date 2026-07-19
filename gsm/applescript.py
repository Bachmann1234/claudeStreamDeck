"""Thin AppleScript (Apple-event) bridge to Ghostty's scripting dictionary.

Everything the Tier 0 manager knows how to do to Ghostty lives here, expressed
as small `osascript` calls. Verified against the shipped **Ghostty 1.3.1** sdef
(see ``docs/tier0-validation-findings.md``): the ``terminal`` class exposes only
``id``, ``name`` (title) and ``working directory`` — notably **no ``tty`` and no
``pid``** — so correlation of an existing surface is done by uuid / cwd / title,
not tty, on stock 1.3.1.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

# ASCII control chars used as field / record separators when marshalling lists
# out of AppleScript. Chosen because they never appear in a path, title or uuid,
# so parsing is robust even when a terminal title contains commas or quotes.
_US = "\x1f"  # unit separator  -> between fields of one terminal
_RS = "\x1e"  # record separator -> between terminals


class GhosttyScriptError(RuntimeError):
    """An osascript call against Ghostty failed."""

    def __init__(self, message: str, *, code: int | None = None, script: str = ""):
        super().__init__(message)
        self.code = code
        self.script = script


class GhosttyNotRunning(GhosttyScriptError):
    """Ghostty is not running, so there is nothing to talk to."""


class DeadSurface(GhosttyScriptError):
    """The addressed terminal surface no longer exists (was closed)."""


class TtyUnsupported(GhosttyScriptError):
    """This Ghostty's dictionary does not expose ``tty`` (true on 1.3.1 stable).

    Raised when a ``whose tty is`` query hits error -1700 because the property
    is absent. On a Ghostty that *does* expose tty this never fires and the
    tty-based resolvers work unchanged.
    """


@dataclass(frozen=True)
class Terminal:
    """A live Ghostty terminal surface, as seen through AppleScript."""

    uuid: str
    title: str
    working_directory: str


# Error numbers that mean "the surface you addressed isn't there" — see the
# findings doc. -1728 is the `terminal id "X"` object-specifier form; -1719 is
# the `first terminal whose id is "X"` form (specifier resolves to nothing).
_DEAD_CODES = (-1728, -1719)
# -1700 "can't make ... into type specifier" is what an unknown property (tty)
# produces on 1.3.1 via property access; -2753 "variable ... is not defined" is
# what the `whose tty is` form produces (unknown property parsed as a variable).
_UNKNOWN_PROPERTY_CODE = -1700
_UNDEFINED_VARIABLE_CODE = -2753


class Ghostty:
    """A handle to one Ghostty application, addressed by name or by path.

    Address by **name** ("Ghostty") for the installed app; address by absolute
    **path** to a dev build (see plan §7) so tests don't hit the installed copy.
    """

    def __init__(self, target: str = "Ghostty", *, osascript: str = "osascript"):
        # `target` is either an app name or an absolute .app path. Both are valid
        # in `tell application "<target>"`.
        self._target = target
        self._osascript = osascript

    # -- low level ---------------------------------------------------------

    def _tell(self, body: str) -> str:
        """Run ``tell application "<target>" ... end tell`` and return stdout.

        NOTE: this *will* launch Ghostty if it is not already running (that is
        AppleScript's behavior for `tell application`). Read paths that must not
        launch Ghostty guard on :meth:`is_running` first.
        """
        script = f'tell application "{_as_str_inner(self._target)}"\n{body}\nend tell'
        return self._run(script)

    def _run(self, script: str) -> str:
        if not shutil.which(self._osascript):
            raise GhosttyScriptError(f"{self._osascript} not found on PATH")
        proc = subprocess.run(
            [self._osascript, "-e", script],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise _classify(proc.stderr.strip(), script)
        return proc.stdout.rstrip("\n")

    # -- app state ---------------------------------------------------------

    def is_running(self) -> bool:
        """True if a Ghostty process exists, checked *without* launching it."""
        script = (
            'tell application "System Events" to '
            '(name of processes) contains "Ghostty"'
        )
        try:
            return self._run(script).strip().lower() == "true"
        except GhosttyScriptError:
            return False

    def version(self) -> str:
        return self._tell("get version")

    def frontmost(self) -> bool:
        """Is Ghostty the active (frontmost) application?"""
        return self._tell("get frontmost").strip().lower() == "true"

    def focused_terminal_id(self) -> str | None:
        """UUID of the currently focused surface, or None if no front window."""
        try:
            out = self._tell(
                "get id of focused terminal of selected tab of front window"
            ).strip()
        except GhosttyScriptError:
            # No front window (all closed/minimized) -> nothing focused.
            return None
        return out or None

    # -- enumeration -------------------------------------------------------

    def list_terminals(self) -> list[Terminal]:
        """Every live terminal surface with its title and working directory.

        Marshalled with control-char delimiters (not commas) so titles/paths
        containing punctuation parse correctly.
        """
        body = (
            f'set _us to (character id 31)\n'
            f'set _rs to (character id 30)\n'
            f'set _out to ""\n'
            f'repeat with _t in every terminal\n'
            f'  set _out to _out & (id of _t) & _us & (name of _t) & _us & '
            f'(working directory of _t) & _rs\n'
            f'end repeat\n'
            f'return _out'
        )
        out = self._tell(body)
        terminals: list[Terminal] = []
        for record in out.split(_RS):
            if not record:
                continue
            parts = record.split(_US)
            # Be tolerant of fewer fields than expected.
            uuid = parts[0] if len(parts) > 0 else ""
            title = parts[1] if len(parts) > 1 else ""
            wd = parts[2] if len(parts) > 2 else ""
            if uuid:
                terminals.append(Terminal(uuid=uuid, title=title, working_directory=wd))
        return terminals

    def terminal_exists(self, uuid: str) -> bool:
        body = f'return (count of (every terminal whose id is "{_as_str_inner(uuid)}")) > 0'
        return self._tell(body).strip().lower() == "true"

    # -- spawn -------------------------------------------------------------

    def spawn_window(
        self,
        *,
        command: str | None = None,
        working_directory: str | None = None,
        env: dict[str, str] | None = None,
        initial_input: str | None = None,
        keep_open: bool = False,
    ) -> str:
        """Create a new window and return the UUID of its terminal surface.

        Captures identity directly from the spawn result, so no tty/title
        correlation is needed for spawned sessions.
        """
        pairs: list[str] = []
        if command is not None:
            pairs.append(f"command:{_as_str(command)}")
        if working_directory is not None:
            pairs.append(f"initial working directory:{_as_str(working_directory)}")
        if initial_input is not None:
            pairs.append(f"initial input:{_as_str(initial_input)}")
        if keep_open:
            pairs.append("wait after command:true")
        if env:
            items = ", ".join(_as_str(f"{k}={v}") for k, v in env.items())
            pairs.append(f"environment variables:{{{items}}}")

        config = "{" + ", ".join(pairs) + "}" if pairs else ""
        with_config = f" with configuration {config}" if config else ""
        body = (
            f"set _w to new window{with_config}\n"
            f"return id of terminal 1 of _w"
        )
        return self._tell(body).strip()

    # -- focus -------------------------------------------------------------

    def focus(self, uuid: str) -> None:
        """Focus a surface by UUID: raise window, select tab, focus split, activate.

        Raises :class:`DeadSurface` if the surface has been closed.
        """
        body = f'focus terminal id "{_as_str_inner(uuid)}"'
        try:
            self._tell(body)
        except DeadSurface:
            raise
        except GhosttyScriptError as e:
            # Some closed-surface phrasings arrive as generic errors; re-classify
            # by message just in case.
            if "no longer available" in str(e).lower():
                raise DeadSurface(str(e), code=e.code, script=e.script) from e
            raise

    # -- resolution of existing surfaces ----------------------------------

    def resolve_by_tty(self, tty: str) -> str | None:
        """UUID of the surface on `tty`. Raises TtyUnsupported on stock 1.3.1.

        Works unchanged on any Ghostty whose dictionary exposes `tty`.
        """
        body = f'get id of (first terminal whose tty is "{_as_str_inner(tty)}")'
        try:
            return self._tell(body).strip() or None
        except TtyUnsupported:
            raise
        except DeadSurface:
            # No matching terminal.
            return None

    def resolve_by_working_directory(self, path: str) -> str | None:
        body = (
            f'get id of (first terminal whose working directory is '
            f'"{_as_str_inner(path)}")'
        )
        try:
            return self._tell(body).strip() or None
        except DeadSurface:
            return None

    def resolve_by_title_contains(self, needle: str) -> str | None:
        body = f'get id of (first terminal whose name contains "{_as_str_inner(needle)}")'
        try:
            return self._tell(body).strip() or None
        except DeadSurface:
            return None


# -- helpers ---------------------------------------------------------------


def _as_str_inner(value: str) -> str:
    """Escape a Python string for use *inside* AppleScript double quotes."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _as_str(value: str) -> str:
    """A full AppleScript double-quoted string literal."""
    return f'"{_as_str_inner(value)}"'


def _classify(stderr: str, script: str) -> GhosttyScriptError:
    """Turn an osascript stderr blob into the most specific exception we can."""
    code = _extract_code(stderr)
    lowered = stderr.lower()
    # A property absent from the dictionary surfaces two different ways on 1.3.1:
    #   * property-access form (`tty of terminal`)   -> -1700 "into type specifier"
    #   * `whose <prop> is` form (our tty resolver)  -> -2753 "variable tty is not
    #     defined" (AppleScript reads the unknown property name as a variable).
    # Both mean "this Ghostty doesn't expose that property".
    unknown_property = (
        code == _UNKNOWN_PROPERTY_CODE
        or code == _UNDEFINED_VARIABLE_CODE
        or "into type specifier" in lowered
        or "is not defined" in lowered
    )
    if unknown_property:
        # Only tty triggers this in our call set; surface it as TtyUnsupported
        # when the script actually referenced tty, else a generic error.
        if "tty" in script:
            return TtyUnsupported(stderr, code=code, script=script)
        return GhosttyScriptError(stderr, code=code, script=script)
    if code in _DEAD_CODES or "no longer available" in lowered:
        return DeadSurface(stderr, code=code, script=script)
    if code in (-600, -1728) and "isn't running" in lowered:
        return GhosttyNotRunning(stderr, code=code, script=script)
    if "application isn't running" in lowered or code == -600:
        return GhosttyNotRunning(stderr, code=code, script=script)
    return GhosttyScriptError(stderr, code=code, script=script)


def _extract_code(stderr: str) -> int | None:
    """Pull the trailing ``(-NNNN)`` OSStatus code out of an osascript error."""
    # e.g. 'execution error: Ghostty got an error: ... (-1728)'
    if "(" in stderr and stderr.rstrip().endswith(")"):
        tail = stderr.rstrip()[:-1].rsplit("(", 1)[-1]
        try:
            return int(tail)
        except ValueError:
            return None
    return None
