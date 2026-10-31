# Protocols and gateways (Modbus, MQTT, OPC UA)

Besides KNX the server can talk Modbus, MQTT and OPC UA, and move values between them. All protocols are equal:
KNX is one module among the others, and the server also runs **without KNX**, e.g. as a plain Modbus → OPC UA
gateway on a WAGO Edge computer.

```
WAGO energy meter ─RS485─┐                           ┌─ OPC UA server ── Ignition / WinCC
Heat pump ─Modbus TCP────┼── server: connections ────┼─ MQTT broker ──── Home Assistant / Node-RED
KNX bus ─KNX IP──────────┘   gateways (rename, scale)└─ Modbus slave ─── another PLC
```

- A **connection** is one device or system: a Modbus master or slave, an MQTT broker, an OPC UA client or server.
  Its **tags** are the values it has (a register, a topic, a node, a group address).
- A **gateway** copies tags from one connection to another, optionally under a new name, scaled, in one or
  both directions.

Everything is in `var/protocols.yaml` next to `config.yaml` (for a site: `sites/<site>/var/protocols.yaml`; set with
`server.protocols: var/protocols.yaml` – it is in `var/` because the systemd service may only write there), and can be
edited in the file or in the TUI (**F11 Gateways**). The server reads the file again when it is saved from the TUI;
after editing the file by hand press **r** in F11 (or restart the server).

## 1. Install the modules you need

Each protocol is a module with its own Python packages. They are only installed when you ask for them:

```bash
./scada modules                       # which modules exist, are installed and are used
./scada modules install modbus mqtt   # modbus: pymodbus + pyserial · mqtt: aiomqtt · opcua: asyncua
```

Restart the server afterwards. A connection whose module is missing is shown in F11 and in `./scada gateway`
with the command that installs it; the other connections run anyway.

| Module   | Client (we fetch from a device)        | Server (others fetch from us)               |
|----------|----------------------------------------|---------------------------------------------|
| `knx`    | always on when KNX is set up in `config.yaml` | –                                   |
| `modbus` | Modbus TCP / RTU (RS485) master        | Modbus TCP / RTU slave                      |
| `mqtt`   | subscribe and publish to a broker      | –                                           |
| `opcua`  | client of a PLC or another server      | our own OPC UA server for SCADA systems     |

BACnet/IP, MQTT Sparkplug B and Matter are planned.

## 2. Connections

```yaml
connections:
  meter1:                       # name: letters, digits, - and _
    protocol: modbus
    device: /dev/ttyUSB0
    model: WAGO 879-3000        # known device: tags and serial settings come from the template

  plc1:
    protocol: modbus
    transport: tcp              # tcp · rtu (RS485) · rtu_over_tcp (through a TCP converter)
    host: 192.168.1.20
    tags:
      Temp_ute: {address: 'ir:0', type: int16, scale: 0.1, unit: °C}
```

Settings every tag can have: `type`, `scale` and `offset` (value = raw × scale + offset), `unit`, `writable`,
`description`.

### Modbus

| Setting | Default | |
|---|---|---|
| `role` | `client` | `client` = master, `server` = slave |
| `transport` | `tcp` | `tcp`, `rtu`, `rtu_over_tcp` |
| `host`, `port` | master `127.0.0.1`, slave `0.0.0.0`; `502` | Master: the device. Slave: the address to listen on (`0.0.0.0` = all) |
| `device`, `baudrate`, `parity`, `stopbits` | `/dev/ttyUSB0`, `9600`, `N`, `1` | RTU only |
| `unit_id` | `1` | Master: default address of the device. Slave: our address, `0` answers every address |
| `poll_ms`, `timeout_s` | `1000`, `3` | Master only (`poll_ms` at least 50) |
| `max_registers` | `100` | Master: at most this many registers in one read (some devices take 50 or less) |
| `max_gap` | `8` | Master: unused registers read across to join two reads; `0` = only neighbouring registers (for devices that answer unknown addresses with an error) |
| `word_order` | `big` | `big`: high word first (most devices) · `little`: low word first |

