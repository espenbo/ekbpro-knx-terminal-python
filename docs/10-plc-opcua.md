# PLC bridge over OPC UA (e.g. CODESYS on a WAGO PFC)

The server can exchange KNX values with a PLC: the PLC **reads** KNX values (temperatures, feedback, alarms) and
**writes** to KNX (setpoints, lights on/off). The server is an OPC UA **client** of the PLC's own OPC UA server,
so the PLC needs no OPC UA client licence and no extra port – only a global variable list (GVL) that is
published in its Symbol Configuration.

```
KNX bus ── KNX IP interface ── server (one tunnel per interface, logging) ── OPC UA ── PLC (CODESYS)
                                                                          GVL_KNX:
                                                                            GA_0_1_0_Outdoor_temp : REAL;
                                                                            GA_1_1_5_Hall_light   : BOOL;
```

Everything goes through the server you already run: one KNX tunnel per interface, every PLC write in the event log (user
`plc`), visible in the bus monitor, and alarms work as usual. The server can run on the PLC itself (see
[Docker](09-docker.md)) or on another machine.

## 1. Choose the group addresses

```bash
./scada plc-map
```

This writes two files next to `config.yaml`:

- `plc_map.yaml` – every group address that can be exchanged, one per line:

  ```yaml
  select: marked
  gvl: GVL_KNX
  addresses:
    - {ga: "0/1/0",   use: false, dir: to_plc, type: REAL,       var: GA_0_1_0_Outdoor_temp}  # Outdoor temp · 9.001
    - {ga: "1/1/5",   use: false, dir: both,   type: BOOL,       var: GA_1_1_5_Hall_light_switching}  # … · 1.001
    - {ga: "3/1/0",   use: false, dir: both,   type: REAL,       var: GA_3_1_0_Living_setpoint}  # … · 9.001
  ```

- `GVL_KNX.st` – the CODESYS global variable list for the selected addresses.

Mark the addresses the PLC needs with `use: true` and run `./scada plc-map` again – your choices are kept,
new addresses from the ETS project are added and removed ones are dropped. To exchange **every** address,
set `select: all` (or run `./scada plc-map --all`).

| Field | Meaning |
|---|---|
| `use` | `true` = exchanged with the PLC (ignored with `select: all`). |
| `dir` | `to_plc`: KNX → PLC, the PLC reads it. `to_knx`: PLC → KNX, the PLC writes it. `both`: both directions. |
| `type` | IEC type of the PLC variable, from the DPT (see below). |
| `var` | Name of the PLC variable. You may rename it; it must be a valid IEC identifier. |

**Names from the system tags.** If the PLC already has its own KNX objects in ETS, named with the system tag
(as the ST_KNX code generator does: `KNX_Linje_M1.M1_000_564_002_RT6101._01_Rom_SP`), set `plc.tag_prefix`
(the building code, e.g. `BY1234`). Addresses linked to such an object are then named after the tag –
`BY1234_564_002_RT6101_Rom_SP`, `BY1234_564_001_XG101_Un` – and get `dir: to_plc`: the PLC's own object already writes
them on the bus, so the bridge only reads them. Other addresses keep the `GA_<address>_<name>` form. After
setting the prefix on an existing mapping, run `./scada plc-map --rename` to rename the existing lines too.
Set `plc.gvl` to name the GVL (and the `.st` file), e.g. `GVL_KNX_Building`, before the first `plc-map`; if
`plc_map.yaml` already exists, change `gvl:` at its top instead.

Defaults: addresses that *control* something in the visualisation (switching, setpoints, operating mode) get
`both`; everything else (feedback, sensors) gets `to_plc`. Letting the PLC write to the bus is a deliberate
choice.

Addresses whose DPT cannot be exchanged (relative dimming, date/time, colours …) are left out; `plc-map`
says how many.

### Types

