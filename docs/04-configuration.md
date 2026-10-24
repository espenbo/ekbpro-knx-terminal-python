# Configuration (`config.yaml`)

`config.yaml` lives in the program directory. Choose another file with `-c file.yaml` or `KNXSCADA_CONFIG=file.yaml`.
Relative paths are resolved from the directory the file is in. The template is [`config.example.yaml`](../config.example.yaml).

Changes to `knx`, `project`, `server` and `alarms` require a restart of the server. Changes to `ui` require a
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
| `keyring_file` | – | `.knxkeys` from ETS. Needed for KNX Secure. |
| `keyring_password` | – | Password of the keyring file |
| `user_id` | – | Tunnel user for secure tunneling |

**Tunneling or routing?** An IP interface has a limited number of tunnels, often 1–5. The program uses
one tunnel regardless of how many users are logged in. With an IP router you can use `routing` and use
no tunnel at all.

`tunneling_secure` uses secure tunneling over TCP.

## `project` – ETS project

| Key | Default | Description |
|---|---|---|
| `file` | – | Path to the `.knxproj` |
| `password` | – | Password if the project is protected |
| `cache` | `var/project_cache.json` | The parsed project. Rebuilt automatically when the `.knxproj` is newer. |

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
