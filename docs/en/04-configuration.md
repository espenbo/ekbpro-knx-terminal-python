# Configuration (`config.yaml`)

`config.yaml` lives in the program directory. Choose another file with `-c file.yaml` or `KNXSCADA_CONFIG=file.yaml`.
Relative paths are resolved from the directory the file is in. The template is [`config.example.yaml`](../../config.example.yaml).

Changes to `knx`, `project`, `server`, `alarms` and `plc` require a restart of the server. Changes to `ui` require a
restart of the visualisation. `ui.language` is also used by the server (for event log and alarm texts), so
restart the server as well when you change it.

## `knx` – connection

| Key | Default | Description |
|---|---|---|
| `enabled` | on when `knx:` or `project:` is filled in | `false` = run without KNX (only the connections and gateways of `protocols.yaml`; the TUI starts on F11) |
| `connection` | `automatic` | `tunneling` (UDP), `tunneling_tcp`, `routing` (multicast), `automatic` (discovery), `tunneling_secure`, `routing_secure` |
| `gateway_ip` | – | IP address of the IP interface. Not needed for `routing` and `automatic`. |
| `gateway_port` | `3671` | |
| `local_ip` | auto | Local IP to send from. Set it when the machine has several network interfaces. |
| `individual_address` | – | Individual address for routing, for example `1.1.250` |
| `route_back` | `false` | `true` when the IP interface is reached through NAT or a VPN (Tailscale subnet router, WireGuard, a router with port forwarding): the interface answers to the address the request came from instead of the address in the request. `tunneling` (UDP) only. |
| `keyring_file` | – | `.knxkeys` from ETS. Needed for KNX Secure. |
| `keyring_password` | – | Password of the keyring file |
| `user_id` | – | Tunnel user for secure tunneling |
| `name` | `knx` | Name of this interface in the TUI and the event log (single-interface form) |

**Tunneling or routing?** An IP interface has a limited number of tunnels, often 1–5. The program uses
one tunnel per interface regardless of how many users are logged in. With an IP router you can use `routing` and use
no tunnel at all.

`tunneling_secure` uses secure tunneling over TCP.

### Several KNX IP interfaces

A building with more than one IP interface (for example one per TP line or building part, with no IP
backbone between them) is configured with `interfaces`. The server opens one connection per interface and
treats them as one bus: everything is logged in the same database, alarms and the PLC bridge see all of
it, and the TUI shows one dot per interface in the top bar and the interface in the bus monitor (*Via*).

```yaml
knx:
  keyring_file: MyProject.knxkeys      # see below which keys at this level are defaults for every interface
  interfaces:
    - name: house                      # short name, shown in the TUI and the event log
      connection: tunneling
      gateway_ip: 192.168.1.50
      default: true                    # used for addresses no other interface claims
    - name: garage
      connection: tunneling
      gateway_ip: 192.168.1.51
      addresses: ["4/*", "5/1/*"]      # group addresses sent through this interface
```

| Key (per interface) | Default | Description |
|---|---|---|
| `name` | `gateway_ip`, else `knx1`, `knx2` … | Letters, digits, `-`, `_`, `.`; at most 16 characters; unique. |
| `connection`, `local_ip`, `route_back`, `keyring_file`, `keyring_password`, `user_id`, `dedup_ms` | from the `knx:` level | As above, for this interface. These are the only keys taken from the `knx:` level. |
| `gateway_ip`, `gateway_port`, `individual_address` | as in the table above | Per interface only – never taken from the `knx:` level. |
| `addresses` | – | Group addresses whose telegrams are **sent** through this interface: a list of patterns, `*` matches anything (`"4/*"`, `"1/2/1?"`, `"3/1/0"`). |
| `project` | first project | With several ETS projects (`project.files`): the project whose bus this interface is connected to. |
| `default` | first interface of the project | The interface for addresses that no `addresses` pattern matches and that have not been seen yet. |

| Key (`knx:` level) | Default | Description |
|---|---|---|
| `dedup_ms` | `1000` | Telegrams with the same address, type and value that arrive through *another* interface within this time are copies and are dropped. `0` = off. |

