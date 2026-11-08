# Changelog

All notable changes. Versions follow [semantic versioning](https://semver.org/): a new minor version (0.x.0) adds
features, a patch version (0.x.y) only fixes. Each version is a git tag `vX.Y.Z` with a release on GitHub, made by
`.github/workflows/release.yml` after every test has passed. How to update and to go back to an earlier version:
[docs/en/01-installation.md](docs/en/01-installation.md#updating-and-going-back).

Write new entries under **Unreleased**; `tools/release.py prepare X.Y.Z` turns that section into the new version.

## Unreleased

## 0.3.0 – 2026-11-08

The first versioned release. It collects three rounds of external review and the safety work that followed.

### Added

- Data quality over BACnet, MQTT and Sparkplug B. The BACnet client reads `status-flags`: *fault* and
  *out-of-service* make a value bad (`status_flags: false` turns it off). Our BACnet objects show `reliability` while
  their source is bad. MQTT targets publish their quality, and Sparkplug carries the `Quality` property both ways.
- Monitor › protocol: "Errors" counts the tags that are bad now, `e` lists them with the reason, and fast tags (PLC
  clocks) are hidden from the start.
- Access levels per Linux account on the server socket (`server.access`: read, operate, configure).
- Supervised background tasks and a watchdog: a task that fails is reported and started again, and a hang is
  reported. The systemd unit is `Type=notify` with `WatchdogSec`.
- `./scada --version`. The server writes its version to the event log when it starts.

### Changed

- Our OPC UA server has safe defaults. Anonymous clients may only read (`anonymous_write` allows writing), nobody
  gets administrator rights, and the endpoint defaults to 127.0.0.1. A server that is open on the network is
  reported at every start.
- Gateway engine:
  - a write that is still waiting is dropped when its source turns bad or is lost (validity tokens);
  - quality travels through chains of gateways;
  - echoes are judged at each device's resolution (float32, DPT), and counters compare exactly.
- `protocols.yaml`, `config.yaml` and their `.bak` copies are written atomically and kept private.
- Reloading a broken `protocols.yaml` keeps the running settings instead of stopping every connection.

### Fixed

- A one-way gateway target on an MQTT connection was refused as read-only.
- Sparkplug: -1 to a UInt64 made the server hang, and a late birth was taken as a change.
- A removed gateway could still write a value that was waiting.
- Large 64-bit integers lost digits on the way through `coerce`.

## 0.2.0 – 2026-11-07

The state before versioned releases (no tag): KNX SCADA with the TUI, the PLC bridge, and modular protocols
(Modbus, MQTT, Sparkplug B, OPC UA, BACnet) with gateways, the power limiter, piping diagrams and the protocol
monitor.
