# Setup reference (for people and AI assistants)

This page has everything needed to set up the program from scratch: which files exist, complete examples,
every key with its type, default and valid values, tasks step by step and the commands that check the result.
It is written so that an AI assistant can do the setup by following it. The other pages explain the *why*; this
page is the checklist.

## Rules for setting up

1. **Only use keys listed on this page.** Unknown keys are ignored without a warning – a misspelt key silently
   gets its default.
2. **Paths are relative to the folder `config.yaml` is in**, not to the current folder (except the output file of
   `plan-dxf`). `~` works.
3. **Quote strings that contain `*`, `:`, `{`, `#`, `?` or start with a digit and a slash**: `"0/6/*"`,
   `"3/1/0"`, `"(?i)alarm$"`. In regular expressions in double quotes, write `\\b` for `\b`.
4. **YAML turns some words into booleans**: `on`, `off`, `yes`, `no`, `true`, `false`. Quote them when you mean
   text (`condition: "true"` is fine either way, but a room called `No` must be `"No"`).
5. **Numbers must be numbers**: `dpt_main: 1`, not `"1"`.
6. **Secrets** (KNX keyring password, PLC password, MQTT/OPC UA passwords) are in `config.yaml` and
   `protocols.yaml`. Both are written with mode 600 by the program; keep it that way.
7. **Restart after changes**: `knx`, `project`, `server`, `alarms`, `plc` → restart the server. `ui` → restart the
   TUI. `protocols.yaml` → saved in F11 it applies at once; edited by hand, press `r` in F11 or restart the
   server. `plan.yaml` → re-read every time F8 opens.
