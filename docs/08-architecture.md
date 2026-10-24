# Architecture (for developers)

## Modules

```
knxscada/
  __main__.py     command line: server, tui, init, import, info, plan-dxf
  config.py       loading of config.yaml, path resolution
  prefs.py        per-user preferences (~/.config/knxscada/ui.json: colour theme, language)
  i18n.py         translation support: _(), N_(), set_language(), LANGUAGES
  locale/
    nb.py         Norwegian bokmål catalogue (CATALOGUE: English → Norwegian)
  project.py      .knxproj → model (group addresses, rooms, functions, datapoints) + cache
  dpt.py          decoding and encoding of DPT values, display text, DPT 1 texts
  alarms.py       alarm rules (glob, regex, condition, severity)
  store.py        SQLite: telegrams, events, alarms
  server.py       ScadaServer (xknx, logging, alarms, socket API) + Simulator (--demo)
  client.py       asynchronous client for the socket
  plan.py         plan.yaml and DXF export
  tui/
    app.py        the Textual app: menu, top bar, key bar, navigation, message handling
    views.py      Overview, Rooms, Group addresses, Bus monitor, Alarms, Events, Trends
    plan_view.py  floor plan (cached line drawing)
    widgets.py    controls (switch, dimmer, setpoint, mode …), FastTable, LineLog, LiveStatic
    dialogs.py    address dialog, value, confirm, colour theme, language
    screensaver.py, splash.py (start-up logo and the About dialog)
    charts.py     plotext helpers (palette from theme, time axis, chart types)
    graphics.py   sparklines, bars, large digits
    home.py       summary per room (temperature, lights, heating, alarm)
    themes.py     colour themes
    app.tcss      style sheet
tests/            pytest tests
tools/
  check_translations.py   lists untranslated and unused strings
```

The old single-file prototype is kept in `legacy/80_KNX_Scada.py` and is not part of the program.

## The model (`var/project_cache.json`)

```json
{
  "cache_version": 4,
  "info":      {"name", "tool_version", …},
  "gas":       {"1/1/0": {"address", "name", "description", "dpt": "1.001", "writable", "readable",
                          "used", "placeholder", "range"}},
  "ranges":    [{"address": "1", "name": "Lys", "gas": […], "children": […]}],
  "floors":    [{"name": "1 etg", "rooms": [{"id", "name", "functions": ["F-1", …]}]}],
  "functions": {"F-1": {"name", "usage", "room", "datapoints": [
                  {"label": "On/Off", "kind": "switch", "ga": "1/1/0", "status": "1/4/0", "dpt": "1.001", "role": "SwitchOnOff"}]}},
  "source":    "ets-functions" | "group-addresses"
}
```

`kind` is one of `switch`, `percent`, `setpoint`, `mode`, `scene`, `value` and `bool`. `ga` is the address that is
written to, and `status` is the address that is displayed. Names from ETS are stored as-is; generated labels
(e.g. "On/Off", "Setpoint") are stored in English and translated when displayed. Increase `CACHE_VERSION` in
`project.py` when the format changes.

## Socket protocol

The protocol uses JSON lines over a Unix socket. On connect, the server sends `hello` with the complete state.

**From the server:**

| `type` | Contents |
|---|---|
| `hello` | `state` (last value per GA), `status`, `stats`, `alarms`, `demo` |
| `telegram` | `t`: `{id, ts, dir, src, dst, type, raw, value, text}`, plus `state` for writes and responses |
| `status` | KNX connection: `{connected, connection, gateway, started}` |
| `stats` | `{count, per_min, clients}` every second |
| `alarms` | All open alarms (active or unacknowledged) |
| `event` | New event |
| `reply` | Reply to a command: `{id, ok, data \| error}` |

**From the client:** `{"id": n, "cmd": …, "user": "user@ip", …}`

| `cmd` | Parameters | Reply |
|---|---|---|
| `hello` / `bye` | – | logs connect and disconnect |
| `write` | `ga`, `value`, `dpt?` | `value` is interpreted according to the DPT. `{"raw": "0C 33"}` gives raw bytes. |
| `read` | `ga` | sends GroupValueRead |
| `ack` | `alarm_id` or `all: true` | number acknowledged |
| `telegrams` | `limit`, `ga?`, `before_id?` | list |
| `events` | `limit` | list |
| `alarm_history` | `limit` | list |
| `history` | `ga`, `hours` | `[[ts, number], …]` |
| `traffic` | `minutes` | telegrams per minute |

Example from the shell:

```bash
echo '{"id":1,"cmd":"history","ga":"3/0/0","hours":1}' | socat - UNIX-CONNECT:var/knxscada.sock | tail -1
```

Clients that do not keep up (write buffer too large) are disconnected by the server.

## Database (SQLite, WAL)

- `telegrams(id, ts, dir, src, dst, type, raw, value JSON, num REAL)`
  - `dir`: `in` | `out`; `type`: `write` | `response` | `read`
  - `num` is used for trends (booleans as 0/1).
  - Indexes on `(dst, ts)` (trends), `(ts)` (traffic, purge) and a partial index on `(dst, id)` for non-read
    telegrams, used to fetch the last value per address at start-up.