Tag addresses: `hr:100` holding register, `ir:5` input register, `co:3` coil, `di:7` discrete input,
`hr:100.3` bit 3 of a register. Hex is fine (`hr:0x5002`), and the classic numbering works too (`40101` = `hr:100`).
Data types: `bool`, `int16`, `uint16` (default), `int32`, `uint32`, `float32`, `int64`, `uint64`, `float64`.
A tag can override the connection's `unit_id` (several devices on one RS485 line).

Some devices are read in one table and written in another. `write_address: hr` writes the tag with function
6/16 to the same number in the holding registers – so `ir:10201` with `write_address: hr` can be written – and
`write_address: hr:200` writes somewhere else. `heartbeat_s: 10` makes the master write a counter (1, 2, 3 …)
to the tag every 10 s while it is connected – for a device watchdog that falls back to safe values when the
controller stops writing. A heartbeat to a tag that cannot be written is reported once in the event log.

As a **slave**, the gateway targets are register addresses (`to: hr:100`).

#### Known devices (`model:`)

For equipment the program knows, write `model:` instead of listing the registers. The template gives the tags
and the serial settings; settings you write yourself win, and your own `tags:` are added (a tag with the same
name as a template tag changes it, e.g. a `description`). In F11, choose the device under *Device*.

| `model` | Equipment | Template |
|---|---|---|
| `WAGO 879-3000` | MID energy meter 4PU, direct, 65 A | `knxscada/protocols/devices/wago-879-30xx.yaml` |
| `WAGO 879-3020` | MID energy meter 4PS, direct, 40 A | same |
| `WAGO 879-3040` | MID energy meter 2PU CT, current transformer | same |
| `Carlo Gavazzi EM111` | 1-phase energy meter, direct or 5 A CT | `carlo-gavazzi-em100.yaml` |
| `Carlo Gavazzi EM112` | 1-phase energy meter, direct, 100 A | same |
| `Carlo Gavazzi ET112` | 1-phase energy meter with hour counter, direct, 100 A | same |
| `Carlo Gavazzi EM270` | dual 3-phase energy meter, TCD current sensors up to 630 A | `carlo-gavazzi-em270.yaml` |
| `Carlo Gavazzi EM280` | dual 3-phase energy meter, TCD06 current sensors, 32 A | same |
| `Carlo Gavazzi EM271` | multi-channel power analyzer, TCD_M current sensor blocks up to 400 A | `carlo-gavazzi-em271.yaml` |
| `Carlo Gavazzi EM580` | 3-phase MID energy meter for EV charging, direct, 65 A | `carlo-gavazzi-em580.yaml` |
| `Carlo Gavazzi WM15` | 3-phase power analyzer, 5 A CT | `carlo-gavazzi-wm15.yaml` |
| `Solar-Log Modbus TCP` | Solar-Log data logger, free Modbus TCP interface (live data of the plant) | `solar-log.yaml` |
| `Solar-Log Modbus TCP PM` | the same with the PM licence: also feed-in point and inverter values | same |

The product number alone works too (`model: 879-3000`, `model: EM111`).

**WAGO 879-30xx:** RS485, Modbus RTU, 9600 baud, **even parity** (8E1), unit id 1, read every 5 s. The 35 tags
are the main values with the names from the manual (appendix A3.2): `Voltage`, `L1 voltage` … `L2-L3 voltage`,
`Frequency`, `Current`, `L1 current` …, `Total active power`, `L1 active power` …, reactive and apparent power,
`Power factor` …, and the energy counters `Total active energy`, `L1`–`L3 Total active energy`,
`Active energy import`, `Active energy export` and `Total reactive energy`. All are `float32` (Float ABCD) and
read-only. Note: the WAGO manual says the factory parity is *none*, the WAGO CODESYS library says *even* –
the template uses even, as our meters do. Set `parity: N` on the connection if a meter answers only then.