| DPT | PLC type | Notes |
|---|---|---|
| 1.x (switch, alarm …) | `BOOL` | |
| 5.001 (percent), 5.003 (angle) | `REAL` | 0–100 % / 0–360° |
| 5.x (counter) | `USINT` | 0–255 |
| 6.x / 7.x / 8.x | `SINT` / `UINT` / `INT` | |
| 9.x (temperature, lux, ppm …), 14.x (power, energy …) | `REAL` | |
| 12.x / 13.x (counters, energy) | `UDINT` / `DINT` | |
| 16.x (text) | `STRING(14)` | |
| 17.001 (scene number) | `USINT` | |
| 20.102 (HVAC mode) | `USINT` | 0 auto, 1 comfort, 2 standby, 3 economy, 4 frost/building protection |

## 2. Prepare the PLC (CODESYS)

The exact menu names depend on the CODESYS version.

1. Add a GVL to the application, named as `gvl` in `plc_map.yaml` (default `GVL_KNX`), and paste the contents
   of the generated `.st` file into it.
2. Add a **Symbol Configuration** to the application (with OPC UA support), press **Build** in its toolbar
   (the list only shows what existed at its last build), and tick the GVL with read/write access.
   The generated GVL starts with three attributes:
   - `{attribute 'linkalways'}` – without it, CODESYS leaves a GVL that no running code uses out of the build,
     and it does not appear in the list;
   - `{attribute 'qualified_only'}` – the PLC code uses the variables as `GVL_KNX.<var>`;
   - `{attribute 'symbol' := 'readwrite'}` – every variable is published with read/write access.
3. Download the application to the PLC and start it.
4. Make sure the OPC UA server is enabled. On a WAGO PFC: web-based management, *Fieldbus → OPC UA*.

Check from the server machine what the PLC offers, and that the variables can be seen:

```bash
./scada plc-browse --endpoints     # security modes and login types (no login needed)
./scada plc-browse GVL_KNX         # the variables of the GVL (logs in with the plc: settings)
```

## 3. Connect

