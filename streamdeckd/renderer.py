"""The rendering boundary: a :class:`Renderer` interface and a headless impl.

M1 will add a ``StreamDeckRenderer`` that owns the USB HID device and paints
real keys. Everything above this line (the daemon, the state model) talks only
to the :class:`Renderer` protocol, so the whole system runs and is tested
without hardware via :class:`VirtualDeck`, which serializes key state to an
inspectable form (a JSON snapshot plus one PNG per key).
"""

from __future__ import annotations

import functools
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from .state import APPEARANCE, KeyAppearance, KeyState, appearance_for

try:  # Pillow is a hard dep for PNG output but we degrade cleanly without it.
    from PIL import Image, ImageDraw, ImageFont

    _PIL_OK = True
except ImportError:  # pragma: no cover - exercised only on a broken install
    _PIL_OK = False


@runtime_checkable
class Renderer(Protocol):
    """Anything that can paint the deck. The daemon depends only on this."""

    key_count: int

    def render(self, keys: list[KeyAppearance]) -> None:
        """Paint all keys from ``keys`` (length == ``key_count``). Idempotent."""
        ...

    def close(self) -> None:
        """Release resources; typically blank the deck first."""
        ...


class VirtualDeck:
    """A fully inspectable, hardware-free deck.

    Keeps the last rendered frame in memory (:attr:`keys`) and, if given an
    ``out_dir``, writes ``snapshot.json``, one ``key_NN.png`` per key, and a
    composite ``deck.png`` of the whole board per render — so a human (or a
    test) can eyeball exactly what a physical deck would show.
    """

    # The daemon skips the animation ticker for the virtual deck: a still PNG
    # can't convey a breath anyway, and re-emitting 15 PNGs + a composite many
    # times a second would thrash the disk. The static ring already reads as
    # "attention". Only the hardware renderer opts into animation.
    animated = False

    def __init__(
        self,
        key_count: int = 15,
        *,
        out_dir: Path | str | None = None,
        write_png: bool = True,
        key_size: int = 96,
        columns: int = 5,
    ):
        self.key_count = key_count
        self.columns = columns
        self.key_size = key_size
        self.out_dir = Path(out_dir).expanduser() if out_dir is not None else None
        self.write_png = write_png and _PIL_OK
        # Start blank so a reader sees a coherent frame before the first render.
        self.keys: list[KeyAppearance] = [
            appearance_for(KeyState.EMPTY) for _ in range(key_count)
        ]
        self.render_count = 0
        if self.out_dir is not None:
            self.out_dir.mkdir(parents=True, exist_ok=True)

    # -- Renderer protocol -------------------------------------------------

    def render(self, keys: list[KeyAppearance]) -> None:
        if len(keys) != self.key_count:
            raise ValueError(
                f"expected {self.key_count} keys, got {len(keys)}"
            )
        self.keys = list(keys)
        self.render_count += 1
        if self.out_dir is not None:
            self._write_snapshot()
            if self.write_png:
                self._write_pngs()

    def close(self) -> None:
        self.render([appearance_for(KeyState.EMPTY) for _ in range(self.key_count)])

    # -- inspection --------------------------------------------------------

    def snapshot(self) -> dict:
        """The current frame as a plain dict (what ``snapshot.json`` holds)."""
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "key_count": self.key_count,
            "columns": self.columns,
            "render_count": self.render_count,
            "keys": [
                {"index": i, **appearance.to_dict()}
                for i, appearance in enumerate(self.keys)
            ],
        }

    # -- file output -------------------------------------------------------

    def _write_snapshot(self) -> None:
        assert self.out_dir is not None
        tmp = self.out_dir / "snapshot.json.tmp"
        tmp.write_text(json.dumps(self.snapshot(), indent=2) + "\n")
        tmp.replace(self.out_dir / "snapshot.json")

    def _write_pngs(self) -> None:
        assert self.out_dir is not None
        for i, appearance in enumerate(self.keys):
            self._render_key_png(appearance).save(
                self.out_dir / f"key_{i:02d}.png"
            )
        self._write_deck_png()

    def _write_deck_png(self, *, gap: int = 10, pad: int = 16) -> None:
        """Compose all keys into one ``deck.png`` laid out like the physical
        3×N board — the glanceable "what does my deck look like right now" view.
        Each tile is stamped with its key index so it maps to ``{"press": N}``."""
        assert self.out_dir is not None
        size = self.key_size
        cols = self.columns
        rows = (self.key_count + cols - 1) // cols
        width = pad * 2 + cols * size + (cols - 1) * gap
        height = pad * 2 + rows * size + (rows - 1) * gap
        board = Image.new("RGB", (width, height), (24, 24, 26))
        for i, appearance in enumerate(self.keys):
            r, c = divmod(i, cols)
            x = pad + c * (size + gap)
            y = pad + r * (size + gap)
            tile = self._render_key_png(appearance)
            draw = ImageDraw.Draw(tile)
            draw.text((3, 2), str(i), fill=(150, 150, 150))  # key index
            board.paste(tile, (x, y))
        board.save(self.out_dir / "deck.png")

    def _render_key_png(self, appearance: KeyAppearance):  # -> PIL.Image
        size = self.key_size
        img = Image.new("RGB", (size, size), appearance.color)
        draw = ImageDraw.Draw(img)
        _paint_key_face(draw, size, appearance)
        return img


