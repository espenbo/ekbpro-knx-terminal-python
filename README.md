# EKBPRO KNX Terminal Python – SCADA in the terminal

![Overview](pic/01-oversikt.png)

A SCADA and visualisation system that runs in the terminal and is used over SSH or a VPN. It started with KNX –
everything is built automatically from the ETS project file (`.knxproj`), so **anyone can use it with their own
installation** – and now also speaks **BACnet/IP, Modbus TCP/RTU, OPC UA and MQTT**, with gateways between them.
KNX is one module among the others: an installation can run with only BACnet, only Modbus, or any mix.
You need no web browser, no per-view licence and no special hardware.

**Features**

- **Visualisation per floor and room:** switches, dimmers, setpoints, operating modes and measurements with live status.
- **Overview:** status tiles (outdoor, indoor, power, lights, KNX traffic, alarms), climate table with sparklines, room status and charts.
- **Floor plan** of the building with live status per room. Can be exported to DXF for CAD programs.
- **Group addresses:** every address with its live value, search, reading and writing.
- **Bus monitor:** all telegrams live, with filter and pause (and the interface each came through). Export to
  ETS (group monitor XML), CSV or JSON – what is shown, or the last hour/day/week from the log.
- **Alarms:** rules from the configuration, acknowledgement and history.
- **Event log:** who operated what, and when.
- **Trends:** curves from the log (1 hour to 30 days) as line, step, filled or bar charts.
- **Systems view (Ctrl+E):** one plant at a time with live values, 24-hour charts and a Settings tab with every
  tag in sections. Air handling units get a fixed air-flow drawing (fans, heat recovery, heating, cooling, flows,
  pressures, operating mode and alarm class); water-borne plants – boiler rooms, floor heating, tap water, energy
  valves – are drawn as **piping diagrams written as text in the configuration**, with supply pipes in red,
  return pipes in blue and pumps coloured by running/alarm/safety switch.
- **Energy meters (Ctrl+G):** power, energy, currents and voltages per meter, and consumption per hour or day
  worked out from the counter.
- **Monitor per protocol (F4):** the KNX bus monitor, and for Modbus, OPC UA, BACnet and MQTT every value change and
  every write live – with the user who wrote it.
- **Screensaver** with many pages – clock, sky with sun, moon and lit windows, heat map, ventilation with moving
  air, energy meters, today's records, telegram rain. Unacknowledged alarms take over the screen.
- **Six colour themes:** muted green, green phosphor, amber, modern dark, solarized and Storm Automasjon –
  chosen per installation, so several sites can be told apart. The Storm Automasjon theme comes with its logo; a logo of your own is possible too.
- **Two languages:** English (default) and Norwegian bokmål – per installation, per user or per session.
- **One server, many clients:** one KNX tunnel per IP interface regardless of the number of SSH sessions, and logging around the clock.
- **Connectivity:** KNX IP tunneling (UDP and TCP), routing and KNX IP Secure with a keyring from ETS.
- **Several KNX IP interfaces** treated as one bus (one per line or building part), with routing of outgoing
  telegrams and de-duplication; reachable through NAT/VPN.
- **Several ETS projects in one site** (e.g. a main building and an annex), `.knxproj` and `.knxprojarchive`.
- **Rooms from group address names** for projects structured by function ("Actual temp Office 1").
- **PLC bridge over OPC UA:** a PLC (e.g. CODESYS on a WAGO PFC) reads and writes selected KNX addresses – the GVL is
  generated from the ETS project, optionally with names from system tags. Several PLCs per site. A PLC download or
  reset never reaches the bus.
- **Protocol modules:** KNX, Modbus TCP and RTU (master and slave, device templates for WAGO 879-30xx energy
  meters, Carlo Gavazzi and Solar-Log), OPC UA (client and server, block-wise reads for WAGO PFC), MQTT, and
  BACnet/IP (client with ReadPropertyMultiple and COV, and a BACnet device of our own). Each module's packages are
  installed only when it is used: `./scada modules install bacnet modbus`.
- **Gateways** between any two connections: renaming, scaling, direction, echo suppression, deadband and rate
  limits – e.g. KNX → BACnet for a BMS, or Modbus energy meters → OPC UA.
