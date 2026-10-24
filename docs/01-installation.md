# Installation

## Requirements

| | |
|---|---|
| **Operating system** | Linux, for example Debian, Ubuntu or Raspberry Pi OS. macOS also works. Windows is not recommended for the server. |
| **Python** | 3.10 or newer. Developed and tested with 3.13. |
| **KNX** | A KNX IP interface (tunneling) or an IP router (routing) on the same network |
| **ETS project** | `.knxproj` exported from ETS 4, 5 or 6 |
| **Terminal** | A terminal with 256 colours or truecolor and a Unicode font, at least approx. 120 × 35 characters |

The machine does not need to be powerful. A Raspberry Pi 4 is enough for a normal home.

Debian/Ubuntu need the package for virtual environments:

```bash
sudo apt install python3 python3-venv git
```

## Install

```bash
git clone https://github.com/espenbo/ekbpro-knx-terminal-python.git
cd ekbpro-knx-terminal-python
./setup.sh
```

`setup.sh` creates a virtual environment in `venv/` and installs the packages from `requirements.txt`:

- `xknx`: KNX communication
- `xknxproject`: reading `.knxproj` files
- `textual` and `textual-plotext`: terminal user interface and charts
- `PyYAML`: configuration

If a `config.yaml` already exists, `setup.sh` also re-reads the project (`./scada import`).

## Set up your project

```bash
./scada init /path/to/MyHouse.knxproj --gateway 192.168.1.50
```

Add `--language nb` if the user interface should be in Norwegian bokmål. See
[Using your own KNX project](02-own-project.md) for details.

## First test without an installation

Demo mode simulates the bus based on your project. Sensors change, actuators respond with status,
and fault messages appear now and then. Demo mode uses a separate database (`var/knxscada-demo.db`)
and does not mix with real logs.

```bash
./scada server --demo        # in one terminal
./scada                      # in another terminal or SSH session
```

## Connect to the installation

```bash
./scada server
```

At start-up the server connects to KNX. By default it sends **no telegrams** at start-up and shows the last
values from its database; with `read_on_start: status` it also reads the feedback and sensor addresses (about
80 ms per address, see [Configuration](04-configuration.md#server)). Watch the log:

```
[knx] KNX connection: CONNECTED
[knx] Reading the status of 107 group addresses     (only with read_on_start: status)
```

Then start the visualisation with `./scada`.

To run the server permanently as a service that starts automatically, see [Operation and SSH](05-operation-and-ssh.md).

## Updating

```bash
git pull
venv/bin/pip install -r requirements.txt
sudo systemctl restart knxscada      # if the server runs as a service
```

Also restart the visualisation in open sessions.