# -- helpers ---------------------------------------------------------------


def _readable_text_color(bg: tuple[int, int, int]) -> tuple[int, int, int]:
    """Black on light keys, white on dark — by perceived luminance."""
    r, g, b = bg
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return (0, 0, 0) if luminance > 140 else (255, 255, 255)


def _lighten(color: tuple[int, int, int], amt: float) -> tuple[int, int, int]:
    """Blend ``color`` toward white by ``amt`` (0..1)."""
    return tuple(round(c + (255 - c) * amt) for c in color)


def draw_spinner(draw, size: int, phase: float, base_color, *, span_deg: int = 100) -> None:
    """Draw a rotating arc hugging the key edge — a "working" spinner.

    ``phase`` (0..1) sets the rotation; the arc is a light tint of the key's
    ``base_color`` so it reads as motion against the fill without clashing with
    the centred label (it rides the border, outside the label's margins).
    """
    inset = max(2, size // 24)
    box = [inset, inset, size - 1 - inset, size - 1 - inset]
    start = (phase * 360) % 360
    draw.arc(
        box,
        start,
        start + span_deg,
        fill=_lighten(base_color, 0.8),
        width=max(2, size // 20),
    )


def draw_question(draw, size: int, color) -> None:
    """Draw a big centred ``?`` — the "needs you" glyph on an attention key."""
    font = _label_font(int(size * 0.6))
    bbox = draw.textbbox((0, 0), "?", font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]), "?", font=font, fill=color)


def label_color_for(appearance: KeyAppearance) -> tuple[int, int, int]:
    """Contrast colour for a key's label, chosen from the *base* state colour —
    not the instantaneous ``appearance.color``, which an animation may have
    dimmed. Fixing it to the base keeps the text from flickering black↔white as
    a pulsing key breathes."""
    base = APPEARANCE.get(appearance.state)
    return _readable_text_color(base.color if base else appearance.color)


# TrueType faces to try, best first; falls back to Pillow's bitmap font if none
# are present (e.g. a bare Linux CI box). macOS ships the first two.
_FONT_CANDIDATES = (
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


@functools.lru_cache(maxsize=64)
def _label_font(size: int):
    """A TrueType font at ``size`` px, or the bitmap default (ignores size)."""
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()  # pragma: no cover - only on font-less hosts


def draw_label(draw, size: int, text: str, color, *, margin: int = 6) -> None:
    """Draw ``text`` as a single centered line in a ``size``×``size`` key.

    No wrapping and no ellipsis by design — labels arrive pre-clipped to a few
    chars (:func:`streamdeckd.state.format_branch_label`). Picks the largest
    font, from ~28 % of the key height down, whose one line fits within the
    horizontal margins, so short labels render big and 7-char ones still fit."""
    if not text:
        return
    avail = size - 2 * margin
    hi = max(10, int(size * 0.28))
    lo = max(8, int(size * 0.14))
    font = _label_font(lo)
    for pt in range(hi, lo - 1, -1):
        f = _label_font(pt)
        if draw.textlength(text, font=f) <= avail:
            font = f
            break
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) / 2, (size - th) / 2 - bbox[1]), text, font=font, fill=color)


def _paint_key_face(draw, size: int, appearance: KeyAppearance) -> None:
    """Draw a key's foreground onto ``draw`` (fill already laid down). The single
    source of truth shared by the virtual and hardware renderers:

    - ATTENTION ("needs you"): a big blinking ``?`` (drawn while ``blink_on``);
      no branch label — the glyph is the whole message.
    - WORKING: a rotating spinner arc plus the branch label.
    - everything else: just the branch label.
    """
    if appearance.pulse:  # attention
        if appearance.blink_on:
            draw_question(draw, size, (255, 255, 255))  # white "?" on the yellow
        return
    if appearance.spin is not None:  # working
        draw_spinner(draw, size, appearance.spin, appearance.color)
    if appearance.label:
        draw_label(draw, size, appearance.label, label_color_for(appearance))
