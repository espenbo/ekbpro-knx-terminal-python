# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""Text-mode graphics: sparklines, bars and big digits – with an ASCII fallback.

These helpers return plain strings so they can be embedded anywhere
(table cells, status tiles, the screensaver). Block and box characters need
a Unicode font; every function takes ``ascii_only`` to fall back to
characters that work on any terminal (``ui.ascii: true``).
"""

from __future__ import annotations

from collections.abc import Sequence

#: Eight levels for sparklines, lowest to highest.
SPARK = "▁▂▃▄▅▆▇█"
SPARK_ASCII = "_.-~=+*#"

#: 3-row "big digit" font used when ``ui.big_digits`` is enabled.
BIG = {
    "0": ("█▀█", "█ █", "▀▀▀"),
    "1": ("▀█ ", " █ ", "▀▀▀"),
    "2": ("▀▀█", "█▀▀", "▀▀▀"),
    "3": ("▀▀█", " ▀█", "▀▀▀"),
    "4": ("█ █", "▀▀█", "  ▀"),
    "5": ("█▀▀", "▀▀█", "▀▀▀"),
    "6": ("█▀▀", "█▀█", "▀▀▀"),
    "7": ("▀▀█", "  █", "  ▀"),
    "8": ("█▀█", "█▀█", "▀▀▀"),
    "9": ("█▀█", "▀▀█", "▀▀▀"),
    ".": (" ", " ", "▀"),
    ",": (" ", " ", "▀"),
    ":": (" ", "▀", "▀"),
    "-": ("   ", "▀▀▀", "   "),
    "+": ("   ", "▄█▄", " ▀ "),
    "°": ("▀", " ", " "),
    " ": (" ", " ", " "),
}


def big_text(text: str, ascii_only: bool = False) -> list[str]:
    """Render *text* in the 3-row big font; unknown characters are skipped."""
    rows = ["", "", ""]
    for ch in text:
        glyph = BIG.get(ch)
        if glyph is None:
            continue
        for i in range(3):
            rows[i] += glyph[i] + " "
    if ascii_only:
        rows = [r.replace("█", "#").replace("▀", "#").replace("▄", "#") for r in rows]
    return [r.rstrip() for r in rows]


def resample(points: Sequence[tuple[float, float]], start: float, end: float, n: int) -> list[float | None]:
    """Reduce ``(timestamp, value)`` points to *n* equal time buckets between *start* and *end*.

    Each bucket gets the average of the points inside it. Empty buckets
    repeat the last known value, because KNX devices only send on change: no
    telegram means "unchanged", not "unknown". Buckets before the first known
    value are None.
    """
    if n <= 0:
        return []
    out: list[float | None] = []
    step = (end - start) / n
    i = 0
    last: float | None = None
    pts = sorted(points)
    # The value in force at *start* is the last one received before it.
    while i < len(pts) and pts[i][0] < start:
        last = pts[i][1]
        i += 1
    for b in range(n):
        edge = start + (b + 1) * step
        vals = []
        while i < len(pts) and pts[i][0] < edge:
            vals.append(pts[i][1])
            i += 1
        if vals:
            last = vals[-1]
            out.append(sum(vals) / len(vals))
        else:
            out.append(last)
    return out


def sparkline(values: Sequence[float | None], ascii_only: bool = False, lo: float | None = None, hi: float | None = None) -> str:
    """One character per value, scaled between *lo* and *hi* (default: min/max of the data)."""
    chars = SPARK_ASCII if ascii_only else SPARK
    nums = [v for v in values if v is not None]
    if not nums:
        return " " * len(values)
    lo = min(nums) if lo is None else lo
    hi = max(nums) if hi is None else hi
    span = (hi - lo) or 1.0  # flat series: avoid division by zero
    out = []
    for v in values:
        if v is None:
            out.append(" ")
        else:
            idx = int((min(max(v, lo), hi) - lo) / span * (len(chars) - 1) + 0.5)
            out.append(chars[idx])
    return "".join(out)


def bar(frac: float, width: int, ascii_only: bool = False) -> str:
    """Horizontal bar *width* characters wide, filled to *frac* (0–1) with 1/8 resolution."""
    frac = max(0.0, min(1.0, frac))
    if ascii_only:
        full = int(round(frac * width))
        return "#" * full + "." * (width - full)
    eighths = " ▏▎▍▌▋▊▉"
    total = frac * width
    full = int(total)
    rest = int((total - full) * 8)
    s = "█" * full
    if full < width:
        s += eighths[rest] if rest else ""
        s = s.ljust(width, "·")
    return s