- **Logging, trends and alarms for protocol tags** as for group addresses (`log`, `match_point`, `delay_s`).
- **Safe by design:** nothing is sent to equipment at start-up or reconnect (statuses are read first, old values
  are only shown); every tag towards equipment is read-only until writing is enabled; every write is confirmed and
  logged with the user; BACnet writes use a chosen priority and can be released again, and the life-safety
  priorities are refused.
- **Docker:** runs in a container, also on a WAGO PFC200/PFC300.

## Getting started

```bash
git clone https://github.com/espenbo/ekbpro-knx-terminal-python.git
cd ekbpro-knx-terminal-python
./setup.sh                                                   # installs everything
./scada init ~/MyHouse.knxproj --gateway 192.168.1.50        # your ETS project and IP interface
./scada server &                                             # starts the KNX connection
./scada                                                      # starts the visualisation
```

Without access to the installation you can try it with a simulated bus: `./scada server --demo`.

Without KNX: leave out the ETS project, install the protocol modules you need and describe the connections in
`protocols.yaml` – see [Protocols and gateways](docs/en/11-protocols-gateways.md). `./scada bacnet-scan ADDRESS --yaml`
writes the tags of a BACnet device for you.

From another machine: `ssh -t user@server ~/ekbpro-knx-terminal-python/scada`

## Screenshots

Taken from a real installation, with the user interface in Norwegian (English is the default).

| | |
|---|---|
| ![Start-up](pic/00-splash.png) Start-up | ![Room](pic/03-rom-trapperom.png) F2 Room |
| ![Floor](pic/04-etasje-1-alle-rom.png) F2 All rooms of a floor | ![Group addresses](pic/05-gruppeadresser.png) F3 Group addresses |
| ![Bus monitor](pic/06-busmonitor.png) F4 Bus monitor | ![Alarms](pic/07-alarmer.png) F5 Alarms |
| ![Event log](pic/08-hendelseslogg.png) F6 Event log | ![Trends](pic/09-trender.png) F7 Trends |
| ![Floor plan](pic/10-plantegning-1.png) F8 Floor plan | ![Floor plan, 2nd floor](pic/10-plantegning-2.png) F8 Floor plan, next floor |
| ![Colour theme](pic/11-fargetema.png) Ctrl+T Colour theme | ![Language](pic/12-sprak.png) Ctrl+L Language |
| ![About](pic/13-om.png) F12 About | ![Screensaver](pic/14-skjermsparer.png) F9 Screensaver |

From a building connected over BACnet/IP and KNX: a WAGO PLC, an air handling unit and Belimo energy valves.

| | |
|---|---|
| ![Overview](pic/20-bacnet-oversikt.png) F1 Overview | ![Boiler room](pic/21-anlegg-varmeanlegg.png) Ctrl+E Piping diagram: boiler and main pumps |
| ![Heating circuits](pic/22-anlegg-varmekurser.png) Ctrl+E Floor heating and ventilation circuit | ![Energy valve](pic/23-anlegg-energiventil.png) Ctrl+E Belimo energy valve |
| ![Air handling unit](pic/24-anlegg-ventilasjon.png) Ctrl+E Air handling unit over BACnet | ![Tap water](pic/25-anlegg-tappevann.png) Ctrl+E Tap water and showers |
| ![BACnet monitor](pic/26-monitor-bacnet.png) F4 Monitor › BACnet/IP | ![Energy meters](pic/27-energimalere.png) Ctrl+G Energy meters |
| ![Screensaver](pic/28-skjermsparer-himmel.png) F9 Screensaver: the sky over the building | |

## Documentation

Every document is in English (`docs/en/`) and Norwegian bokmål (`docs/nb/`).

