# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""F8: the floor plan in the terminal with live room status.

Each floor from ``plan.yaml`` is scaled to the available space and drawn
with box-drawing characters. Rooms linked to an ETS room show their
temperature, setpoint, lights, heating and alarm state, and are tinted by
status (alarm red, warning yellow, lights on accent, selected room primary).

Rendering strategy
------------------
The first version built the picture character by character as one big
``Text`` with a style per character (≈7 600 segments) and redrew it every
second. Over SSH that meant ~100 kB per refresh and a sluggish UI. Now:

* the picture is built once into a list of :class:`~textual.strip.Strip`
  (one per screen line), with neighbouring cells of equal style merged into
  one segment (a few hundred segments in total);
* the strips are cached and only rebuilt when something that affects the
  picture changes: size, floor, selected room, focus, theme or room status.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Horizontal
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import Button, Static

from ..i18n import _
from ..plan import PlanFloor, PlanRoom, load_plan, plan_path
from .views import View

if TYPE_CHECKING:
    from .app import ScadaApp

# Wall drawing: each cell collects the directions (N/E/S/W) in which a wall
# leaves it; the combination selects the box-drawing character, so crossing
# walls automatically get ┼, ├, ┬ … junctions.
N, E, S, W = 1, 2, 4, 8
LIGHT = {  # interior walls
    0: " ", N: "│", S: "│", N | S: "│", E: "─", W: "─", E | W: "─",
    E | S: "┌", W | S: "┐", N | E: "└", N | W: "┘",
    N | S | E: "├", N | S | W: "┤", E | W | S: "┬", E | W | N: "┴", N | E | S | W: "┼",
}
HEAVY = {  # outer walls
    0: " ", N: "┃", S: "┃", N | S: "┃", E: "━", W: "━", E | W: "━",
    E | S: "┏", W | S: "┓", N | E: "┗", N | W: "┛",
    N | S | E: "┣", N | S | W: "┫", E | W | S: "┳", E | W | N: "┻", N | E | S | W: "╋",
}