**Receiving** works without any setup: every interface delivers what it sees. **Sending** (operation from the
TUI, writes from the PLC, `read_on_start`) uses one interface per group address:

1. the first interface whose `addresses` match;
2. otherwise the interface the address was last *received* on – the server learns this while running;
3. otherwise the default interface.

So `addresses` is only needed for group addresses that the server must *send* to before anything has been heard
on them – typically the control addresses of lines that are not behind the default interface.

**Coupled lines.** If the interfaces sit on lines that are connected (line couplers, IP routers), a telegram is
seen by several interfaces. The copies are dropped (`dedup_ms`), so the log and the alarms see it once. A
telegram you send goes out through one interface only; the couplers carry it to the other lines as usual.

The single-interface form above (`connection`, `gateway_ip` … directly under `knx:`) still works and is the same
as one interface named `knx`. With several interfaces, the top bar shows `KNX` in green only when all of them are
connected; operation works as long as the interface for that address is connected.

## `project` – ETS project

| Key | Default | Description |
|---|---|---|
| `file` | `project.knxproj` | Path to the `.knxproj` (or `.knxprojarchive`) |
| `password` | – | Password if the project is protected |
| `cache` | `var/project_cache.json` | The parsed project. Rebuilt automatically when the project file is newer, or when `files` or `rooms` change. |
| `files` | – | Several ETS projects in one site, instead of `file` (see below). `password` is then given per entry. |
| `rooms` | – | Rooms from the group address names (see below) |
| `extra_rooms` | – | Rooms of your own for addresses no ETS room holds (see [Extra rooms](#extra-rooms)) |
| `point_rooms` | – | Rooms whose cards show protocol tags, e.g. an annex behind a gateway (see [Rooms from protocol tags](#point-rooms)) |

`.knxprojarchive` files from ETS 6 can be used directly as well.

### Rooms from the group address names

Without ETS functions, cards are built from the group address structure: main group → floor, middle group →
room. That suits projects structured by room, but not projects structured by *function*, where the room is
only part of the name:

```
2/0  Actual temp     2/0/5 Actual temp Office 1       2/0/6 Actual temp Office 2 …
2/2  Setpoint        2/2/5 Setpoint Office 1          2/2/6 Setpoint Office 2 …
0/0  Switching       0/0/0 Switching Office 1 ceiling 0/0/1 Switching Office 2 left …
```

`rooms` collects the addresses per room from their names:

```yaml
project:
  rooms:
    # Finds the room in a name. The first group (or the whole match) is the room name.
    pattern: "(?i)\\b(office\\s*\\d+|meeting room|kitchen|storage|corridor)\\b"
    # Main group → floor. Without it the floor is the name of the main group.
    floors: {0: "2nd floor", 3: "2nd floor", 1: "1st floor", 2: "1st floor"}
    # Room names to show instead of the matched text, as "floor/room" (optional).
    names:
      "2nd floor/Office 6": "Office 6 · RT6206"
    # Further names of setpoints, which get − / + (optional; "setpoint", "setpunkt", "ønsket" … are built in).
    setpoint: "(?i)set temp"
    # Names that are only shown, never written from a card, even with the write flag in ETS (optional).
    read_only: "(?i)desired temp|valve position"
    # Addresses left out of the cards, e.g. an unused thermostat channel (optional; * matches anything).
    exclude: ["2/*/46"]
```

- The text *before* the room is the row label ("Actual temp", "Switching status"); a name that starts with the room
  ("Office 1 Request") has its label after it.
- The text *after* the room is the fixture ("ceiling", "left"). A room gets one card per main group and
  fixture, titled with the main group name without the floor words: "Lighting 2nd floor" + "ceiling" on floor
  "2nd floor" → **Lighting ceiling**; "Heating 2nd floor" → **Heating**.
- Control and status addresses are paired as usual ("Switching Office 1 ceiling" + "Switching status Office 1 ceiling").
- Addresses without a room in the name are shown as before, from the group address structure.
- Every address with the write flag in ETS can be written from its card. When a thermostat's object has the flag but
  should only be read (the desired temperature it calculates, the valve position), add the name to `read_only`;
  `read_only` wins over `setpoint` and the built-in setpoint words.
- The overview (F1) takes the room's setpoint from the setpoint row of its heating card and shows heating
  when a valve position (5.001, read-only) is above 0 %.
- Ignored when the project has ETS functions. With several projects, `rooms` applies to all of them; an entry
  in `files` can have its own `rooms`.

Run `./scada import` after changing it (`./scada info` shows the rooms).

<a id="extra-rooms"></a>

### Extra rooms

Addresses that no ETS room holds – e.g. a sensor and lights in an attic that is missing in the ETS building
structure – can be put into rooms of your own. They appear in F2 and in the overview, and `plan.yaml` links to
them by name like to any other room.

```yaml
project:
  extra_rooms:
    - floor: Attic                    # an existing floor, or a new one
      name: Attic                     # the name plan.yaml links to (room: "Attic")
      cards:
        - name: Temperature
          addresses: ["3/0/35"]
        - name: Attic light
          addresses: ["1/1/70", "1/4/70", "1/3/70", "1/5/70"]
        - name: Store room light
          addresses: ["1/0/5", "1/0/7"]
          status: {"1/0/5": "1/4/81"} # control → feedback, when the names do not pair them
          usage: light                # light | dimmer | heating | other (default: guessed)
```

Defaults: `floor` = `Other`, `name` = `Room 1`, `Room 2` …, `usage` guessed from the addresses. With several ETS
projects, the addresses of the second and later projects need their prefix (`S:3/0/35`).

Each card is built like the cards from ETS: control and status addresses are paired by name (or by
`status`), the DPT decides switch, dimmer, setpoint or value. A card with a temperature counts as the room
temperature. Addresses that are not in the project stop the start with an error. The rooms are read from
`config.yaml` at every start – no `./scada import` needed.

<a id="point-rooms"></a>

### Rooms from protocol tags (another building behind a gateway)

Rooms can also show tags of `protocols.yaml` – for example an annex whose KNX reaches us through a KNX→BACnet
gateway. `project.point_rooms` is built like `extra_rooms`, but each card has `rows` with a `point`
(`connection/tag`). With `building`, the menu groups the floors by building: «Floors and rooms → Main building /
Annex → floors → rooms»; `ui.building` names the building of the ETS floors (default «Main building»).

```yaml
project:
  point_rooms:
    - building: Annex
      floor: 2nd floor
      name: Office 205
      cards:
        - name: Light
          usage: light                # light | dimmer | heating | other
          rows:
            - {point: "gateway/Light 205 On/Off", status: "gateway/Light 205 Status", label: Light}
        - name: Heating
          usage: heating
          rows:
            - {point: "gateway/RT205_PV", role: temp, label: Temperature}
            - {point: "gateway/RT205_SP", role: setpoint, label: Setpoint}
            - {point: "gateway/RT205_VP", role: heat, label: Heating}
ui:
  building: Main building
```

The kind of row follows from the tag: writable on/off → switch, writable with `role: setpoint` → setpoint
(− / +), writable % → dimmer, everything else a value (`kind:` overrides). `role` `temp`, `setpoint` and `heat`
make the room temperature, setpoint and heating of the room card in the overview. Writes go to the tag
(`writable: true` in `protocols.yaml`) and are logged like other writes. A tag that does not exist is reported
when the TUI starts; the other rooms work.

### Several ETS projects in one site

A site can consist of several ETS projects with their own KNX installations – for example a main building and
an annex. List them under `files` instead of `file`:

```yaml
project:
  files:
    - name: main                 # used by knx.interfaces[].project
      label: Main building       # shown in the TUI (floors, address tree)
      file: Main building.knxproj
    - name: annex
      label: Annex
      prefix: S                  # letters/digits; default: the name
      file: Annex.knxprojarchive
      # password: ""
  cache: var/project_cache.json

knx:
  interfaces:
    - {name: main-1, project: main, gateway_ip: 192.168.1.50}
    - {name: annex,  project: annex, gateway_ip: 192.168.2.50}
```

Two projects can use the same group address for different things (`3/0/1` is a room temperature in one and the
outdoor temperature in the other). The addresses of the first project therefore stay as they are, and those of
the others get their prefix: **`S:3/0/1`**. That is the address everywhere in the program – in the TUI, the
database, alarm rules (`match_address: "S:3/*"`), `plan.yaml` and the PLC mapping (variable `GA_S_3_0_1_…`).
On the bus the prefix is removed again: a telegram from an interface of the annex is stored as `S:…`, and
`S:…` is only sent through an annex interface. `addresses` patterns of an interface are written without the
prefix.

Floors are named "<label>: <floor>", and the address tree (F3) has one top node per project.

| Key (per entry in `files`) | Default | Description |
|---|---|---|
| `file` | required | Path to the `.knxproj` / `.knxprojarchive`. An entry can also be just the path as a string. |
| `name` | `p1`, `p2` … | Used by `knx.interfaces[].project` |
| `label` | the name | Shown in the TUI (floors, address tree) |
| `prefix` | the name | Letters and digits. The first project never gets one. |
| `password` | – | Password if the project is protected |
| `rooms` | `project.rooms` | Rooms from names, for this project only |

## `server`

| Key | Default | Description |
|---|---|---|
| `socket` | `var/knxscada.sock` | Unix socket between server and visualisation |
| `database` | `var/knxscada.db` | SQLite with telegrams, events and alarms. Demo mode uses `var/knxscada-demo.db`. |
| `retention_days` | `90` | How long telegrams and events are kept. Old data is deleted every hour. Alarms are kept. |
| `read_on_start` | `status` | Read values from the bus when the server starts: `status` (feedback, sensor and alarm addresses only), `false` (send no telegrams; the last values from the database are shown, marked as old) or `all` (see the warning below). `true` means `status`, and `off`, `no`, `0` mean `false`. Any other value is taken as `status`. Values from the database are never passed on to gateways. |
| `read_interval_ms` | `80` | Pause between the reads, so the bus is not overloaded |
| `protocols` | `protocols.yaml` | Connections and gateways (Modbus, MQTT, OPC UA, BACnet): see [Protocols and gateways](11-protocols-gateways.md). Use `var/protocols.yaml`, as `config.example.yaml` does: the systemd service may only write in `var/`, so F11 can only save the file there. |

**Why `status` is the default, and `all` is not recommended.** A GroupValueRead on a *control*
address (e.g. "Hall light switching") is answered by every device that has the read flag (R) on that address –
also by a push button or logic module that still holds an old value. Actuators whose object has the update
flag (U) treat such a response like a write, so a read can switch a light off. `status` therefore never reads
an address that is used to control something; it only reads feedback, sensor and alarm addresses, so the
values are fresh right after a start. With `false` the server sends nothing at start-up: the last values from
the database are shown dimmed and in italics ("not read yet") until the bus sends a new one, and they are never
passed on to the PLC bridge or to gateways.

The database grows with the traffic, approx. 115 bytes per telegram. At 1–2 telegrams/s that is approx. 10–20 MB
per day, and 0.9–1.8 GB with `retention_days: 90`.

## Alarms

`alarms:` is a list of rules. For each group address the **first matching rule** is used, so put specific rules
before general ones.

```yaml
alarms:
  - match_address: "0/6/*"          # address glob (optional)
    match_name: "(?i)alarm$"        # regex against the GA name (optional)
    exclude_name: "(?i)acknowledg"  # regex that excludes (optional)
    dpt_main: 1                     # main DPT (optional)
    condition: "true"               # when the rule is active
    severity: critical              # critical | warning (any name is allowed)
    text: "Smoke alarm: {name}"     # {name}, {address}, {value}
```

`address` is accepted as an alias for `match_address`. `dpt_main` must be a number (`1`, not `"1"`). A rule
whose `condition` is none of the forms below is never active – there is no warning. A rule without `condition`
uses `"true"`, without `text` the GA name. Placeholder addresses and addresses without any linked
communication object are never monitored.

`delay_s: 60` raises the alarm only when the condition has lasted 60 seconds. Tags of `protocols.yaml` (Modbus,
OPC UA, MQTT, BACnet) get alarms from rules with `match_point: "connection/tag-glob"` – see
[Alarms on tags](11-protocols-gateways.md#alarms-on-tags).

| `condition` | Active when |
|---|---|
| `true` / `on` / `1` | the value is true, for example DPT 1 = 1 |
| `false` / `off` / `0` | the value is false. Useful for "OK" signals that drop out. |
| `> 30`, `< 5`, `>= x`, `<= x`, `== x`, `!= x` | the numeric value meets the condition (`.` or `,` as decimal separator) |

**Severity:** `critical` and `warning` are the standard severities. Critical alarms are shown in red and take over
the screensaver with "CRITICAL ALARM". The Norwegian names `kritisk` and `advarsel` from older configurations are
still accepted and mapped to `critical` and `warning`. Other names are allowed and shown as written. If
`severity` is omitted, `warning` is used.

**Texts:** `text` is used as written (it is not translated), so write it in the language you want to see.
`{name}` is the GA name from ETS, `{address}` the group address and `{value}` the formatted value.

**Life cycle:** An alarm is raised when the condition becomes true, and cleared when it becomes false. It is
acknowledged by a user. Alarms are stored in the database and survive a restart.

This is how you check which addresses the rules match:

```bash
venv/bin/python -c "
from knxscada.config import load_config; from knxscada.project import load_project; from knxscada.alarms import build_rules
c=load_config(); m=load_project(c)
for a,r in build_rules(c.alarms, m['gas']).items(): print(a, m['gas'][a]['name'], '→', r.severity, r.condition)"
```

## `ui` – visualisation

| Key | Default | Description |
|---|---|---|
| `title` | project name | Title in the top bar |
| `building` | `Main building` | Name of the ETS building in the menu when `project.point_rooms` add another building |
| `language` | `en` | Language: `en` (English) or `nb` (Norwegian bokmål; `no` is accepted as an alias). Also the language of the event log and alarm texts the server generates. Each user's own choice (Ctrl+L) and `./scada tui --language` take precedence in the visualisation. |
| `theme` | `retro` | Default theme: `retro`, `retro-classic`, `amber`, `modern`, `solarized`, `storm`. The user's own choice for this installation (Ctrl+T) takes precedence. The old names `retro-klassisk` and `moderne` are still accepted. |
| `logo_file` | – | A logo of your own for the start-up screen and About (a text file). The theme `storm` always shows the Storm Automasjon logo instead. Drawn with `█ ▀ ▄` (solid, in the theme's main colour) and `▒` (shade); blocks separated by an empty line – when the logo is too big for the window, only the first block is shown. |
| `splash` | `true` | Start-up logo |
| `big_digits` | `false` | Large block digits in status tiles and screensaver. Normal text is the easiest to read. |
| `ascii` | `false` | Only ASCII characters in charts, for older terminals |
| `screensaver_minutes` | `5` | Minutes without input before the screensaver starts. `0` turns it off. |
| `screensaver_rotate_s` | `20` | Seconds per screensaver page |
| `screensaver_pages` | all | The screensaver pages and their order, e.g. `[clock, sky, heatmap, meters, records]`. Keys: `clock`, `home`, `sky`, `temps`, `ventilation`, `heatmap`, `energy`, `meters`, `rain`, `air`, `records`, `knx`, `bounce` (see the user guide). Unknown keys are ignored. |
| `latitude`, `longitude` | `59.22`, `10.93` (Fredrikstad) | Position for sunrise, sunset and the moon on the screensaver page `sky` (decimal degrees) |
| `plan` | `plan.yaml` | The floor plan file |
| `export_dir` | `~` | Where the bus monitor export (`e`: ETS XML, CSV or JSON) is saved. In Docker: e.g. `/data/export`. |
| `outdoor_ga` | auto | Group address of the outdoor temperature. Auto finds a DPT 9 address whose name starts with "ute temp", "outdoor" or "outside". Without one, the `outdoor` tag of the first unit in `ui.systems` is used. |
| `power_ga` | auto | Power (DPT 14.056) for the status tile. Auto finds a name ending in "power" or "W", or containing "effekt". Without one, the tile rotates through the energy meters of `ui.systems`. |
| `energy_ga` | auto | Energy meter (DPT 13.x). Auto finds a name containing "kwh" or "energy". |
| `setpoint_step` | `0.5` | Step for − / + on setpoints |
| `dim_step` | `10` | Step for − / + on dimmers |
| `confirm_raw_write` | `true` | Confirmation before writing from the group address view |

An unknown `language` falls back to English.

### Systems view (Ctrl+E)

`ui.systems` describes the air handling units of `protocols.yaml` for the **Systems** view and the meters for
the **Energy meters** view (Ctrl+G). Each unit names a connection and binds its tags to roles; `prefix` is put in
front of every tag name. Every unit gets a menu entry under Systems: `short`, else the `360.001` part of a name
that starts with `=360.001`, else the name.

```yaml
ui:
  systems:
    - name: "=360.001 Ventilation 1st floor"
      short: "360.001"                  # menu entry – optional
      type: ventilation
      connection: vent1                 # a connection in protocols.yaml
      prefix: BY_360_001_
      meter: meter1                     # its energy meter (a connection with a meter template) – optional
      tags: {outdoor: RT900_PV, supply: RT401_PV, extract: RT501_PV, setpoint: AktReg_SP,
             supply_fan: JV401_OP, extract_fan: JV501_OP, recovery: LX001_OP, heating: LV001_OP,
             cooling: LK001_OP, alarm: SYS_AL, filter_supply: QD401_AL, filter_extract: QD501_AL, mode: SD_CMD}
      mode_texts: {0: Auto, 1: Manual off, 2: Manual on}
      status_texts: {12: Time schedule} # text per value of the status tag – optional
      alarm_normal: 1                   # value of the alarm tag that means "no alarm" – optional
      settings: ["*_SP", "*_Set*"]      # tags on the Settings tab (glob) – optional, default: all tags
      hide: [Act_Time, DayOfWeek]       # never on the Settings tab (glob without prefix) – optional
      groups:                           # sections of the Settings tab (glob without prefix) – optional
        - {title: "Operation and alarms", tags: [SYS_*, SD_CMD, QD401_AL, QD501_AL]}
        - {title: "Supply fan JV401", tags: [JV401_*]}
    - name: Energy
      type: energy
      meters:
        - {name: Main board, connection: meter_main}
        - {name: Ventilation 1st floor, connection: meter1, tags: {pf: "Power factor"}}
```

Roles of a unit: `outdoor`, `supply`, `extract`, `exhaust`, `setpoint`, `supply_fan`, `extract_fan`,
`supply_fan_run`, `extract_fan_run`, `recovery`, `heating`, `cooling`, `supply_flow`, `extract_flow`,
`supply_pressure`, `extract_pressure`, `running`, `alarm`, `filter_supply`, `filter_extract`, `mode`, `status`.
Leave out what the unit does not have. "Running" is `running`, else `supply_fan_run`, else `supply_fan` > 0.
BACnet multi-state values are numbers (1, 2, 3 …): `status_texts` shows a text for the `status` value (a
Climatix `ActOpSta` 12 = "Time schedule"), and `alarm_normal` gives the `alarm` value that means "no alarm"
(an alarm class with 1 = Normal, 2 = Alarm – without it every value above 0 counts as an alarm). The texts of a
device can be read with `property: state-text` on a tag.
A week program is shown when `week_start_hour` and `week_stop_hour` are set: arrays with one value per day,
Monday first (`week_start_minute`, `week_stop_minute`, `week_enabled` optional), and `overtime` (on/off).

#### Piping diagrams (`type: diagram`)

Water-borne plants look different on every site, so their drawing is written as text, the way an HMI page is
drawn, and the live values are put into it:

```yaml
    - name: "=320.002 Floor heating"
      type: diagram
      connection: plc1
      prefix: "320_002_"
      diagram: |2
         supply ━━━━ SB501 {valve } ━━ {p1 } JP401 ━━━━━━━━▶ RT401 {t_sup  }
         return ◀══════════════════════════════════════════ RT501 {t_ret  }
      fields:
        valve: {tag: RelPos, connection: valve1, decimals: 0}   # a tag of another connection
        p1: {kind: pump, run: JP401_V, alarm: [JP401_AL], safety: JP401_SB}
        t_sup: {tag: RT401_PV, alarm: [RT401_L_AL, RT401_H_AL]}
        t_ret: {tag: RT501_PV}
      charts:                           # the 24 h charts: legend → field
        temperatures: {"Supply": t_sup, "Return": t_ret}
        outputs: {"Valve %": valve}
```

- `{key}` is replaced by the field *key*, as wide as the placeholder itself (braces included): spaces inside the
  braces widen the field, so the drawing keeps its columns. Write `diagram: |2` so the first line may start
  with spaces.
- Heavy box characters (`━ ┃ ┏ ┓ ┗ ┛ ┣ ┫ ┳ ┻ ╋`) are supply pipes (red), double ones (`═ ║ ╔ ╗ ╚ ╝ ╠ ╣ ╦ ╩ ╬`)
  return or cold water (blue); arrows `▶ ◀ ▲ ▼` take the colour of the pipe before them.
- Field kinds: `value` (default; `decimals`, `unit: false`), `pump` (`run`, `alarm`, `safety`), `state` (an
  on/off tag shown as `on`/`off` texts) and `text` (`texts` for the numbers of a multi-state tag). `alarm` is a
  tag or a list: any of them on draws the field red. Tags get the entry's `prefix`; a field with its own
  `connection` reads that connection without prefix (or with its own `prefix`). `align` (`left` / `center` /
  `right`, default `center`) places the text inside the field.
- `groups`, `settings` and `hide` work as for an air handling unit (the Settings tab).

A meter uses the tag names of the device templates (`Total active power`, `Total active energy`, `L1 current` …,
`L1 voltage` …, `Power factor`, `Frequency`); `tags:` overrides single roles (`power`, `energy`, `i1`–`i3`,
`u1`–`u3`, `pf`, `freq`). The curves of a meter need its power and energy tags to be logged (`log` in
`protocols.yaml`); the charts of a unit need its temperatures and outputs to be logged.

`type: panel` is a system without a drawing – e.g. a KNX→BACnet gateway: it gets a menu entry under Systems
and opens on the Settings tab with its tags in `groups` (writable ones with Enter). Roles are not used.

Settings tab: a tag goes into the first of the `groups` it matches. Tags in no group are put into automatic
sections by TFM code (`JV401_OP` → `JV401`) or by the first part of the name (`Sequence_Kp1` → `Sequence`) when at
least two tags share it; the section is named after the words their descriptions start with. The rest is «Other».
The section name – or the title without a trailing TFM code – is left out of each description (`Supply fan output`
in «Supply fan JV401» shows as `Output`). Put titles with a comma in quotes.

### Personal settings

Each user has `~/.config/knxscada/ui.json` (or `$XDG_CONFIG_HOME/knxscada/ui.json`) with their own choice of
colour theme and language. The file can safely be deleted.

<a id="plc"></a>

## `plc` – PLC bridge over OPC UA

Optional. Exchanges KNX values with a PLC (e.g. CODESYS on a WAGO PFC). Guide: [PLC bridge](10-plc-opcua.md).

| Key | Default | Description |
|---|---|---|
| `enabled` | `false` | Start the bridge with the server. With `controllers` (below), each PLC is on unless it or `plc.enabled` says `false`. |
| `name` | `plc` | Name of the PLC in the top bar and the event log |
| `controllers` | – | Several PLCs (see below) |
| `endpoint` | `opc.tcp://127.0.0.1:4840` | The PLC's OPC UA server |
| `username`, `password` | – | User of the PLC runtime. Without `username` the connection is anonymous. |
| `security` | `None` | `None`, or `<policy>,Sign` / `<policy>,SignAndEncrypt` with policy `Basic256Sha256`, `Aes128Sha256RsaOaep` or `Aes256Sha256RsaPss` |
| `certificate`, `private_key` | `var/opcua/client_cert.der`, `var/opcua/client_key.pem` | Client certificate for `security`; created automatically (the key with mode 600). The PLC must trust it. |
| `server_certificate` | – | The PLC's OPC UA server certificate (`.der`/`.pem`, e.g. exported from CODESYS). With it the bridge only talks to that PLC; without it the certificate the PLC offers is accepted, so a device on the network could pretend to be the PLC. Recommended with `security`. |
| `map` | `plc_map.yaml` | Which group addresses are exchanged, their direction and PLC variable names (`./scada plc-map`) |
| `gvl` | `GVL_KNX` | Name of the GVL in the PLC. Only used when `plc_map.yaml` is first created; after that, `gvl:` at the top of `plc_map.yaml` counts. |
| `gvl_file` | `<gvl>.st` | Where `./scada plc-map` writes the GVL |
| `node_prefix`, `namespace` | found automatically | Node id prefix of the GVL variables (e.g. `\|var\|<device>.Application.GVL_KNX.`) and its namespace index – only needed if the automatic search does not find the GVL. With `node_prefix` and no `namespace`, the namespace is `4`. |
| `publish_interval_ms` | `200` | How often the PLC reports changes |
| `min_interval_ms` | `200` | At most one KNX write per address in this time; the latest value wins |
| `timeout_s` | `10` | Connection timeout |
| `missing_recheck_s` | `60` | How often to check whether variables missing in the PLC have appeared (after an online change) |
| `settle_ms` | `500` | PLC changes are held this long before they are sent to KNX (to recognise a PLC restart) |
| `burst_limit` | `8` | More variables than this changing within `settle_ms` = a PLC restart (download, reset): nothing is sent. `0` = off |
| `restart_quiet_s` | `15` | After a PLC restart, changes are only remembered for this long; then the PLC gets the KNX values again |
| `tag_prefix` | – | Name variables after the system tag of tagged PLC objects in ETS (see [PLC bridge](10-plc-opcua.md)) |

### Several PLCs

List them under `controllers`. Keys directly under `plc:` are then defaults for every PLC (user, security,
timing …); `name`, `endpoint`, `map`, `gvl`, `gvl_file`, `node_prefix`, `namespace`, `server_certificate` and
`enabled` belong to each PLC.

```yaml
plc:
  enabled: true
  username: knx                         # default for all PLCs
  password: "secret"
  controllers:
    - name: pfc300                      # letters, digits, _ – shown in the top bar and the event log
      endpoint: opc.tcp://192.168.1.100:4840
    - name: board2
      endpoint: opc.tcp://192.168.1.101:4840
      gvl: GVL_KNX_Board2
      # enabled: false                  # keep it configured, but do not connect
```

`enabled` on a PLC wins; without it, `plc.enabled` counts, and without that the PLC is **on**. Give each PLC its own
`gvl` – two PLCs with the default `GVL_KNX` would write the same `GVL_KNX.st`.

Each PLC has its own mapping, by default `plc_map_<name>.yaml`, and its own GVL; `./scada plc-map` creates or
updates all of them (`--plc board2` for one), `./scada plc-browse --plc board2` checks one. Every PLC reads and
writes KNX independently, with all the rules of the bridge. Two PLCs can write the same group address – the
latest write wins, so give such an address `dir: both` in one PLC only. The top bar shows one dot per PLC, and
PLC writes appear in the event log with the user `plc:<name>`.

