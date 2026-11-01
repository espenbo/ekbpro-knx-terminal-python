# User guide

Start with `./scada`, locally or over SSH (`ssh -t user@server ~/ekbpro-knx-terminal-python/scada`).
At start-up the logo is shown for 5 seconds. Any key skips it.

## The screen

```
┌─ top bar: title · KNX status · telegrams/s · alarms · user · clock ──────────────────┐
│ menu       │ active view                                                              │
│ (tree)     │                                                                          │
├────────────┴──────────────────────────────────────────────────────────────────────────┤
│ key bar: F1 Overview  F2 Rooms … │ hints for the current view                         │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

**The top bar** shows whether KNX is connected (green ●) or disconnected (red ●), and the traffic in telegrams
per second. With several KNX IP interfaces it shows one dot per interface (green connected, red not), and
`● PLC` when the PLC bridge is configured. It also shows the number of unacknowledged alarms (blinking red),
the logged-in user with IP, and a clock. In demo mode a yellow **DEMO** label is shown.

## Keys

| Key | Function |
|---|---|
| `F1` | Overview |
| `F2` | Floors and rooms |
| `F3` | Group addresses |
| `F4` | Monitor: KNX bus monitor; pressed again, the monitor of the next protocol |
| `F5` | Alarms |
| `F6` | Event log |
| `F7` | Trends |
| `F8` | Floor plan |
| `F9` | Screensaver |
| `F10` | Quit |
| `F11` | Gateways (connections, tags and gateways – see [Protocols and gateways](11-protocols-gateways.md)) |
| `Ctrl+E` | Systems: one air handling unit at a time (`ui.systems`) |
| `Ctrl+G` | Energy meters: table, power curve and consumption (`ui.systems`) |
| `F12` | About |
| `Ctrl+T` | Colour theme |
| `Ctrl+L` | Language |
| `Ctrl+B` | Hide or show the menu |
| `Ctrl+P` | Command palette |
| `Ctrl+Q`, `Ctrl+C` | Quit |
| `Tab` / `Shift+Tab` | Next or previous control |
| `Enter` / `Space` | Operate or open |
| `/` | Search or filter in the current view |

The mouse works in most terminals: clicks, scrolling and buttons.

Moving the cursor in the menu shows views and rooms directly. Menu entries that open a dialog or act
immediately (colour theme, language, about, quit) need `Enter`.

The terminal is cleared when you quit.

## F1 Overview

- **Status tiles:**
  - **Outdoor** and **Indoor avg** show the temperature; outdoor has a sparkline for the last 24 hours, indoor
    shows the min–max range of the rooms. Without a KNX outdoor address, Outdoor shows the outdoor sensor of the
    first air handling unit in `ui.systems`.
  - **Power** shows W or kW, with a sparkline. Without a KNX power address, it shows the energy meters of
    `ui.systems` one at a time (5 seconds each), with the meter's name in the title and "2/6" below.
  - **Lights on** shows how many of the lights are on.
  - **KNX tlg/s** shows the traffic.
  - **Alarms** shows active and unacknowledged alarms.
  - The colour has a meaning: green is normal, yellow is a warning, red is critical and grey is no data.
- **Climate:** actual temperature, setpoint, mode, heating and a sparkline for the last 24 hours per room.
  The temperature turns yellow when the room is more than 1.5° below its setpoint. Enter opens the room.
- **Temperature chart:** outdoor and indoor average for the last 24 hours.
- **Room status:** one card per room.
  - `● 1/4` means 1 of the room's 4 lights is on (filled dot: at least one on); `○ 0/3` means none of 3.
  - A yellow frame means a light is on, a yellow `!` is a warning and a red `!!` is critical.
  - ♨ means the heating is on.
  - A click opens the room.
- **Bus activity:** the latest telegrams. Only changed values are highlighted, repeats are muted and alarms are red.
- **KNX traffic:** telegrams per minute for the last hour.
- **Lights on:** Enter switches off the light under the cursor.

## F2 Floors and rooms

`F2` opens the first room of the first floor. Choose floor and room in the menu on the left. If you select a
floor, all its rooms are shown at once. Each function in the room is a card with controls:

| Control | Operation |
|---|---|
| **Switch** | Click, or Space/Enter. The value on the left is the status feedback. |
| **Dimmer** | `−` / `+` changes by 10 % (`ui.dim_step`). `…` sets an exact value. |
| **Setpoint** | `−` / `+` changes by 0.5 °C (`ui.setpoint_step`). `…` sets an exact value. |
| **Operating mode** | Drop-down list: Auto, Comfort, Standby, Night/Eco, Frost protection |
| **Scene** | Enter a number 1–64 and press Send |
| **Values** | Display only. Alarm states are red, and "on" is green. |

Hover the mouse over a row to see the group addresses, the DPT and when the value last changed.

## F3 Group addresses

All group addresses with name, DPT, live value, time and source.

- **The tree on the left** filters by main and middle group.
- **The search field (`/`)** searches address, name, DPT and description. Several words mean all must match.
- **`u`** shows or hides unused addresses and placeholders.
- **Enter** opens the address dialog with details and flags.
  - `w` writes a value, with confirmation.
  - `r` sends a read request.
  - `t` opens the address in Trends.

When you write values, they are interpreted according to the DPT, for example `21.5` (or `21,5`), `on`, `Comfort`
or `75`. If the DPT is unknown, enter raw bytes in hex, for example `0C 33`.

## F4 Monitor

The menu entry **Monitor** has one entry per protocol in use: **KNX**, and for example **OPC UA (2)**,
**Modbus (4)** and **BACnet/IP (1)** (the number of connections). `F4` opens the first one (KNX, or the first
protocol in an installation without KNX); pressed again, it moves on to the next protocol.

### Monitor › Modbus, OPC UA, BACnet, MQTT

On top, the connections of the protocol with role, address, number of tags and state (as in F11). Below, every
**value change** of their tags (← time, connection, tag, description, value with unit, quality when it is not
good) and every **write** (→ value and the user who wrote it), live. Values that do not change are not shown:
the hub passes on changes only.

- **`/`** filters on connection, tag, description, value or user; `write` lists only writes, `released`
  the releases.
- **`p`** pauses and resumes, **`c`** clears the view.
- **Enter, click or `w`** on a line writes a new value to its tag, with confirmation – only for tags with
  `writable: true`. For BACnet the dialog also offers the write priority (see "Writing to BACnet" below).

### Writing to BACnet: priority and release

A BACnet value is written into one of 16 slots of the device's priority array; the highest active slot (the
lowest number) decides the value. The write dialog (Monitor, Systems › Settings, F11 `w`) shows a **Priority**
field preset to the tag's or connection's `priority` (16 when none is set):

- **16** is the usual slot for automatic control. A write there is overwritten the next time the controller
  writes.
- **8** (manual operator) and other numbers below the default **override the automatic control until the value
  is released**. The dialog warns about this, and the confirmation names the priority.
- **Release** writes "nothing" into the chosen slot, so the value with the next lower priority takes over again.
  Release the same priority you wrote with.
- 1, 2 and 5 (life safety and critical equipment) are never offered, and the server refuses them.

The event log shows each write with the priority when it was not the default, and releases as
"released (priority 8)".

The monitor shows what has happened since the TUI was started (up to 10,000 entries for all protocols).

### Monitor › KNX (bus monitor)

All telegrams on the bus, live. Each line shows time, direction (← in, → out), source, destination, name,
type (Write, Response, Read), DPT, raw data and decoded value. With several KNX IP interfaces a **Via** column
shows the interface the telegram came through (the filter matches it too).

- **`/`** filters, for example `1/1`, `stue`, `1.1.24` or `read`. Several words mean all must match.
- **`p`** pauses and resumes.
- **`e`** (or the *Export* button) exports telegrams. Choose
  - the **format**: *ETS group monitor (.xml)* – open it in ETS with *Diagnostics → Group Monitor → Open*;
    *CSV* – `;`-separated with a header, opens in Excel/LibreOffice; or *JSON lines* – one object per line, for
    scripts;
  - the **period**: the telegrams shown in the monitor, or the last hour, 24 hours or 7 days from the server's
    log (up to 500,000 telegrams).

  The filter applies in both cases. The file `knx-busmonitor-<date>-<time>.<ext>` is saved in your home
  directory (`ui.export_dir`). In the ETS file, group addresses are written without a project prefix.
- **`c`** clears the view. The log in the database is kept.
- **`↑` / `↓`, `PgUp` / `PgDn` and `Home` / `End`** scroll. When you are at the bottom, the list follows new telegrams.
- **Enter or click** opens the address dialog.

## F5 Alarms

Active and unacknowledged alarms are shown at the top, the history at the bottom.

An alarm has two independent states: **active** or **normal**, and **acknowledged** or **unacknowledged**.
It disappears from the upper list when it is both back to normal and acknowledged.

- `k` acknowledges the selected alarm
- `K` acknowledges all
- `h` refreshes the history

New alarms show a notification on screen and sound a beep (terminal bell) in all open sessions. Who acknowledged
an alarm is stored.

## F6 Event log

Shows operations with user and IP ("`1/1/0 Stue Vinduslys switching ← On`"), alarms that are raised, cleared
and acknowledged, the KNX connection, and clients connecting and disconnecting. `/` filters.

The texts are stored in the language the server was running with when the event happened
(see [Language](#language-ctrll)), so older entries may be in another language than the user interface.

## F7 Trends

1. **Search** on the left: "temp" shows all temperatures, and "stue temp" the temperatures in the living room.
   The search covers address, name, description and main group.
2. **Enter or click** adds or removes a curve. Each curve gets its own colour (●). You can have up to 6 curves;
   when you add a seventh, the oldest is dropped.
3. **Period:** 1h, 6h, 24h, 7d or 30d.
4. **Chart type:** Line, Step, Filled or Bars (average per hour or day).

`Del` removes all curves. The curves update live.

Trends are based on the telegram log, so they go back as far as `server.retention_days`, and only as far back
as the server has been running. Logged tags of `protocols.yaml` (`log`) are in the list too, with the connection
as the address.

## Systems (Ctrl+E)

Every air handling unit has its own entry under **Systems** in the menu (`Systems → 360.001`, `360.002` …);
`PgUp` / `PgDn` move to the next or previous unit. A unit page has two tabs (`←` / `→` on the tab bar):

- **Overview:** running/stopped, alarm, mode, status text, setpoint and the power of its meter, and the unit
  drawn with boxes – supply air from outdoor air through filter, heat recovery, heating, cooling and fan to
  supply air at the bottom, extract air through filter and fan to exhaust at the top – with temperatures,
  outputs, air flows and pressures. A filter alarm shows `⚠ Alarm` in the filter box, an alarm turns the card
  red, and values from a lost connection are marked as old. On a narrow screen a smaller drawing is used.
  Below: charts of the last 24 hours – temperatures (outdoor, supply, extract, exhaust, setpoint) and outputs
  (fans, heat recovery, heating, cooling). The charts need the tags to be logged (`log` in `protocols.yaml`).
- **Settings:** the tags of the unit's connection in sections – the `groups` of `ui.systems`, else one section
  per component (TFM code such as `JV401`, or the first part of the name), the rest under «Other». Each row shows
  the description without the section name, the live value, the tag (without `prefix`) and whether it is
  writable (`✎`). Type in the field to filter. **Enter** (or `w`) on a `✎` row opens a dialog for the new value;
  after a confirmation showing the old and the new value it is written, and the event log records who did it.
  Only tags with `writable: true` in `protocols.yaml` can be written.

`m` opens the system switch of the unit: one button per `mode_texts` entry, then a confirmation. It needs the
`mode` role to be a writable tag.

What is shown is set up in [`ui.systems`](04-configuration.md#systems-view-ctrle).

## Energy meters (Ctrl+G)

A table of the meters: power, energy, current and voltage per phase, cos φ, frequency and the last 24 hours of
power. The meter under the cursor (`↑` / `↓`) gets a power curve and the consumption per hour or day, worked out
from its energy counter, and the consumption today and in the period. `d` / `w` / `m` choose 24 hours, 7 days or
30 days. The curves need the power and energy tags to be logged (`log` in `protocols.yaml`).

## F8 Floor plan

Shows the building drawn from [`plan.yaml`](06-floor-plan.md). Each room shows temperature → setpoint, lights
(`● 1/4` = 1 of 4 lights on), ♨ heating and alarm. The colour of the room shows its status.

- **`←` / `→`, `PgUp` / `PgDn`, `1`–`9` or `n` / `p`** change floor. You can also click the floor tabs.
- **`↑` / `↓`** select a room.
- **Enter or click** opens the room with all its controls.

## F9 Screensaver

Starts automatically after 5 minutes without input (`ui.screensaver_minutes`), or with `F9`.
It rotates every 20 seconds (`ui.screensaver_rotate_s`) between these pages (`ui.screensaver_pages` picks them
and sets the order; pages without data are skipped):

| Key | Page |
|---|---|
| `clock` | Clock, date and outdoor temperature with the last 24 hours |
| `home` | Home right now: temperature, heating and lights per room |
| `sky` | The sky over the buildings: the sun on its arc by day, the moon and stars by night, one window per room, lit when a light in the room is on. Sunrise and sunset are calculated for `ui.latitude`/`ui.longitude` (default Fredrikstad). |
| `temps` | Temperatures with setpoint marker |
| `ventilation` | Every air handling unit from `ui.systems` as a line drawing: the air moves along the ducts and the fans turn while the unit runs (moves) |
| `heatmap` | Every room as a tile coloured by its deviation from the setpoint, floor by floor |
| `energy` | Power from a KNX power meter (only if one is found) |
| `meters` | All energy meters from `ui.systems`: power now, the last 24 hours, the counter, and the total |
| `rain` | Telegrams falling down the screen like digital rain (moves) |
| `air` | Indoor climate, CO₂ and humidity (only if such sensors are found) |
| `records` | Today: warmest and coldest room, outdoor min/max, lights on, active alarms and the busiest group addresses |
| `knx` | KNX traffic |
| `bounce` | The time in big digits bouncing around the screen (moves) |

The pages marked "moves" are redrawn twice a second. Over a slow SSH link, leave them out with
`ui.screensaver_pages`. The outdoor temperature comes from `ui.outdoor_ga`, else from the outdoor sensor of the
first unit in `ui.systems`. The content moves a little at each page change to avoid burn-in. **Unacknowledged alarms take over the whole
screen** with a slow red blink. Any key or click returns to the normal view.

## Colour theme (Ctrl+T)

| Theme | Style |
|---|---|
| Muted green | Default. Calm base colour, and the status colours stand out. |
| Green phosphor | Retro VT100, everything in green |
| Amber | Retro amber IBM or Wyse terminal |
| Modern dark | Neutral dark with blue, purple and clear signal colours |
| Solarized | Modern with low contrast, easy on the eyes |
| Storm Automasjon | The colours of Storm Elektro: deep blue panels, grey text, white focus, green for OK and red/orange for alarms. Shows the Storm Automasjon logo at start-up and in About (F12). |

`↑` / `↓` previews, Enter selects and Esc cancels. The choice is saved per user **and per installation** in
`~/.config/knxscada/ui.json`, so two sites on the same machine can have different colours.

## Language (Ctrl+L)

The user interface is available in English (default) and Norwegian bokmål. `Ctrl+L`, or the menu entry
**Language**, opens a picker. The choice is saved per user in `~/.config/knxscada/ui.json` (the same file as
the colour theme), and the visualisation restarts itself immediately in the new language.

To use another language for one session only, start with:

```bash
./scada tui --language nb
```

Which language is used: `--language` → the user's own choice (Ctrl+L) → `ui.language` in `config.yaml` → English.

Group address names, room names and other names from ETS are shown as they are in the project and are not
translated. Texts in the event log and alarm texts are generated by the server, in the server's language
(`ui.language`).

## F12 About

Shows version, licence, project, number of addresses and rooms, KNX connection, traffic, alarms, user, theme,
language and platform.