After connecting, the master reads the meter's identification once – meter code (`0x4002`: which of the
three it is), unit id, baud rate, parity (`0x4011`) and serial number – and reports it in the event log and in
F11, e.g. `meter1: found WAGO 879-3000 (4PU – direct connection, 65 A)  unit 1  9600 E  serial 0012AB34`.
When the meter is another model, or its settings differ from the connection's, an error says so.

**Carlo Gavazzi:** RS485, Modbus RTU, 9600 baud, **no parity** (8N1, the factory setting), unit id 1, read every
5 s. The values are integers with a value weight (V × 10, A × 1000, W × 10, kWh × 10) and the low word first –
the template scales them, so the tags have the real unit (V, A, W, kWh). The tag names are those of the
Carlo Gavazzi manuals: `V L1-N`, `A L1`, `W sys`, `PF sys`, `Hz`, `kWh (+) TOT`, `kWh (-) TOT` …; on the
EM270/EM280/EM271 each of the two sensor sets has its own tags (`W TOT TCDA1`, `kWh (+) TOT TCDA2` …). The meters
answer at most 50 (EM100 series) or 18 (EM270/EM280/EM271) registers per request; the template sets
`max_registers`. "Out of range" (EEE) and "not measured" are shown as bad quality, not as a number. The
identification code (`0x000B`) tells the model, `0x2000`–`0x2002` the address, baud rate and parity. The EM271
has its own protocol document (codes 274–277): no PF, but power demand (`W TOT TCDA1 DMD`, `… MAXDMD`) and the
detected primary current of each sensor block (`TCDA1 CT primary`, bad quality when no TCD is connected).

**Solar-Log:** Modbus TCP, port 502, unit id 1, read every 5 s; turn the interface on in the Solar-Log
(*Configuration → Digital interfaces → Modbus TCP*). The Solar-Log reads only with function code 4, so all
tags are input registers (`ir:`); 32-bit values have the low word first. `Solar-Log Modbus TCP` gives the
live data of the whole plant (3500–3531): `Pac`, `Pdc`, `Uac`, `Udc`, `Daily yield` … `Total yield` (Wh),
`Pac consumption`, `Daily yield cons.` … `Total yield cons.` and `TotalPower` (installed Wp).
`Solar-Log Modbus TCP PM` (licence 255511) adds the values from the feed-in point and the inverters
(10500–10607, `PM Pac`, `PM Iac L1`, `PM CosPhi`, `PM Status` …; some need firmware 6.0.1). Sensors that
are not connected (irradiation 65535, temperature −273) are shown as bad quality. The power limiting

The PM model also has the power control registers (10200–10248), named `PC …` as in the manual: `PC PLimit_Type`
and `PC PLimitPerc` (active power limit), `PC Reactive_Type`, `PC CosPhi_Fix`, `PC QPerc` (reactive power),
`PC WatchDog_Tag`, `PC WatchDog_Time`, `PC WatchDog_Limit`, the external utility meter values
(`PC Utility_connect_good`, `PC Utility_fUacRS` … `PC Utility_fQacT`) and the Q(U) curve parameters of firmware 6
(`PC Uq0_Uc`, `PC Qref_Pbinst`, `PC Qref_total`, `PC Qref_type`). They are read with function 4 and written
with 6/16 (`write_address: hr`). With a watchdog time (`PC WatchDog_Time` > 0 s), `PC WatchDog_Tag` must be
written within that time, or the Solar-Log falls back ("Failure of the remote control"). Let the master do it:

```yaml
  solarlog:
    protocol: modbus
    host: 192.168.1.50
    model: Solar-Log Modbus TCP PM
    tags:
      PC WatchDog_Tag: {heartbeat_s: 10}   # shorter than PC WatchDog_Time
```

A tag can have its own `invalid: [65535]` – raw values that mean "no value" for that tag alone – in addition to
the template's `invalid` list.

Give the tags names from your tagging system in the gateway, when they go on to OPC UA or BACnet:

```yaml
  - name: Energimåler til SCADA
    from: meter1
    to: scada
    map:
      - Total active power -> 320.001-OE001.P
      - Total active energy -> 320.001-OE001.E
```

#### Test without the equipment (`./scada simulate`)

```bash
./scada simulate                                  # lists the device templates
./scada simulate "Carlo Gavazzi EM111"            # Modbus TCP slave on 127.0.0.1:5020, unit id 1
./scada simulate EM270 --port 5021 --unit 3
./scada simulate "WAGO 879-3000" --device /dev/ttyUSB1   # on a serial port, with the template's serial settings
```

The simulator answers on the template's registers with plausible values that change a little (about 230 V,
50 Hz, energy counters counting up) and on its identification registers, so a connection with the same `model:`
identifies it and reads real-looking values. What a master writes to the tags with `write_address` (the
Solar-Log power limits) is kept and read back. It prints the lines to put in `protocols.yaml` (`transport: tcp`,
`host`, `port`, `model`). Ctrl+C stops it.

A new template is a YAML file in `knxscada/protocols/devices/` – see the WAGO file and the docstring of
`knxscada/protocols/devices/__init__.py`.

### MQTT

| Setting | Default | |
|---|---|---|
| `host`, `port` | `127.0.0.1`, `1883` | The broker |
| `username`, `password`, `tls`, `client_id` | | `tls: true` usually means port 8883 |
| `topic_prefix` | | Put in front of every topic, e.g. `bygg/` |
| `subscribe` | | Topic filters (`sensors/#`): every topic that arrives becomes a tag by itself |
| `payload` | `plain` | `plain`: `21.5` · `json`: `{"value": 21.5, "ts": …}` |
| `qos`, `retain` | `0`, `true` | |