- `events(id, ts, level, kind, user, text)`
  - `level`: `info` | `error` | `alarm`
  - `kind`: `system` | `knx` | `user` | `operation` | `alarm`
  - `text` is stored in the server's language at the time of the event.
- `alarms(id, ga, text, severity, raised_ts, cleared_ts, acked_ts, acked_by, value)`
  - `severity`: `critical` | `warning` | free text. Old rows with `kritisk` / `advarsel` are mapped when loaded.

Telegram and event inserts are committed once per second; alarm changes are committed immediately. Once an hour
the server deletes telegrams and events older than `retention_days` in batches (5000 rows per batch), yielding to
the event loop in between, so a large purge does not block the server.

## Performance in the TUI

Lessons from development, valid for Textual 8:

- `Static.update()` always requests a layout of the whole screen. Use `LiveStatic.set()`, which only redraws on
  an actual change and without layout. Give such widgets a fixed height in CSS.
- `DataTable` measures every cell on insertion, and `remove_row` is O(n). `FastTable` skips the measurement when all
  columns have a fixed width. For logs, use `LineLog`, which formats only visible lines and caches them.
- Large drawing areas (the floor plan) are built as `Strip`s with merged segments and cached. That keeps
  SSH traffic low.
- Every telegram is passed to every view (`on_telegram`), so keep that cheap: the app keeps a cached list of
  views, and hidden views (group addresses, trends) only remember the latest value per row and apply it when
  they are shown again. Search fields that rebuild large tables wait for a short pause in typing (0.2 s).
- The overview computes the room status once per change and reuses it for the climate table, the room cards and
  the status tiles.
- `App.query_one()` searches the *active* screen. When dialogs or the screensaver are on top, use `app.main.query_one()`.
- Do not use attribute names that Textual uses internally, for example `_closing` and `_tooltip`.

## Translations

All user-facing text in the source code is written in English. The available languages are listed in
`LANGUAGES` in `knxscada/i18n.py` (`en`, `nb`). Norwegian bokmål is in `knxscada/locale/nb.py`, a plain
dict `CATALOGUE` mapping the English text to the translation. No gettext toolchain (`.po`/`.mo`) is used.

- Wrap text in `_()` where it is **displayed**: `_("Bus monitor")`, `_("{n} telegrams").format(n=n)`.
  Translate the template first, then call `.format()`, so the placeholders stay in the catalogue key.
- Mark module-level constants with `N_()`. It returns the text unchanged, but lets the checker find it; the code
  that shows the constant wraps it in `_()`. Avoid `_()` at import time in the core modules – the server and the
  commands choose the language after importing them.
- Exception: Textual key binding descriptions (`BINDINGS`) are created when the TUI classes are defined. That is
  why `__main__.py` sets the UI language *before* importing `knxscada.tui`, and why a language change from the
  menu restarts the TUI process (`os.execv`, see `run()` in `tui/app.py`) instead of switching in place.
- Missing translations fall back to the English text.
- Names from ETS (group addresses, rooms, functions) and texts from `config.yaml`/`plan.yaml` are user data and
  are never translated.

The language is chosen with this precedence: `./scada tui --language` → the user's choice (Ctrl+L, stored in
`~/.config/knxscada/ui.json`) → `ui.language` in `config.yaml` → English. The server and the
other commands use `ui.language`. `set_language()` accepts `no` as an alias for `nb` and falls back to English for
unknown codes.

Check the catalogue after changing strings:

```bash
venv/bin/python tools/check_translations.py
```

It lists strings used in the code that have no translation, and catalogue entries that are no longer used.

**Adding a language:**

1. Create `knxscada/locale/<code>.py` with a `CATALOGUE` dict (copy `nb.py` and translate the values).
2. Add the code and its native name to `LANGUAGES` in `knxscada/i18n.py`.
3. Run `tools/check_translations.py` against the new catalogue until nothing is missing.

## Tests

The tests are in `tests/` and cover DPT conversion, alarm rules and the interpretation of ETS projects.
pytest is listed in `requirements-dev.txt`:

```bash
venv/bin/pip install -r requirements-dev.txt
venv/bin/python -m pytest
```

## Extending

**New control type:**

1. Give the datapoint a new `kind` in `project.build_datapoints`.
2. Create a `DatapointRow` subclass in `tui/widgets.py`.
3. Hook it into `make_row`.

**New view:**

1. Inherit from `View` in `tui/views.py`, and implement `on_show_view`, `on_telegram` and `on_theme_changed`.
2. Add it to `ScadaApp.compose` and to `MENU`, and give it a short label in `KEYBAR_LABELS` and key hints in
   `VIEW_HINTS` (all in `tui/app.py`).
3. Add the new strings to the catalogues.

**New theme:** add a `Theme` in `tui/themes.py`, to `THEMES` and to `THEME_CHOICES`.

**New alarm condition:** see `Rule.active` in `alarms.py`.

## Testing without an installation

```bash
./scada server --demo         # simulates sensors, actuators with status echo, and fault messages
```

The simulator is based on the project model. It answers read requests and echoes control telegrams to the status
address, including the relation between a switch and its dimming value. Demo mode uses its own database
(`knxscada-demo.db`).