8. **Check after every change** with the commands in [Check the setup](#check-the-setup). Never report a setup as
   done before `./scada info` and the server log are clean.
9. **Never change these without being asked:** the KNX bus (no test writes), the PLC program, systemd units
   (need `sudo`), and `read_on_start: all` (can switch actuators).

## Files

| File | Where | Written by | What it holds |
|---|---|---|---|
| `config.yaml` | program folder, or `sites/<site>/` | `./scada init`, by hand | KNX connection, ETS project, server, alarms, UI, PLC bridge |
| `*.knxproj` / `*.knxprojarchive` | next to `config.yaml` | ETS export | The ETS project |
| `protocols.yaml` | `var/` (`server.protocols: var/protocols.yaml`) | F11, by hand | Modbus, MQTT, OPC UA connections and gateways |
| `plan.yaml` | next to `config.yaml` (`ui.plan`) | by hand | Floor plan for F8 |
| `plc_map.yaml`, `GVL_KNX.st` | next to `config.yaml` | `./scada plc-map`, then by hand (`use:`) | PLC bridge: which addresses, direction, variable names |
| `var/` | next to `config.yaml` | the server | `knxscada.sock`, `knxscada.db`, `project_cache.json`, `opcua/` certificates |
| `~/.config/knxscada/ui.json` | each user's home | the TUI | That user's colour theme and language |

Templates in the program folder: `config.example.yaml`, `protocols.example.yaml`, `plan.example.yaml`.

**One site or several.** A machine with one installation keeps `config.yaml` in the program folder (service
`knxscada`). A machine with several keeps each one in `sites/<site>/` with its own `var/` (service
`knxscada@<site>` or `knxscada-<site>`), and every command takes `-c sites/<site>/config.yaml`. A wrapper
`/usr/local/bin/knx-<site>` containing `exec <program folder>/scada -c <program folder>/sites/<site>/config.yaml "$@"`
makes `knx-<site>` open that site's TUI and `knx-<site> info` run its commands.

## Commands

All commands: `./scada [-c CONFIG] <command>`. Without `-c` the file is `$KNXSCADA_CONFIG`, else `config.yaml` in
the program folder. Without a command, `tui` starts.

| Command | Options | Does |
|---|---|---|
| `tui` | `--language en\|nb` | The terminal user interface (needs a running server) |
| `server` | `--demo` (simulated bus, own database), `-v` (debug log) | KNX, logging, alarms, PLC bridge, protocols, socket |
| `init PROJECT` | `--gateway IP`, `--connection TYPE`, `--password PW`, `--language en\|nb`, `--force` | Writes `config.yaml` from the template and imports the project |
| `import` | | Re-reads the ETS project(s), rebuilds the cache |
| `info` | | Summary of the project: addresses, cards, floors and rooms |
| `plan-dxf [FILE]` | | Floor plan to DXF (default `plan.dxf` in the current folder) |
| `plc-map` | `--all`, `--marked`, `--rename`, `--plc NAME` | Writes `plc_map.yaml` and the GVL |
| `plc-browse [FILTER]` | `--endpoints`, `--plc NAME` | Lists the PLC's OPC UA variables / endpoints |
| `plc-gateway` | `--plc NAME`, `--prefix`, `--namespace`, `--write` | The PLC bridge (`plc:` + `plc_map.yaml`) as an OPC UA connection and gateways; printed, or added to protocols.yaml with `--write` |
| `modules` | `install NAME …` | Lists the protocol modules, or installs them |
| `gateway` | | Checks `protocols.yaml`, with live status when the server runs |
| `simulate [MODEL]` | `--host`, `--port` (5020), `--unit` (1), `--device` | Plays a device template as a Modbus slave, for tests without equipment; without a model it lists the templates |
| `bacnet-scan [ADDRESS]` | `--device N`, `--local`, `--port`, `--bbmd`, `--timeout` (3), `--yaml` | Without an address: the BACnet devices on the network (Who-Is); with one: the objects of the device, `--yaml` as a connection for `protocols.yaml` |

`./setup.sh [knx modbus mqtt opcua bacnet]` creates `venv/`, installs the base packages and the named protocol
modules (none named: it asks, default `knx`), and runs `./scada import` when `config.yaml` exists and KNX is
installed. Base packages: `textual`, `textual-plotext`, `PyYAML`. Modules: `knx` (`xknx`, `xknxproject`), `modbus`,
`mqtt`, `opcua` (`asyncua`, also needed by the PLC bridge `plc:`), `bacnet`.

## Tasks

### A. New installation with KNX

```bash
git clone <repository> ~/knx_scada && cd ~/knx_scada
./setup.sh knx                               # venv, base packages and the KNX module; add modbus mqtt opcua if needed
./scada init ~/MyHouse.knxproj --gateway 192.168.1.50 --language nb
./scada info                                 # check: addresses, cards and rooms are there
./scada server                               # first test in a terminal; Ctrl+C stops it
./scada                                      # TUI in a second terminal
```

`--gateway` empty = automatic discovery. `--connection`: `tunneling` (default with a gateway), `tunneling_tcp`,
`routing`, `automatic`, `tunneling_secure`, `routing_secure` – it is not checked by `init`, a typo only shows when
the server starts. Then install the service: [Operation and SSH](05-operation-and-ssh.md).

### B. Another site on the same machine

```bash
mkdir -p sites/cabin && cp ~/Cabin.knxproj sites/cabin/
./scada -c sites/cabin/config.yaml init sites/cabin/Cabin.knxproj --gateway 10.0.0.20
./scada -c sites/cabin/config.yaml info
sed -e "s#YOUR_USER#$USER#g" -e "s#INSTALL_DIR#$PWD#g" deploy/knxscada@.service \
    | sudo tee /etc/systemd/system/knxscada@.service
sudo systemctl daemon-reload && sudo systemctl enable --now knxscada@cabin
journalctl -u knxscada@cabin -n 30          # check: "Server started (KNX)", KNX connected
```

### C. KNX Secure

Export the keyring from ETS (*Project → Security → Export keyring*), put it next to `config.yaml`:

```yaml
knx:
  connection: tunneling_secure     # or routing_secure
  gateway_ip: 192.168.1.50
  keyring_file: MyHouse.knxkeys
  keyring_password: "secret"
  # user_id: 2                     # tunnel user, optional
```

With `keyring_file` set, the connection is secure whatever `connection` says.

### D. Run without KNX (only Modbus/MQTT/OPC UA/BACnet)

Install without the KNX module – `./setup.sh modbus opcua` (or the modules you need) – so `xknx` and `xknxproject`
are not installed at all.

```yaml
# config.yaml
knx: {enabled: false}
server:
  socket: var/knxscada.sock
  database: var/knxscada.db
  protocols: var/protocols.yaml
ui:
  title: Energy gateway
  language: en
```

Then task E, and start with `./scada server`. The TUI opens on F11.

### E. Energy meter on RS485 to an OPC UA server

```bash
./scada modules install modbus opcua
```

```yaml
# var/protocols.yaml
connections:
  meter1:
    protocol: modbus
    device: /dev/ttyUSB0                 # the RS485 adapter
    model: WAGO 879-3000                 # tags and serial settings from the device template
    # unit_id: 2                         # only if the meter's Modbus address is not 1
  scada:
    protocol: opcua
    role: server
    endpoint: opc.tcp://0.0.0.0:4841     # 0.0.0.0 = reachable from the network

gateways:
  - name: Meter to SCADA
    from: meter1
    to: scada
    map:                                 # template tag name -> name in your tagging system
      - Total active power -> 320.001-OE001.P
      - Total active energy -> 320.001-OE001.E
      - L1 current -> 320.001-OE001.I1
```

Check: `./scada gateway` (no `!` lines), restart the server, then the event log shows
`meter1: found WAGO 879-3000 …`; F11 shows the values. The user running the server must be allowed to open the
serial port (group `dialout`).

### F. PLC bridge (KNX ↔ CODESYS over OPC UA)

```bash
./scada plc-map                          # writes plc_map.yaml and GVL_KNX.st
# edit plc_map.yaml: use: true on the addresses the PLC needs, then:
./scada plc-map                          # updates the GVL
./scada plc-browse --endpoints           # the PLC answers? which security?
```

Import `GVL_KNX.st` into the PLC project, add it to the Symbol Configuration, download, then:

```yaml
plc:
  enabled: true
  endpoint: opc.tcp://192.168.1.100:4840
  username: knx
  password: "secret"
```

Restart the server. Check: `./scada plc-browse GVL_KNX` lists the variables; the top bar shows `PLC` in green.
Guide: [PLC bridge](10-plc-opcua.md).

### G. Alarms

Add rules under `alarms:` (first match wins, specific rules first), restart the server, and list which
addresses each rule catches with the one-liner in [Configuration → Alarms](04-configuration.md#alarms).

## Complete `config.yaml`

Every key, with its default where it has one. Keys marked *optional* can be left out.

```yaml
knx:
  enabled: true                       # optional; false = no KNX (protocols.yaml only)
  connection: automatic               # tunneling | tunneling_tcp | routing | automatic | tunneling_secure | routing_secure
  gateway_ip: 192.168.1.50            # not needed for routing/automatic
  gateway_port: 3671
  local_ip: 192.168.1.10              # optional; machines with several networks
  route_back: false                   # true behind NAT/VPN (tunneling only)
  individual_address: 1.1.250         # optional; routing
  keyring_file: MyHouse.knxkeys       # optional; KNX Secure
  keyring_password: "secret"
  user_id: 2                          # optional; secure tunnel user
  name: knx                           # optional; shown in the TUI
  dedup_ms: 1000                      # several interfaces: drop copies within this time; 0 = off
  # interfaces:                       # instead of the connection keys above, for several IP interfaces
  #   - name: house                   # ^[A-Za-z0-9_.-]{1,16}$, unique
  #     gateway_ip: 192.168.1.50      # gateway_ip, gateway_port, individual_address: per interface only
  #     default: true                 # the interface for addresses no pattern claims
  #   - name: garage
  #     gateway_ip: 192.168.1.51
  #     addresses: ["4/*", "5/1/*"]   # sent through this interface
  #     project: annex                # with project.files

project:
  file: MyHouse.knxproj               # or .knxprojarchive
  password: ""                        # optional
  cache: var/project_cache.json
  # files:                            # several ETS projects, instead of file:
  #   - {name: main, label: Main building, file: Main.knxproj}
  #   - {name: annex, label: Annex, prefix: S, file: Annex.knxprojarchive, password: ""}
  # rooms:                            # rooms from group address names (projects without ETS functions)
  #   pattern: "(?i)\\b(office\\s*\\d+|kitchen)\\b"
  #   floors: {0: "1st floor", 1: "2nd floor"}
  #   names: {"1st floor/Office 1": "Office 1 · RT101"}
  #   setpoint: "(?i)set temp"
  #   read_only: "(?i)desired temp|valve"
  #   exclude: ["2/*/46"]
  # extra_rooms:                      # rooms for addresses no ETS room holds
  #   - floor: Attic
  #     name: Attic
  #     cards:
  #       - {name: Temperature, addresses: ["3/0/35"]}
  #       - {name: Light, addresses: ["1/0/5", "1/0/7"], status: {"1/0/5": "1/4/81"}, usage: light}

server:
  socket: var/knxscada.sock
  database: var/knxscada.db
  retention_days: 90
  read_on_start: status               # status | false | all
  read_interval_ms: 80
  protocols: var/protocols.yaml     # in var/: the service may only write there

alarms:
  - match_address: "0/6/*"            # optional glob (alias: address)
    match_name: "(?i)smoke.*alarm"    # optional regex on the GA name
    exclude_name: "(?i)acknowledg"    # optional regex
    dpt_main: 1                       # optional number
    condition: "true"                 # true | false | > x | < x | >= x | <= x | == x | != x
    severity: critical                # critical | warning | any text
    text: "Smoke: {name}"             # {name} {address} {value}
  - match_point: "aggregat1/*_AL"     # a tag of protocols.yaml (connection/tag glob) instead of a group address
    severity: warning
    delay_s: 60                       # optional: the condition must last this long

ui:
  title: My house                     # default: "EKBPRO KNX Terminal Python – <ETS project name>"
  language: en                        # en | nb
  theme: retro                        # retro | retro-classic | amber | modern | solarized | storm
  logo_file: my-logo.txt              # optional
  splash: true
  big_digits: false
  ascii: false
  screensaver_minutes: 5              # 0 = off
  screensaver_rotate_s: 20
  screensaver_pages: [clock, sky, home, heatmap, ventilation, meters, records]   # default: all pages
  latitude: 59.22                     # sun and moon on the page "sky" (default Fredrikstad)
  longitude: 10.93
  plan: plan.yaml
  export_dir: "~"
  outdoor_ga: 0/1/0                   # optional; found by name when left out
  power_ga: 0/0/23                    # optional
  energy_ga: 0/1/20                   # optional
  setpoint_step: 0.5
  dim_step: 10
  confirm_raw_write: true

plc:
  enabled: false
  name: plc
  endpoint: opc.tcp://127.0.0.1:4840
  username: knx                       # optional; empty = anonymous
  password: "secret"
  security: None                      # None | <policy>,Sign | <policy>,SignAndEncrypt
  certificate: var/opcua/client_cert.der
  private_key: var/opcua/client_key.pem
  server_certificate: var/opcua/plc_cert.der   # optional; recommended with security
  map: plc_map.yaml
  gvl: GVL_KNX
  gvl_file: GVL_KNX.st
  tag_prefix: BY1234                  # optional
  node_prefix: "|var|WAGO 750-8302 PFC300 2ETH RS.Application.GVL_KNX."   # optional; found automatically
  namespace: 4                        # optional; with node_prefix
  publish_interval_ms: 200
  min_interval_ms: 200
  timeout_s: 10
  missing_recheck_s: 60
  settle_ms: 500
  burst_limit: 8
  restart_quiet_s: 15
  # controllers:                      # several PLCs; keys above are defaults except the per-PLC ones
  #   - {name: pfc300, endpoint: "opc.tcp://192.168.1.100:4840"}
  #   - {name: board2, endpoint: "opc.tcp://192.168.1.101:4840", gvl: GVL_KNX_Board2}
```

## Keys of `config.yaml`

### `knx`

| Key | Type | Default | Valid values |
|---|---|---|---|
| `enabled` | bool | on when `knx:` or `project:` is filled in | only `false` turns KNX off |
| `connection` | text | `automatic` | `tunneling`, `tunneling_tcp`, `routing`, `automatic`, `tunneling_secure`, `routing_secure`. Anything else stops the server. |
| `gateway_ip` | IP | – | |
| `gateway_port` | int | `3671` | |
| `local_ip` | IP | automatic | |
| `route_back` | bool | `false` | |
| `individual_address` | text | – | `1.1.250` |
| `keyring_file` | path | – | `.knxkeys`; turns on KNX Secure |
| `keyring_password` | text | – | |
| `user_id` | int | – | |
| `name` | text | `knx` | `^[A-Za-z0-9_.-]{1,16}$` |
| `dedup_ms` | int | `1000` | `0` = off |
| `interfaces` | list | – | see below |

`interfaces[]`: `name` (default `gateway_ip`, else `knx1` …; unique), `gateway_ip`, `gateway_port`,
`individual_address` (never inherited), `connection`, `local_ip`, `route_back`, `keyring_file`, `keyring_password`,
`user_id`, `dedup_ms` (inherited from `knx:`), `addresses` (glob or list of globs, default none), `default` (bool,
at most one per project), `project` (a `project.files[].name`, default the first project).

### `project`

| Key | Type | Default | Valid values |
|---|---|---|---|
| `file` | path | `project.knxproj` | ignored when `files` is set |
| `password` | text | – | not inherited by `files[]` |
| `cache` | path | `var/project_cache.json` | |
| `files` | list | – | entries: `{file (required), name (p1, p2 …), label (the name), prefix (the name; letters/digits; never on the first), password, rooms}` or just the path |
| `rooms` | map | – | `pattern` (required), `floors`, `names`, `setpoint`, `read_only`, `exclude`; only used when the project has no ETS functions |
| `extra_rooms` | list | – | entries: `floor` (`Other`), `name` (`Room N`), `cards[]`: `name`, `addresses`, `status`, `usage` (`light`, `dimmer`, `heating`, `other`; guessed). An address that is not in the project stops the program. |
| `point_rooms` | list | – | like `extra_rooms` with `building` (groups the menu), and `cards[].rows[]`: `point` (`conn/tag`, required), `status`, `label` (tag description), `role` (`temp`, `setpoint`, `heat`), `kind` (from the tag). Unknown tags are reported, not fatal. See [04](04-configuration.md#point-rooms). |

### `server`

| Key | Type | Default | Valid values |
|---|---|---|---|
| `socket` | path | `var/knxscada.sock` | |
| `database` | path | `var/knxscada.db` | |
| `retention_days` | number | `90` | |
| `read_on_start` | text/bool | `status` | `status` (also `true`), `false` (also `off`, `no`, `0`), `all`. Anything else = `status`. |
| `read_interval_ms` | int | `80` | |
| `protocols` | path | `protocols.yaml` | use `var/protocols.yaml` – the systemd service may only write in `var/` |

### `alarms[]`

| Key | Type | Default | Valid values |
|---|---|---|---|
| `match_address` (`address`) | glob | all | `"0/6/*"`, `"S:3/*"` |
| `match_name` | regex | all | searched in the GA name; a broken regex stops the server |
| `exclude_name` | regex | – | |
| `dpt_main` | int | all | `1`, `9`, `14` … |
| `condition` | text | `"true"` | `true`/`on`/`1`, `false`/`off`/`0`, `> x`, `< x`, `>= x`, `<= x`, `== x`, `!= x`. Anything else never fires. |
| `severity` | text | `warning` | `critical`, `warning` (`kritisk`, `advarsel`), any text |
| `text` | text | `"{name}"` | `{name}`, `{address}`, `{value}` |
| `match_point` | glob | – | `"aggregat1/*_AL"`: the rule applies to tags of `protocols.yaml` (`connection/tag`), not to group addresses; `match_name`/`exclude_name` are then matched against the tag name and its description |
| `delay_s` | float | `0` | seconds the condition must last before the alarm is raised |

### `ui`

| Key | Type | Default | Valid values |
|---|---|---|---|
| `title` | text | program name – ETS project name | |
| `language` | text | `en` | `en`, `nb` (`no` = `nb`); unknown = `en` |
| `theme` | text | `retro` | `retro`, `retro-classic`, `amber`, `modern`, `solarized`, `storm` |
| `logo_file` | path | built-in | text file with `█ ▀ ▄ ▒` |
| `splash`, `big_digits`, `ascii`, `confirm_raw_write` | bool | `true`, `false`, `false`, `true` | |
| `building` | text | `Main building` | name of the ETS floors' building in the menu when `point_rooms` add another |
| `systems` | list | – | air handling units for the Systems view (Ctrl+E) and energy meters for the Energy meters view (Ctrl+G), see [04](04-configuration.md#systems-view-ctrle). Per unit: `name`, `short` (menu entry; default `=NNN.NNN` from the name), `type` (`ventilation` / `diagram` / `panel` / `energy`), `connection`, `prefix`, `meter`, `tags` (roles), `mode_texts`, `status_texts` (value → text), `alarm_normal` (number), `type: diagram`: `diagram` (text with `{field}`), `fields` (`kind` value/pump/state/text, `tag`, `connection`, `prefix`, `decimals`, `unit`, `alarm`, `run`, `safety`, `on`, `off`, `texts`, `align`), `charts` (`temperatures`/`outputs`: legend → field), `settings` (glob list for the Settings tab; default all tags), `hide` (glob list without prefix), `groups` (list of `{title, tags}`; globs without prefix); `type: energy`: `meters` (`name`, `connection`, `tags`) |
| `screensaver_minutes`, `screensaver_rotate_s` | number | `5`, `20` | `0` = no screensaver |
| `screensaver_pages` | list | all | `clock`, `home`, `sky`, `temps`, `ventilation`, `heatmap`, `energy`, `meters`, `rain`, `air`, `records`, `knx`, `bounce`; `ventilation`, `rain` and `bounce` move (more traffic over SSH) |
| `latitude`, `longitude` | number | `59.22`, `10.93` | decimal degrees, for the page `sky` |
| `plan` | path | `plan.yaml` | |
| `export_dir` | path | `~` | |
| `outdoor_ga`, `power_ga`, `energy_ga` | group address | found by name and DPT | without them: the unit's `outdoor` tag and the meters in `ui.systems` |
| `setpoint_step`, `dim_step` | number | `0.5`, `10` | |

### `plc`

| Key | Type | Default | Valid values |
|---|---|---|---|
| `enabled` | bool | `false`; with `controllers` each PLC is on unless it or `plc.enabled` says `false` | |
| `name` | text | `plc` | `^[A-Za-z_][A-Za-z0-9_]*$`; required and unique in `controllers` |
| `controllers` | list | – | per PLC only: `name`, `endpoint`, `map`, `gvl`, `gvl_file`, `node_prefix`, `namespace`, `server_certificate`, `enabled`; other keys come from `plc:` |
| `endpoint` | text | `opc.tcp://127.0.0.1:4840` | |
| `username`, `password` | text | anonymous | |
| `security` | text | `None` | `None`, `<policy>,Sign`, `<policy>,SignAndEncrypt`; policy `Basic256Sha256`, `Aes128Sha256RsaOaep`, `Aes256Sha256RsaPss` |
| `certificate`, `private_key` | path | `var/opcua/client_cert.der`, `var/opcua/client_key.pem` | created automatically |
| `server_certificate` | path | – | |
| `map` | path | `plc_map.yaml` (`plc_map_<name>.yaml` with `controllers`) | |
| `gvl` | text | `GVL_KNX` | only used when the map is first created |
| `gvl_file` | path | `<gvl>.st` | |
| `tag_prefix` | text | – | |
| `node_prefix`, `namespace` | text, int | found automatically (`namespace` `4` with `node_prefix`) | |
| `publish_interval_ms`, `min_interval_ms` | int | `200`, `200` | |
| `timeout_s`, `missing_recheck_s` | number | `10`, `60` | |
| `settle_ms`, `burst_limit`, `restart_quiet_s` | number | `500`, `8`, `15` | `burst_limit: 0` = off |

## Keys of `plc_map.yaml`

| Key | Type | Default | Valid values |
|---|---|---|---|
| `select` | text | `marked` | `marked` (only `use: true`), `all` |
| `gvl` | text | `GVL_KNX` | IEC name |
| `addresses[].ga` | group address | – | must be in the project |
| `addresses[].use` | bool | `false` | |
| `addresses[].dir` | text | `to_plc` | `to_plc`, `to_knx`, `both` |
| `addresses[].type` | text | from the DPT | `BOOL`, `REAL`, `SINT`, `USINT`, `UINT`, `INT`, `UDINT`, `DINT`, `STRING(14)` |
| `addresses[].var` | text | `GA_<address>_<name>` | `^[A-Za-z_][A-Za-z0-9_]*$`, no `__`, unique |

## Keys of `plan.yaml`

| Key | Type | Default | Valid values |
|---|---|---|---|
| `floors[].name` | text | `""` | |
| `floors[].width`, `.depth` | mm | `8000` | |
| `floors[].rooms[].x`, `.y`, `.w`, `.h` | mm | **required** | |
| `floors[].rooms[].label` | text | `""` | |
| `floors[].rooms[].room` | text | – | an ETS room name (`./scada info`), any case |

## Complete `protocols.yaml`

```yaml
connections:
  meter1:                             # name: ^[A-Za-z][A-Za-z0-9_-]{0,31}$; "knx" is reserved
    protocol: modbus                  # modbus | mqtt | opcua | bacnet
    role: client                      # client (master) | server (slave)
    model: WAGO 879-3000              # optional device template (list: 11-protocols-gateways.md)
    transport: rtu                    # tcp | rtu | rtu_over_tcp
    host: 192.168.1.20                # tcp: the device (client) / listen address (server)
    port: 502
    device: /dev/ttyUSB0              # rtu
    baudrate: '9600'                  # 1200 2400 4800 9600 19200 38400 57600 115200
    parity: E                         # N | E | O
    stopbits: '1'                     # 1 | 2
    unit_id: 1                        # server: 0 = answer every address
    poll_ms: 5000                     # client; at least 50
    timeout_s: 3                      # client
    word_order: big                   # big | little
    max_registers: 100                # master; at most this many registers per read
    max_gap: 8                        # master; 0 = only neighbouring registers
    log: ["Total active power", "Total active energy"]   # tags stored for trends (names/patterns, "*" = all)
    log_deadband: 0.01                # optional, default 0
    log_min_s: 60                     # optional, default 10 s
    tags:                             # added to the template's tags
      Day register: {address: 'hr:0x6049', type: float32, unit: kWh, writable: false}

  broker:
    protocol: mqtt
    host: 127.0.0.1
    port: 1883
    username: ""
    password: ""
    tls: false
    client_id: ""                     # empty = automatic
    topic_prefix: bygg/
    subscribe: ["sensors/#"]          # topics that become tags by themselves
    payload: plain                    # plain | json
    qos: '0'                          # 0 | 1 | 2
    retain: true
    tags:
      Outdoor: {topic: weather/temp, type: float, json_key: value, unit: °C}

  plc1:
    protocol: opcua
    role: client
    endpoint: opc.tcp://192.168.1.100:4840
    username: ""
    password: ""
    security: None
    publish_interval_ms: 500
    timeout_s: 10
    tags:
      Temp: {node: "ns=4;s=|var|PLC.Application.GVL.rTemp", type: float, unit: °C}

  scada:
    protocol: opcua
    role: server
    endpoint: opc.tcp://0.0.0.0:4841  # left out = only this machine (127.0.0.1)
    namespace: urn:ekbpro:gateway

  ahu1:
    protocol: bacnet
    role: client
    address: 192.168.1.40             # or device_id: 40 (Who-Is); 2:5 = MS/TP 5 behind a router on network 2
    local_address: 192.168.1.10/24    # empty = this machine (client with address: the route to the device)
    poll_ms: 5000
    timeout_s: 3
    cov: true                         # also COV subscriptions
    priority: 8                       # empty = the device's default (16)
    tags:
      Supply air: {object: ai:1, unit: °C}
      Fan on: {object: bo:1, priority: 12}

  bms:
    protocol: bacnet
    role: server
    device_instance: 1001             # required, unique on the network
    device_name: Gateway building A
    local_address: 192.168.1.10/24    # port 47808
    # bbmd: 10.0.0.1:47808             # register as a foreign device

gateways:
  - name: Meter to SCADA              # unique
    from: meter1                      # connection names (knx exists when KNX is on)
    to: scada
    direction: forward                # forward | both (default for the lines)
    # initial: true                   # left out = start values only to own servers; true = also devices (never KNX); false = none
    deadband: 0                       # default for the lines
    min_interval_ms: 0                # default for the lines
    # settle_ms: 500                  # restart guard towards KNX: hold time; 0 = off
    # burst_limit: 8                  # more changes than this from one connection within settle_ms = a restart
    # restart_quiet_s: 15             # after a restart or a bad status: changes only remembered this long
    enabled: true                     # false = off
    map:
      - Total active power -> 320.001-OE001.P                        # source -> target
      - Frequency                                                     # same name in the target
      - {from: L1 current, to: 320.001-OE001.I1, scale: 1, deadband: 0.1}
      - {from: Power factor, to: 320.001-OE001.PF, type: float, description: cos phi}
      - "L? voltage -> Voltage.{name}"                               # wildcards * ? [ ], {name} {address} {description}
```

### Keys of `protocols.yaml`

Connection settings for **Modbus**, **MQTT**, **OPC UA** and **BACnet**: see the tables in
[Protocols and gateways](11-protocols-gateways.md#2-connections) – they are complete there. In short:

| Tag key | Protocols | Default | Valid values |
|---|---|---|---|
| `address` | Modbus | the tag name | `hr:N`, `ir:N`, `co:N`, `di:N`, `hr:N.bit`, hex `0x…`, classic `40001` |
| `type` | Modbus | `uint16` | `bool`, `int16`, `uint16`, `int32`, `uint32`, `float32`, `int64`, `uint64`, `float64` |
| `type` | MQTT, OPC UA | automatic | `bool`, `int`, `float`, `str` |
| `unit_id`, `word_order` | Modbus | the connection's | |
| `write_address` | Modbus | – (written where it is read) | `hr` / `co` (same number), or an address `hr:N`, `co:N` |
| `log`, `log_deadband`, `log_min_s` | all | the connection's | store the tag's values for trends (`log: true`/`false` wins over the connection's `log` patterns) |
| `heartbeat_s` | Modbus master | – (off) | seconds; writes 1, 2, 3 … to the tag (device watchdog) |
| `topic`, `json_key`, `set_topic` | MQTT | prefix + name, –, the topic itself | |
| `node` | OPC UA | the tag name | node id |
| `object`, `property` | BACnet | client: required, `present-value`; server: the next free number | `av:3`, `bi:1`, `analog-value,3`; a property name |
| `priority` | BACnet client | the connection's | 1–16 |
| `states`, `cov_increment` | BACnet server | `16`, `0.1` | for `msv`/`msi` and analog objects |
| `scale`, `offset` | all | – | value = raw × scale + offset |
| `unit`, `description` | all | – | |
| `writable` | all | `true` (Modbus `ir`/`di` read-only unless `write_address`) | |

| Mapping key | Level | Default | Valid values |
|---|---|---|---|
| `from`, `to` | gateway | required | connection names, different |
| `from` | line | required | tag name, address or (KNX) unique ETS name; `* ? [` = wildcards |
| `to` | line | same as `from` | with wildcards a template with `{name}`, `{address}`, `{description}` |
| `direction` | gateway, line | `forward` | `forward`, `both` |
| `initial` | gateway, line | own servers only | `true`, `false` |
| `deadband`, `min_interval_ms` | gateway, line | `0` | numbers |
| `settle_ms`, `burst_limit`, `restart_quiet_s` | gateway | `500`, `8`, `15` | restart guard for changes towards KNX; `settle_ms: 0` = off |
| `scale`, `offset`, `invert`, `type`, `unit`, `description` | line only | – | `type`/`unit`/`description` only count when the target tag is created |
| `enabled` | gateway | `true` | `false` |

Connections have no `enabled` – remove or comment out a connection to turn it off. When F11 saves the file,
comments are removed and keys that are not listed here are dropped.

## Check the setup

| What | Command | Good result |
|---|---|---|
| The ETS project | `./scada info` | `Group addresses: N (M in use)`, `Cards: …`, one line per floor with its rooms |
| The server starts | `./scada server` (or `journalctl -u <service> -n 50`) | `Server started (KNX)`, `KNX connection: CONNECTED`, no traceback |
| The bus | `./scada` → F4 Bus monitor | telegrams arrive |
| Alarm rules | the one-liner in [Configuration → Alarms](04-configuration.md#alarms) | each expected address with its rule |
| Protocol modules | `./scada modules` | every module that a connection uses says `installed` |
| `protocols.yaml` | `./scada gateway` | exit code 0, no lines starting with `!`; with the server running every connection `connected` |
| A device template | event log (F6) | `<connection>: found <model> …`, no error after it |
| The PLC | `./scada plc-browse --endpoints`, then `./scada plc-browse GVL_KNX` | the endpoints, then the GVL variables |
| The PLC bridge | event log and top bar | `PLC` green, `PLC (OPC UA) connected: N variables` |
| The floor plan | `./scada plan-dxf /tmp/plan.dxf` | `Wrote … (N floors, M rooms)` |

There is no single command that checks all of `config.yaml`: `info` checks the project part, `gateway` checks
`protocols.yaml`, and the server checks the rest when it starts (KNX interfaces, PLC settings, `plc_map.yaml`) – read
its log.

## Common mistakes

| Symptom | Cause |
|---|---|
| A setting has no effect | Misspelt or wrongly indented key – unknown keys are ignored |
| An alarm rule never fires | `dpt_main: "1"` (text) or a `condition` that is not one of the valid forms |
| The SCADA cannot reach our OPC UA server | `endpoint` left out or `127.0.0.1` – use `opc.tcp://0.0.0.0:<port>` |
| A Modbus meter does not answer | Wrong `parity`/`baudrate`/`unit_id`, A/B swapped, no termination; the user is not in `dialout` |
| Several PLCs, one should be off | With `controllers`, a PLC is on unless `enabled: false` is set on it or on `plc:` |
| Two PLCs overwrite each other's GVL file | Both use the default `gvl: GVL_KNX` – give each its own |
| The server stops at start-up after an ETS change | `plc_map.yaml` or `extra_rooms` name an address that is gone – run `./scada plc-map`, fix `extra_rooms` |