class FloorCanvas(Widget, can_focus=True):
    """Draws one floor scaled to the available space (cached line strips)."""

    BINDINGS = [
        # Several ways to change floor: some SSH clients send unusual arrow key codes.
        Binding("left", "floor(-1)", _("Previous floor"), show=False),
        Binding("right", "floor(1)", _("Next floor"), show=False),
        Binding("pageup", "floor(-1)", show=False),
        Binding("pagedown", "floor(1)", show=False),
        Binding("n", "floor(1)", show=False),
        Binding("p", "floor(-1)", show=False),
        *[Binding(str(i + 1), f"goto_floor({i})", show=False) for i in range(9)],
        Binding("up", "room(-1)", show=False),
        Binding("down", "room(1)", show=False),
        Binding("enter", "open", _("Open room"), show=False),
    ]

    def __init__(self, plan: PlanView, **kw: Any) -> None:
        super().__init__(**kw)
        self.plan = plan
        self.selected = 0  # index of the selected room on the current floor
        self._boxes: list[tuple[int, int, int, int, PlanRoom]] = []  # screen rectangles for clicks
        self._key: Any = None  # what the cached strips were built for
        self._strips: list[Strip] = []

    @property
    def scada(self) -> ScadaApp:
        return self.app  # type: ignore[return-value]

    # -- rendering --------------------------------------------------------------------------
    def render_line(self, y: int) -> Strip:
        """Textual line API. Rebuilds the cached picture when its inputs changed."""
        key = (self.size, self.plan.index, self.selected, self.has_focus, self.plan._sig, self.app.theme, len(self.plan.floors))
        if key != self._key:
            self._key = key
            self._strips = self._build()
        if y < len(self._strips):
            return self._strips[y]
        return Strip.blank(self.size.width, self.rich_style)

    def _build(self) -> list[Strip]:
        """Build the whole picture as one Strip per line."""
        floor = self.plan.floor
        width, height = self.size.width, self.size.height
        base = self.rich_style
        if floor is None or width < 20 or height < 6:
            msg = Text(" " + _("No floor plan. Edit plan.yaml."), style=base + Style(dim=True))
            return [Strip(list(msg.render(self.app.console, end="")), msg.cell_len).extend_cell_length(width, base)]
        theme = self.scada.current_theme
        # Scale mm → cells. A character cell is about twice as high as wide,
        # so the vertical scale is half the horizontal one.
        s = min((width - 2) / floor.width, (height - 2) * 2 / floor.depth)
        sx, sy = s, s / 2
        ox = int((width - floor.width * sx) / 2)   # centre the floor
        oy = int((height - floor.depth * sy) / 2)

        def cx(mm: float) -> int:
            return ox + int(round(mm * sx))

        def cy(mm: float) -> int:
            return oy + int(round(mm * sy))

        # Per-cell layers: wall direction bits, background colour and text.
        light = [[0] * width for _row in range(height)]
        heavy = [[0] * width for _row in range(height)]
        fill: list[list[str | None]] = [[None] * width for _row in range(height)]
        chars: list[list[tuple[str, Style] | None]] = [[None] * width for _row in range(height)]

        def box(grid, x0: int, y0: int, x1: int, y1: int) -> None:
            """Add a rectangle's walls to a direction grid."""
            x0, x1 = max(0, x0), min(width - 1, x1)
            y0, y1 = max(0, y0), min(height - 1, y1)
            if x1 <= x0 or y1 <= y0:
                return
            for x in range(x0, x1 + 1):
                for y in (y0, y1):
                    if x > x0:
                        grid[y][x] |= W
                    if x < x1:
                        grid[y][x] |= E
            for y in range(y0, y1 + 1):
                for x in (x0, x1):
                    if y > y0:
                        grid[y][x] |= N
                    if y < y1:
                        grid[y][x] |= S

        def blend(color: str, alpha: float) -> str:
            """A tint of *color* over the surface colour (keeps text readable)."""
            return Color.parse(theme.surface or theme.background or "#000").blend(Color.parse(color), alpha).hex

        self._boxes = []
        for i, r in enumerate(floor.rooms):
            x0, y0, x1, y1 = cx(r.x), cy(r.y), cx(r.x + r.w), cy(r.y + r.h)
            box(light, x0, y0, x1, y1)
            self._boxes.append((x0, y0, x1, y1, r))
            st = self.plan.status_of(r)
            color = None
            if st and st["alarm"] == "critical":
                color = blend(theme.error or "#f00", 0.35)
            elif st and st["alarm"]:
                color = blend(theme.warning or "#ff0", 0.25)
            elif st and st["lights_on"]:
                color = blend(theme.accent or "#ff0", 0.16)
            if i == self.selected and self.has_focus:
                color = blend(theme.primary or "#0f0", 0.30)
            if color:
                for y in range(max(0, y0 + 1), min(height, y1)):
                    row = fill[y]
                    for x in range(max(0, x0 + 1), min(width, x1)):
                        row[x] = color
            self._label(chars, r, st, x0, y0, x1, y1, width, height)
        box(heavy, cx(0), cy(0), cx(floor.width), cy(floor.depth))

        # Reuse identical Style objects so neighbouring cells merge into one segment
        # (the merge test below is an identity check, which is much faster than ==).
        cache: dict[Any, Style] = {}

        def style(key: Any, make) -> Style:
            st_ = cache.get(key)
            if st_ is None:
                st_ = cache[key] = make()
            return st_

        wall = base + Style(color=theme.foreground)
        outer = base + Style(color=theme.primary, bold=True)
        strips = []
        for y in range(height):
            segs: list[Segment] = []
            run: list[str] = []
            run_style: Style | None = None
            for x in range(width):
                if heavy[y][x]:
                    ch, st_ = HEAVY.get(heavy[y][x], "┃"), outer
                elif light[y][x]:
                    ch, st_ = LIGHT.get(light[y][x], "┼"), wall
                else:
                    bg = fill[y][x]
                    cell = chars[y][x]
                    if cell:
                        ch = cell[0]
                        st_ = style((id(cell[1]), bg), lambda c=cell[1], b=bg: base + c + (Style(bgcolor=b) if b else Style()))
                    else:
                        ch = " "
                        st_ = style(("bg", bg), lambda b=bg: base + Style(bgcolor=b) if b else base)
                if st_ is run_style:
                    run.append(ch)
                else:
                    if run:
                        segs.append(Segment("".join(run), run_style))
                    run, run_style = [ch], st_
            if run:
                segs.append(Segment("".join(run), run_style))
            strips.append(Strip(segs, width))
        return strips

    def _label(self, chars, r: PlanRoom, st: dict | None, x0: int, y0: int, x1: int, y1: int, width: int, height: int) -> None:
        """Write the room's text lines, centred inside its rectangle."""
        theme = self.scada.current_theme
        inner_w = x1 - x0 - 1
        inner_h = y1 - y0 - 1
        if inner_w < 3 or inner_h < 1:
            return  # too small to hold any text
        rows: list[tuple[str, Style]] = [(r.label, Style(bold=True, color=theme.foreground))]
        if st:
            if st["temp"] is not None:
                sp = st["sp"]
                sp_txt = f" → {sp:.1f}°" if isinstance(sp, (int, float)) and not isinstance(sp, bool) else ""
                rows.append((f"{st['temp']:.1f}°{sp_txt}", Style(bold=True, color=theme.primary)))
            marks = []
            if st["lights_total"]:
                marks.append(f"{'●' if st['lights_on'] else '○'} {st['lights_on']}/{st['lights_total']}")
            if st["heating"]:
                marks.append("♨")
            if st["alarm"]:
                marks.append("!!" if st["alarm"] == "critical" else "!")
            if marks:
                color = theme.error if st["alarm"] else (theme.accent if st["lights_on"] else theme.foreground)
                rows.append(("  ".join(marks), Style(color=color, bold=bool(st["alarm"] or st["lights_on"]))))
        elif r.room:
            rows.append((_("(unknown ETS room «{room}»)").format(room=r.room), Style(dim=True)))
        rows = rows[:inner_h]
        top = y0 + 1 + max(0, (inner_h - len(rows)) // 2)
        for i, (text, style) in enumerate(rows):
            text = text if len(text) <= inner_w else text[: max(1, inner_w - 1)] + "…"
            start = x0 + 1 + (inner_w - len(text)) // 2
            y = top + i
            if 0 <= y < height:
                for j, ch in enumerate(text):
                    x = start + j
                    if 0 <= x < width:
                        chars[y][x] = (ch, style)

    # -- interaction ---------------------------------------------------------------------
    def action_floor(self, delta: int) -> None:
        self.plan.change_floor(delta)

    def action_goto_floor(self, index: int) -> None:
        if index < len(self.plan.floors):
            self.plan.change_floor(index - self.plan.index)

    def action_room(self, delta: int) -> None:
        floor = self.plan.floor
        if floor and floor.rooms:
            self.selected = (self.selected + delta) % len(floor.rooms)
            self.refresh()

    def action_open(self) -> None:
        floor = self.plan.floor
        if floor and 0 <= self.selected < len(floor.rooms):
            self.plan.open_room(floor.rooms[self.selected])

    def on_click(self, event: events.Click) -> None:
        for i, (x0, y0, x1, y1, r) in enumerate(self._boxes):
            if x0 < event.x < x1 and y0 < event.y < y1:
                self.selected = i
                self.refresh()
                self.plan.open_room(r)
                return

    def on_focus(self) -> None:
        self.refresh()  # the selected room is highlighted only while focused

    def on_blur(self) -> None:
        self.refresh()


class PlanView(View):
    """Floor tabs, the canvas and a legend line."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.floors: list[PlanFloor] = []
        self.index = 0
        self._sig: Any = None  # signature of all room states on the shown floor
        self._tab_names: list[str] | None = None
        self.by_name: dict = {}  # lower-case ETS room name → home.Room

    @property
    def floor(self) -> PlanFloor | None:
        return self.floors[self.index] if self.floors else None

    def compose(self) -> ComposeResult:
        yield Horizontal(id="plan-tabs")
        yield FloorCanvas(self, id="plan-canvas")
        yield Static("", id="plan-legend", classes="muted")

    def on_mount(self) -> None:
        self.by_name = {r.name.lower(): r for r in self.scada.home.rooms}
        self.set_interval(1, self._tick)

    async def reload(self) -> None:
        """Re-read plan.yaml, so edits show up without restarting."""
        floors = load_plan(self.scada.cfg)
        names = [f.name for f in floors]
        self.floors = floors
        self.index = min(self.index, max(0, len(floors) - 1))
        if names != self._tab_names:
            # Rebuild the tabs only when the floors changed (avoids duplicate widget ids).
            self._tab_names = names
            tabs = self.query_one("#plan-tabs", Horizontal)
            await tabs.remove_children()
            await tabs.mount_all(
                [Button(n, id=f"floor-{i}", compact=True, classes="floor-tab" + (" -active" if i == self.index else ""))
                 for i, n in enumerate(names)]
            )
        path = plan_path(self.scada.cfg)
        legend = _("● lights on   ♨ heating   ! alarm   ←/→, PgUp/PgDn or 1–9: floor · ↑/↓ room · Enter/click opens the room")
        if not self.floors:
            legend = _("No floor plan found in {path}").format(path=path)
        self.query_one("#plan-legend", Static).update(f"{legend}   [dim]({path.name})[/dim]")
        self.query_one(FloorCanvas).refresh()

    def status_of(self, r: PlanRoom) -> dict | None:
        """Live status of the ETS room linked to a plan room (None if not linked)."""
        room = self.by_name.get((r.room or "").lower())
        if room is None:
            return None
        app = self.scada
        return app.home.room_status(room, app.state, app.alarms)

    def _tick(self) -> None:
        """Once per second: repaint only if the status of a room on this floor changed."""
        if not self.display or self.floor is None:
            return
        sig = tuple(
            tuple(sorted((st or {}).items())) for st in (self.status_of(r) for r in self.floor.rooms)
        )
        if sig != self._sig:
            self._sig = sig
            self.query_one(FloorCanvas).refresh()

    def change_floor(self, delta: int) -> None:
        if not self.floors:
            return
        self.index = (self.index + delta) % len(self.floors)
        canvas = self.query_one(FloorCanvas)
        canvas.selected = 0
        for b in self.query(".floor-tab"):
            b.set_class(b.id == f"floor-{self.index}", "-active")
        self._sig = None
        canvas.refresh()

    @on(Button.Pressed, ".floor-tab")
    def _tab(self, event: Button.Pressed) -> None:
        self.change_floor(int(str(event.button.id).split("-")[1]) - self.index)

    def open_room(self, r: PlanRoom) -> None:
        room = self.by_name.get((r.room or "").lower())
        if room is None:
            self.notify(_("«{label}» is not linked to an ETS room (room: in plan.yaml)").format(label=r.label), severity="warning")
            return
        self.scada.show_room_id(room.id)

    def on_show_view(self) -> None:
        self._sig = None
        self.run_worker(self.reload(), exclusive=True, group="plan")
        self.call_after_refresh(self.query_one(FloorCanvas).focus)