| English | Norsk | Contents |
|---|---|---|
| [Installation](docs/en/01-installation.md) | [Installasjon](docs/nb/01-installation.md) | Requirements, installation, first test |
| [Using your own KNX project](docs/en/02-own-project.md) | [Eget KNX-prosjekt](docs/nb/02-own-project.md) | Export from ETS, `init`, what makes a good visualisation, customisation, KNX Secure |
| [User guide](docs/en/03-user-guide.md) | [Brukerveiledning](docs/nb/03-user-guide.md) | All views, keys and functions: Systems, Energy meters, Monitor per protocol, BACnet writes with priority |
| [Configuration](docs/en/04-configuration.md) | [Konfigurasjon](docs/nb/04-configuration.md) | Complete reference for `config.yaml`: alarm rules, language, several KNX interfaces and ETS projects, rooms from names, the Systems view (`ui.systems`: air handling units, piping diagrams, panels, energy meters) |
| [Operation and SSH](docs/en/05-operation-and-ssh.md) | [Drift og SSH](docs/nb/05-operation-and-ssh.md) | systemd, multiple users, dedicated SSH user, security, backup |
| [Floor plan](docs/en/06-floor-plan.md) | [Plantegning](docs/nb/06-floor-plan.md) | `plan.yaml` and DXF export |
| [Troubleshooting](docs/en/07-troubleshooting.md) | [Feilsøking](docs/nb/07-troubleshooting.md) | Common problems and solutions |
| [Docker](docs/en/09-docker.md) | [Docker](docs/nb/09-docker.md) | Running in a container, e.g. on a WAGO PFC200/PFC300 |
| [PLC bridge (OPC UA)](docs/en/10-plc-opcua.md) | [PLS-bro (OPC UA)](docs/nb/10-plc-opcua.md) | Let a PLC read and write KNX: mapping, CODESYS GVL, security |
| [Protocols and gateways](docs/en/11-protocols-gateways.md) | [Protokoller og gatewayer](docs/nb/11-protocols-gateways.md) | Modbus TCP/RTU, MQTT, OPC UA and BACnet/IP as modules, gateways between them and KNX, running without KNX |
| [Setup reference](docs/en/12-setup-reference.md) | [Oppsettsreferanse](docs/nb/12-setup-reference.md) | Everything to set the program up – also for AI assistants: complete examples, every key with type, default and valid values, tasks step by step, checks |
| [Architecture](docs/en/08-architecture.md) | [Arkitektur](docs/nb/08-architecture.md) | For developers: components, protocol, database, translations, tests, extensions |

## Commands

| Command | Does |
|---|---|
| `./scada` | Starts the visualisation |
| `./scada tui [--language en\|nb]` | Starts the visualisation, optionally in another language for this session |
| `./scada server [--demo] [-v]` | Starts the server (KNX, logging, alarms) |
| `./scada init PROJECT.knxproj [--gateway IP] [--connection TYPE] [--password PW] [--language en\|nb] [--force]` | Creates `config.yaml` for a project |
| `./scada import` | Re-reads the `.knxproj` |
| `./scada info` | Shows a summary of the project |
| `./scada plan-dxf [file.dxf]` | Exports the floor plan to DXF |
| `./scada plc-map [--all\|--marked] [--rename] [--plc NAME]` | Creates/updates the PLC mapping (`plc_map.yaml`) and the CODESYS GVL, for every PLC or one |
| `./scada plc-browse [FILTER] [--endpoints] [--plc NAME]` | Lists the variables (or the security modes) of the PLC's OPC UA server |
| `./scada modules [install NAME …]` | Lists the protocol modules (Modbus, MQTT, OPC UA, BACnet), or installs their packages |
| `./scada gateway` | Checks `protocols.yaml` and shows connections and gateways (live when the server runs) |
| `./scada simulate [MODEL] [--port 5020] [--device TTY]` | Plays a device template (WAGO, Carlo Gavazzi …) as a Modbus slave, for tests without equipment |
| `./scada bacnet-scan [ADDRESS] [--device N] [--yaml]` | Finds BACnet devices (Who-Is), or lists the objects of one device – as tags for `protocols.yaml` with `--yaml`. Works across a VPN too (devices whose I-Am is a broadcast are read directly) |

All commands accept `-c path/to/config.yaml` (before the command). You can also set the environment variable
`KNXSCADA_CONFIG`.

## Keys at a glance

`F1` Overview · `F2` Rooms · `F3` Addresses · `F4` Monitor · `F5` Alarms · `F6` Log · `F7` Trends ·
`F8` Plan · `F9` Screensaver · `F10` Quit · `F11` Gateways · `F12` About · `Ctrl+E` Systems ·
`Ctrl+G` Energy meters · `Ctrl+T` Colour theme · `Ctrl+L` Language · `/` Search

More in the [user guide](docs/en/03-user-guide.md).

## Development

Tests live in `tests/` and are run with pytest:

```bash
venv/bin/pip install -r requirements-dev.txt
venv/bin/python -m pytest
```

See [Architecture](docs/en/08-architecture.md) for the code structure and how to add translations.

## License

EKBPRO KNX Terminal Python is free software, licensed under the GNU General Public License, version 2 or (at your
option) any later version (GPL-2.0-or-later). See [LICENSE](LICENSE).
