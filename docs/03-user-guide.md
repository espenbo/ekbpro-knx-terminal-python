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
per second. It also shows the number of unacknowledged alarms (blinking red), the logged-in user with IP, and a
clock. In demo mode a yellow **DEMO** label is shown.

## Keys

| Key | Function |
|---|---|
| `F1` | Overview |
| `F2` | Floors and rooms |
| `F3` | Group addresses |
| `F4` | Bus monitor |
| `F5` | Alarms |
| `F6` | Event log |
| `F7` | Trends |
| `F8` | Floor plan |
| `F9` | Screensaver |
| `F10` | Quit |
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
    shows the min–max range of the rooms.
  - **Power** shows W or kW, with a sparkline.
  - **Lights on** shows how many of the lights are on.
  - **KNX tlg/s** shows the traffic.
  - **Alarms** shows active and unacknowledged alarms.
  - The colour has a meaning: green is normal, yellow is a warning, red is critical and grey is no data.
- **Climate:** actual temperature, setpoint, mode, heating and a sparkline for the last 24 hours per room.
  The temperature turns yellow when the room is more than 1.5° below its setpoint. Enter opens the room.
- **Temperature chart:** outdoor and indoor average for the last 24 hours.
- **Room status:** one card per room.
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

## F4 Bus monitor

All telegrams on the bus, live. Each line shows time, direction (← in, → out), source, destination, name,
type (Write, Response, Read), DPT, raw data and decoded value.

- **`/`** filters, for example `1/1`, `stue`, `1.1.24` or `read`. Several words mean all must match.
- **`p`** pauses and resumes.
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
as the server has been running.

## F8 Floor plan

Shows the building drawn from [`plan.yaml`](06-floor-plan.md). Each room shows temperature → setpoint, ● lights,
♨ heating and alarm. The colour of the room shows its status.

- **`←` / `→`, `PgUp` / `PgDn`, `1`–`9` or `n` / `p`** change floor. You can also click the floor tabs.
- **`↑` / `↓`** select a room.
- **Enter or click** opens the room with all its controls.

## F9 Screensaver

Starts automatically after 5 minutes without input (`ui.screensaver_minutes`), or with `F9`.
It rotates every 20 seconds (`ui.screensaver_rotate_s`) between these pages:

- clock and outdoor temperature
- home right now
- temperatures with setpoint marker
- energy (only if a power meter is found)
- indoor climate, CO₂ and humidity (only if such sensors are found)
- KNX traffic

The content moves a little at each page change to avoid burn-in. **Unacknowledged alarms take over the whole
screen** with a slow red blink. Any key or click returns to the normal view.

## Colour theme (Ctrl+T)

| Theme | Style |
|---|---|
| Muted green | Default. Calm base colour, and the status colours stand out. |
| Green phosphor | Retro VT100, everything in green |
| Amber | Retro amber IBM or Wyse terminal |
| Modern dark | Neutral dark with blue, purple and clear signal colours |
| Solarized | Modern with low contrast, easy on the eyes |

`↑` / `↓` previews, Enter selects and Esc cancels. The choice is saved per user in `~/.config/knxscada/ui.json`.

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
