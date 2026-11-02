# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""FastTable: row-local cell updates must show the new value (also when scrolled)."""

import asyncio

from textual.app import App, ComposeResult

from knxscada.tui.widgets import FastTable


class TableApp(App):
    """A small app with a FastTable of 200 rows, more than fit on screen."""
    def compose(self) -> ComposeResult:
        """Only the table."""
        yield FastTable(id="t", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        """Fill the table with address/value rows keyed by address."""
        t = self.query_one(FastTable)
        t.add_column("Address", key="addr", width=8)
        t.add_column("Value", key="value", width=10)
        for i in range(200):
            t.add_row(f"1/1/{i}", f"v{i}", key=f"1/1/{i}")


def visible_text(table: FastTable) -> str:
    """The text of the lines the table renders right now (what the user sees)."""
    return "\n".join(table.render_line(y).text for y in range(table.size.height))


def test_update_cell_shows_new_value_and_keeps_others():
    """update_cell takes the fast row-local path and shows the new value, without touching neighbours, also after
    scrolling.
    """
    async def run() -> None:
        """Update a visible row, then a row far down after moving the cursor there."""
        app = TableApp()
        async with app.run_test(size=(30, 12)) as pilot:
            t = app.query_one(FastTable)
            await pilot.pause()
            before = visible_text(t)
            assert "v3" in before
            count = t._update_count
            t.update_cell("1/1/3", "value", "NEW")
            assert t._update_count == count, "the fast, row-local path was not used"
            await pilot.pause()
            after = visible_text(t)
            assert "NEW" in after and "v3" not in after
            assert "v2" in after and "v4" in after          # neighbours untouched
            # Scrolled: line numbers and rows no longer coincide.
            t.move_cursor(row=150)
            await pilot.pause()
            t.update_cell("1/1/150", "value", "SCROLLED")
            await pilot.pause()
            assert "SCROLLED" in visible_text(t)
            assert str(t.get_cell("1/1/150", "value")) == "SCROLLED"

    asyncio.run(run())


def test_table_cells_are_plain_text_not_markup():
    """A user name or ETS name with brackets must not be parsed as markup (it could end the TUI)."""
    class T(App):
        """An app with an empty FastTable."""
        def compose(self):
            """Only the table."""
            yield FastTable(id="t")

    async def run():
        """Add and update cells with text that looks like markup."""
        app = T()
        async with app.run_test(size=(60, 10)) as pilot:
            t = app.query_one(FastTable)
            t.add_column("a", key="a", width=20)
            t.add_row("[/x] [link=evil]", key="r")
            t.update_cell("r", "a", "[b]not bold[/b]")
            await pilot.pause()
            assert str(t.get_cell("r", "a")) == "[b]not bold[/b]"

    asyncio.run(run())


def test_export_dialog_returns_format_and_period():
    """The export dialog returns the chosen format and period when OK is pressed."""
    from textual.widgets import RadioButton

    from knxscada.tui.dialogs import ExportDialog

    result = []

    class T(App):
        """An app that opens the export dialog and keeps its result."""
        def on_mount(self):
            """Open the dialog at once; the result is appended to the list."""
            self.push_screen(ExportDialog("1/1"), result.append)

    async def run():
        """Choose CSV and 24 hours, then press OK."""
        app = T()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            buttons = list(app.screen.query(RadioButton))
            buttons[1].value = True     # CSV
            buttons[5].value = True     # last 24 hours
            await pilot.pause()
            await pilot.click("#exp-ok")
            await pilot.pause()

    asyncio.run(run())
    assert result == [{"fmt": "csv", "hours": 24}]


def test_logo_by_name_or_file(tmp_path):
    """The splash logo comes from the theme or ui.logo_file (relative to config.yaml), and every built-in logo can be
    read.
    """
    from types import SimpleNamespace

    from knxscada.config import Config
    from knxscada.tui.splash import LOGO_FILE, LOGOS, logo_lines, logo_path

    def app(theme="retro", **ui):
        """A stand-in for the app with only a theme and a config."""
        return SimpleNamespace(theme=theme, cfg=Config(path=tmp_path / "config.yaml", ui=ui))

    assert logo_path(app()) == LOGO_FILE
    assert logo_path(app("storm")) == LOGOS / "storm-automasjon.txt"           # the Storm logo comes with its theme …
    assert logo_path(app("amber", logo_file="storm-automasjon")) != LOGOS / "storm-automasjon.txt"  # … and only then
    assert logo_path(app(logo_file="own.txt")) == tmp_path / "own.txt"       # relative to config.yaml
    for f in LOGOS.glob("*.txt"):                                             # every built-in logo is usable
        assert logo_lines(f) and "█" in "".join(logo_lines(f))