Add to `config.yaml` (all keys: [Configuration](04-configuration.md#plc)):

```yaml
plc:
  enabled: true
  endpoint: opc.tcp://192.168.1.100:4840     # opc.tcp://127.0.0.1:4840 when the server runs on the PLC
```

Restart the server. The event log shows `PLC (OPC UA) connected: N variables`, and the top bar shows
`● PLC` (green when connected).

## Security

| Setting | `config.yaml` |
|---|---|
| Anonymous | no `username`, `security: None` (default) |
| User name and password | `username:` and `password:` – a user of the PLC runtime |
| Signed / encrypted | `security: Aes128Sha256RsaOaep,SignAndEncrypt` (or `Basic256Sha256`, `Aes256Sha256RsaPss`; mode `Sign` or `SignAndEncrypt`) |

User name and password can be combined with encryption, and should be: with `security: None` the password is
sent in plain text (the PLC must allow that – on a WAGO PFC: *Allow Password On Plaintext*).

**Pin the PLC's certificate.** Without `server_certificate` the bridge accepts the certificate the PLC offers,
so a device on the network could pretend to be the PLC – receive the password and feed values that the bridge
sends to KNX. Export the OPC UA server certificate from CODESYS (device security screen) and set
`server_certificate: var/opcua/plc_cert.der`.

With encryption the server creates its own client certificate the first time (`var/opcua/client_cert.der`, the
private key with mode 600).
The PLC must **trust** it: in CODESYS, open the security screen of the device, find the rejected (quarantined)
client certificate "EKBPRO KNX Terminal Python@<host>" and move it to the trusted certificates. Then restart
the server. On the WAGO PFC, the policies offered are set under *OPC UA Server Security Settings*
("Aes128Sha256RsaOaep and better" covers all three policies above).

## How values are exchanged

- **No telegrams at start-up.** When the bridge connects, the PLC variables (`to_plc`, `both`) get the last
  known KNX values from the server – that writes to the PLC, not to the bus. The current values of the PLC
  variables are only *noted*; nothing is sent to KNX until a variable **changes** after that. The same applies
  after a reconnect: changes the PLC made while the connection was down are not sent.
- **No telegrams when the PLC restarts.** A download, reset or online change with re-initialisation sets the
  GVL variables to their initial values (`FALSE`, `0`) while the OPC UA connection may stay up. Without
  protection that looks like the PLC program switching everything off. Three guards prevent it:
  1. A report without a value or with a status other than *Good* (CODESYS sends these while it loads an
     application) makes the variable unknown: its next value is only remembered, like the first one after
     connecting. A missing value is never sent as `FALSE` or `0`.
  2. Changes towards KNX are held for `settle_ms` (500 ms). When more than `burst_limit` (8) variables change
     in that time, it is treated as a restart and nothing is sent.
  3. After a restart, changes are only remembered for `restart_quiet_s` (15 s). Then the PLC gets the KNX
     values again, as at start-up. The event log shows "PLC restart detected – nothing sent to KNX".

  If the PLC program really must switch many addresses at once, use a central group address ("all off")
  or raise `burst_limit`.
- **KNX → PLC:** every write or response on the bus updates the PLC variable.
- **PLC → KNX:** a change of a `to_knx`/`both` variable is sent as a write telegram. Writing the *same* value
  again sends nothing. A `both` variable follows the telegrams on *its own* group address, so a wall switch on
  the same address keeps it up to date. If the light can also be switched through *another* address (a
  central "all off", a scene), the variable may still say `TRUE` while the light is off: read the feedback
  address (`to_plc`) in the PLC, and to switch on again write `FALSE`, wait a cycle, then `TRUE`.
- **No echoes:** a value that came from KNX is not sent back to KNX, and a value the PLC sent is not written
  back to the PLC.
- **Rate limit:** at most one KNX write per address every `min_interval_ms` (default 200 ms); if the PLC
  changes a value faster, the latest value is sent. Do not let a `to_knx` REAL follow a noisy measurement
  every cycle – round or filter it in the PLC.
- **`to_plc` variables are owned by KNX:** if the PLC program writes them, the next telegram overwrites them,
  and nothing is sent to the bus.
- **Missing variables** (the GVL in the PLC is older than `plc_map.yaml`) are listed in the event log; the other
  variables keep working. The bridge checks every minute whether they have appeared (after an online change)
  and then reconnects by itself, with the same start-up rules – no server restart needed.

## Troubleshooting

| Problem | Solution |
|---|---|
| `GVL_KNX` is not listed in the Symbol Configuration | The GVL is not used by any code that runs, so CODESYS left it out. Use the generated GVL, which has `{attribute 'linkalways'}`, then press **Build** in the Symbol Configuration. |
| `GVL GVL_KNX not found on the PLC` | The GVL is not in the Symbol Configuration, or the application is not running. Check with `./scada plc-browse GVL_KNX`. If the PLC uses another naming, set `node_prefix` (and `namespace`) from the output of `plc-browse`. |
| `variables are missing in the PLC` | Import the new `GVL_KNX.st` after running `plc-map`, and download the application. |
| Connection refused / timeout | Check `endpoint`, that the OPC UA server is enabled, and the firewall. From the Docker container on the PFC use `127.0.0.1`. |
| `BadIdentityTokenInvalid` | The PLC does not offer anonymous access (on CODESYS: the runtime's port/user authentication is on). Set `username` and `password`. |
| `BadIdentityTokenRejected` / `BadUserAccessDenied` | Wrong user name/password, or the user has no access to the variables. |
| Encryption is set, but the PLC only offers `None` | The PLC's OPC UA server has no certificate yet. In CODESYS, create one for the OPC UA server in the device's security screen and restart the runtime. Check with `plc-browse`: with encryption working, the connection succeeds with `security:` set. |
| `BadSecurityChecksFailed` / certificate errors | Trust the client certificate in the PLC (see Security), then restart the server. |
| The PLC writes, but KNX does not change | Is `dir` `to_knx` or `both`? Is KNX connected (top bar)? The event log shows `PLC → KNX … failed` with the reason. |
