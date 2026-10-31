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

Everything is in `protocols.yaml` next to `config.yaml` (for a site: `sites/<site>/protocols.yaml`), and can be
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
    transport: rtu              # tcp · rtu (RS485) · rtu_over_tcp (through a TCP converter)
    device: /dev/ttyUSB0
    baudrate: '9600'
    parity: E
    unit_id: 1
    poll_ms: 5000
    tags:
      Spenning_L1: {address: 'hr:0x5002', type: float32, unit: V}
```

Settings every tag can have: `type`, `scale` and `offset` (value = raw × scale + offset), `unit`, `writable`,
`description`.

### Modbus

| Setting | Default | |
|---|---|---|
| `role` | `client` | `client` = master, `server` = slave |
| `transport` | `tcp` | `tcp`, `rtu`, `rtu_over_tcp` |
| `host`, `port` | –, `502` | Slave: the address to listen on (`0.0.0.0` = all) |
| `device`, `baudrate`, `parity`, `stopbits` | `/dev/ttyUSB0`, `9600`, `N`, `1` | RTU only |
| `unit_id` | `1` | Master: default address of the device. Slave: our address, `0` answers every address |
| `poll_ms`, `timeout_s` | `1000`, `3` | Master only |
| `word_order` | `big` | `big`: high word first (most devices) · `little`: low word first |

Tag addresses: `hr:100` holding register, `ir:5` input register, `co:3` coil, `di:7` discrete input,
`hr:100.3` bit 3 of a register. Hex is fine (`hr:0x5002`), and the classic numbering works too (`40101` = `hr:100`).
Data types: `bool`, `int16`, `uint16` (default), `int32`, `uint32`, `float32`, `int64`, `uint64`, `float64`.
A tag can override the connection's `unit_id` (several devices on one RS485 line).

As a **slave**, the gateway targets are register addresses (`to: hr:100`).

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
(where writes go). A gateway target is published to prefix + name; with `direction: both` the gateway also listens
to `<topic>/set`.

### OPC UA

| Setting | Default | |
|---|---|---|
| `role` | `client` | `client` of a PLC/server, or `server` for SCADA systems |
| `endpoint` | `opc.tcp://127.0.0.1:4840` | Server: `opc.tcp://0.0.0.0:4840` listens on every address |
| `username`, `password` | | Empty = anonymous |
| `security` | `None` | As for the [PLC bridge](10-plc-opcua.md) |
| `namespace` | `urn:ekbpro:gateway` | Server only |
| `publish_interval_ms` | `500` | Client only |

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
      - '*  -> Energi.Hovedtavle.{name}'        # every tag, under one folder

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
  - KNX: ready at once, or with `server.read_on_start` (in `config.yaml`) when the bus has been read
    (+2 s for the last answers). Values from the database are statuses too.
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
| `e` / Enter | Edit the selected connection |
| `g` / `G` | New gateway / edit the selected gateway |
| `w` / Enter | Write a value to the selected tag |
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