Tags: `topic` (default prefix + tag name), `json_key` (pick the value out of a larger JSON object) and `set_topic`
(where writes go; empty = the tag's own topic, and `<topic>/set` for tags found through `subscribe`). A gateway target is published to prefix + name; with `direction: both` the gateway also listens
to `<topic>/set`.

### OPC UA

| Setting | Default | |
|---|---|---|
| `role` | `client` | `client` of a PLC/server, or `server` for SCADA systems |
| `endpoint` | `opc.tcp://127.0.0.1:4840` | Server: write `opc.tcp://0.0.0.0:4840` to listen on every address – left out, the server is only reachable from this machine |
| `username`, `password` | | Empty = anonymous |
| `security` | `None` | As for the [PLC bridge](10-plc-opcua.md) |
| `namespace` | `urn:ekbpro:gateway` | Server only |
| `publish_interval_ms` | `500` | Client only |
| `timeout_s` | `10` | Client only |
| `certificate`, `private_key` | client `var/opcua/client_cert.der`, `…/client_key.pem`; server `var/opcua/server_cert.der`, `…/server_key.pem` | Created automatically; used with `security` |
| `server_certificate` | – | Client: pin the server's certificate (recommended with `security`) |

Client tags name a node id (`node: "ns=4;s=|var|PLC.Application.GVL.rTemp"`); `./scada plc-browse` lists them.
As a server, every gateway target becomes a variable, and dots or slashes in the name make folders:
`Building.Room101.Temperature` → `Objects/Gateway/Building/Room101/Temperature`. The node id is the name, so it stays
the same when the server restarts. Targets with `direction: both` are writable.

### KNX

The connection `knx` exists by itself when KNX is set up in `config.yaml` (`knx:` and `project:`). Its tags are the
group addresses (`1/2/3`, or `S:1/2/3` with several projects) or their ETS names when they are unique. Values are
encoded with the address's DPT; writes are logged in the event log with the gateway as the user.

## 3. Gateways

```yaml
gateways:
  - name: Energimåler til SCADA
    from: meter1
    to: scada
    map:
      - Total active power -> Energi.Hovedtavle.Effekt      # a new name for each tag
      - Total active energy -> Energi.Hovedtavle.Energi

  - name: KNX til MQTT
    from: knx
    to: broker
    deadband: 0.2
    min_interval_ms: 1000
    map:
      - 3/0/1 -> stue/temperatur
      - {from: 1/1/1, to: stue/lys, direction: both}
      - {from: hr:100, to: Ute.Temp, scale: 0.1}
```

A mapping line is one of:

- `source` – the same name on both sides;
- `source -> new name` – `*` and `?` match several tags, `{name}` is the source tag's name;
- `{from: …, to: …, …}` with `direction` (`forward` or `both`), `scale`, `offset`, `invert`, `deadband`,
  `min_interval_ms`, `initial`, and for a new target tag `type`, `unit` and `description`.

Settings of the whole gateway: `direction`, `deadband`, `min_interval_ms`, `initial` and `enabled`.

Rules that keep the plant safe and quiet:

- **No echoes.** A value that was just written and is read back does not travel back to the source.
- **Read first, send afterwards.** After a start-up or a reconnect every connection first reads the status of its
  tags; the F11 view shows `reading …` until it is done. These start-up values are remembered on both sides of
  every line, but **not sent to devices**: a device keeps its own value until something really changes, and only
  changes that happen *after* both sides have been read are passed on.
  - Modbus master: ready after the first poll of all registers.
  - OPC UA client: ready when every node has been read once.
  - MQTT: ready 1 s after subscribing (the retained messages are the statuses).
  - KNX: ready when the feedback, sensor and alarm addresses have been read (`server.read_on_start: status`, the
    default, +2 s for the last answers); with `read_on_start: false` at once.
  - **Old values are never passed on.** The KNX values in the database from the last run are only shown in the
    TUI (dimmed) – OPC UA, MQTT and Modbus get a KNX value first when the bus has sent it since the start. Until
    then the tag is empty.
  - Our own servers (OPC UA server, Modbus slave) are ready as soon as they run.
- **Start-up values fill our own address space only.** The OPC UA server, the Modbus slave and the MQTT state
  topics a gateway creates get every value at once (and again when they come back, e.g. after a broker restart),
  so clients reading them do not see empty values. `initial` on a gateway or a line changes this:
  - not set (default): only to our own servers;
  - `initial: true`: also to devices (Modbus master, OPC UA client, MQTT topics of devices) – **never to KNX**,
    a start-up or reconnect never puts telegrams on the bus;
  - `initial: false`: to nothing.
- **The target is read before it is written.** A change for a target that is still reading waits until it is
  done, and is not sent when the target already has that value. If the target is not connected, the change is
  reported as an error and dropped.
- **Deadband**: numbers that changed less than this are not sent.
- **Rate limit** (`min_interval_ms`): at most one write per line and direction in that time; the latest value is
  sent when the time is up.
- KNX HVAC modes (DPT 20.102) become numbers (0 auto, 1 comfort, 2 standby, 3 economy, 4 protection) and back.

The target tag is created by itself when the target is a server (OPC UA server, Modbus slave, MQTT). A client target
(Modbus master, OPC UA client) must have the tag under `tags:`.

## 4. Check and watch

```bash
./scada gateway
```

checks `protocols.yaml` and lists connections and gateways – with live status, tag counts and errors when the server
runs. In the TUI, **F11 Gateways** shows the connections (top), the tags of the selected connection with live values
(left) and the gateway lines with their last value and errors (right).

| Key | |
|---|---|
| `a` | New connection (choose the protocol, then a form with only the settings that apply) |
| `e` or Enter | Edit the selected connection |
| `g` / `G` or Enter | New gateway / edit the selected gateway |
| `w` or Enter | Write a value to the selected tag |
| `r` | Re-read `protocols.yaml` |

## 5. Without KNX

Leave `knx:` and `project:` out of `config.yaml` (or set `knx: {enabled: false}`). The server then only runs the
connections and gateways; the KNX views are empty and the TUI starts on F11.

```yaml
# config.yaml – a plain Modbus → OPC UA gateway
server:
  socket: var/knxscada.sock
  database: var/knxscada.db
```
