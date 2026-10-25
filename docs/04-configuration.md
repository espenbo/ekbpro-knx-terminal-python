# Configuration (`config.yaml`)

`config.yaml` lives in the program directory. Choose another file with `-c file.yaml` or `KNXSCADA_CONFIG=file.yaml`.
Relative paths are resolved from the directory the file is in. The template is [`config.example.yaml`](../config.example.yaml).

Changes to `knx`, `project`, `server`, `alarms` and `plc` require a restart of the server. Changes to `ui` require a
restart of the visualisation. `ui.language` is also used by the server (for event log and alarm texts), so
restart the server as well when you change it.

## `knx` – connection

| Key | Default | Description |
|---|---|---|
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
  keyring_file: MyProject.knxkeys      # keys at this level are defaults for every interface
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
| `name` | `gateway_ip` | Letters, digits, `-`, `_`, `.`; at most 16 characters. |
| `connection`, `gateway_ip`, `gateway_port`, `local_ip`, `route_back`, `individual_address`, `keyring_file`, `keyring_password`, `user_id` | | As above, for this interface. |
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
| `files` | – | Several ETS projects in one site, instead of `file` (see below) |
| `rooms` | – | Rooms from the group address names (see below) |

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
```

- The text *before* the room is the row label ("Actual temp", "Switching status"); a name that starts with the room
  ("Office 1 Request") has its label after it.
- The text *after* the room is the fixture ("ceiling", "left"). A room gets one card per main group and
  fixture, titled with the main group name without the floor words: "Lighting 2nd floor" + "ceiling" on floor
  "2nd floor" → **Lighting ceiling**; "Heating 2nd floor" → **Heating**.
- Control and status addresses are paired as usual ("Switching Office 1 ceiling" + "Switching status Office 1 ceiling").
- Addresses without a room in the name are shown as before, from the group address structure.
- Ignored when the project has ETS functions. With several projects, `rooms` applies to all of them; an entry
  in `files` can have its own `rooms`.

Run `./scada import` after changing it (`./scada info` shows the rooms).

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
| `file` | required | Path to the `.knxproj` / `.knxprojarchive` |
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
| `read_on_start` | `false` | Read values from the bus when the server starts: `false` (send no telegrams; the last values are taken from the database), `status` (feedback, sensor and alarm addresses only) or `all` (see the warning below). `true` means `status`. |
| `read_interval_ms` | `80` | Pause between the reads, so the bus is not overloaded |

**Why reading at start-up is off by default, and `all` is not recommended.** A GroupValueRead on a *control*
address (e.g. "Hall light switching") is answered by every device that has the read flag (R) on that address –
also by a push button or logic module that still holds an old value. Actuators whose object has the update
flag (U) treat such a response like a write, so a read can switch a light off. `status` therefore never reads
an address that is used to control something; it only reads feedback, sensor and alarm addresses. With `false`
the server sends nothing at start-up: it logs all bus traffic around the clock, so the last values in the
database are normally up to date, and every value is refreshed with the next telegram.

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

`address` is accepted as an alias for `match_address`. Placeholder addresses and addresses without any linked
communication object are never monitored.

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
| `language` | `en` | Language: `en` (English) or `nb` (Norwegian bokmål; `no` is accepted as an alias). Also the language of the event log and alarm texts the server generates. Each user's own choice (Ctrl+L) and `./scada tui --language` take precedence in the visualisation. |
| `theme` | `retro` | Default theme: `retro`, `retro-classic`, `amber`, `modern`, `solarized`. The user's own choice (Ctrl+T) takes precedence. The old names `retro-klassisk` and `moderne` are still accepted. |
| `splash` | `true` | Start-up logo |
| `big_digits` | `false` | Large block digits in status tiles and screensaver. Normal text is the easiest to read. |
| `ascii` | `false` | Only ASCII characters in charts, for older terminals |
| `screensaver_minutes` | `5` | Minutes without input before the screensaver starts. `0` turns it off. |
| `screensaver_rotate_s` | `20` | Seconds per screensaver page |
| `plan` | `plan.yaml` | The floor plan file |
| `outdoor_ga` | auto | Group address of the outdoor temperature. Auto finds a DPT 9 address whose name starts with "ute temp", "outdoor" or "outside". |
| `power_ga` | auto | Power (DPT 14.056) for the status tile. Auto finds a name ending in "power" or "W", or containing "effekt". |
| `energy_ga` | auto | Energy meter (DPT 13.x). Auto finds a name containing "kwh" or "energy". |
| `setpoint_step` | `0.5` | Step for − / + on setpoints |
| `dim_step` | `10` | Step for − / + on dimmers |
| `confirm_raw_write` | `true` | Confirmation before writing from the group address view |

An unknown `language` falls back to English.

### Personal settings

Each user has `~/.config/knxscada/ui.json` (or `$XDG_CONFIG_HOME/knxscada/ui.json`) with their own choice of
colour theme and language. The file can safely be deleted.

<a id="plc"></a>

## `plc` – PLC bridge over OPC UA

Optional. Exchanges KNX values with a PLC (e.g. CODESYS on a WAGO PFC). Guide: [PLC bridge](10-plc-opcua.md).

| Key | Default | Description |
|---|---|---|
| `enabled` | `false` | Start the bridge with the server |
| `endpoint` | `opc.tcp://127.0.0.1:4840` | The PLC's OPC UA server |
| `username`, `password` | – | User of the PLC runtime. Without `username` the connection is anonymous. |
| `security` | `None` | `None`, or `<policy>,Sign` / `<policy>,SignAndEncrypt` with policy `Basic256Sha256`, `Aes128Sha256RsaOaep` or `Aes256Sha256RsaPss` |
| `certificate`, `private_key` | `var/opcua/client_cert.der`, `var/opcua/client_key.pem` | Client certificate for `security`; created automatically (the key with mode 600). The PLC must trust it. |
| `server_certificate` | – | The PLC's OPC UA server certificate (`.der`/`.pem`, e.g. exported from CODESYS). With it the bridge only talks to that PLC; without it the certificate the PLC offers is accepted, so a device on the network could pretend to be the PLC. Recommended with `security`. |
| `map` | `plc_map.yaml` | Which group addresses are exchanged, their direction and PLC variable names (`./scada plc-map`) |
| `gvl` | `GVL_KNX` | Name of the GVL in the PLC. Only used when `plc_map.yaml` is first created; after that, `gvl:` at the top of `plc_map.yaml` counts. |
| `gvl_file` | `<gvl>.st` | Where `./scada plc-map` writes the GVL |
| `node_prefix`, `namespace` | found automatically | Node id prefix of the GVL variables (e.g. `\|var\|<device>.Application.GVL_KNX.`) and its namespace index – only needed if the automatic search does not find the GVL |
| `publish_interval_ms` | `200` | How often the PLC reports changes |
| `min_interval_ms` | `200` | At most one KNX write per address in this time; the latest value wins |
| `timeout_s` | `10` | Connection timeout |
| `missing_recheck_s` | `60` | How often to check whether variables missing in the PLC have appeared (after an online change) |
| `settle_ms` | `500` | PLC changes are held this long before they are sent to KNX (to recognise a PLC restart) |
| `burst_limit` | `8` | More variables than this changing within `settle_ms` = a PLC restart (download, reset): nothing is sent. `0` = off |
| `restart_quiet_s` | `15` | After a PLC restart, changes are only remembered for this long; then the PLC gets the KNX values again |
| `tag_prefix` | – | Name variables after the system tag of tagged PLC objects in ETS (see [PLC bridge](10-plc-opcua.md)) |

