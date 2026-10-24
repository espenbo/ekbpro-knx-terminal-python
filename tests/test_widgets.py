# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""FastTable: row-local cell updates must show the new value (also when scrolled)."""

import asyncio

from textual.app import App, ComposeResult

from knxscada.tui.widgets import FastTable


class TableApp(App):
    def compose(self) -> ComposeResult:
        yield FastTable(id="t", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        t = self.query_one(FastTable)
        t.add_column("Address", key="addr", width=8)
        t.add_column("Value", key="value", width=10)
        for i in range(200):
            t.add_row(f"1/1/{i}", f"v{i}", key=f"1/1/{i}")


def visible_text(table: FastTable) -> str:
    return "\n".join(table.render_line(y).text for y in range(table.size.height))


def test_update_cell_shows_new_value_and_keeps_others():
    async def run() -> None:
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
            assert t.get_cell("1/1/150", "value") == "SCROLLED"

    asyncio.run(run())
