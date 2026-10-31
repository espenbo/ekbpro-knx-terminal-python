# Operation and SSH

## Server and clients

```
 KNX IP interface ──UDP/TCP──  scada server  ── Unix socket ──┬── ./scada (local)
                                 │                            ├── ssh -t … ./scada
                                 └── var/knxscada.db          └── ssh scada@…  (dedicated user)
```

- **The server** holds the KNX connection, logs everything, evaluates alarms and shares real-time data.
  It runs all the time.
- **The visualisation** (`./scada`) is a pure client. Start and stop it as often as you like. It affects neither
  logging nor alarms.

## The server as a systemd service

[`deploy/knxscada.service`](../deploy/knxscada.service) contains the placeholders `YOUR_USER` and `INSTALL_DIR`.
Run this from the program directory as the user that owns it – it fills them in and installs the service:

```bash
sed -e "s#YOUR_USER#$USER#g" -e "s#INSTALL_DIR#$PWD#g" deploy/knxscada.service \
    | sudo tee /etc/systemd/system/knxscada.service
sudo systemctl daemon-reload
sudo systemctl enable --now knxscada
journalctl -u knxscada -f          # follow the log
```

Stop and start the server with `sudo systemctl stop|start|restart knxscada`. The server stops immediately, even when
clients are connected. The clients show "server lost" and reconnect by themselves when the server is back.
If you run the server in a terminal, `Ctrl+C` stops it. A second `Ctrl+C` forces it to exit.

The unit is hardened: the file system is read-only for the server except `var/`, it gets no new privileges and
no capabilities, and its memory is limited to 1 GB.

### Several installations as services

Several sites can run on one machine, each with its own server, socket and database. Put each in its own
directory under `sites/` (ignored by git), e.g. `sites/cabin/config.yaml` with the project file next to it, and
use the template [`deploy/knxscada@.service`](../deploy/knxscada@.service) – the part after `@` is the
directory name:

```bash
mkdir -p sites/cabin
./scada -c sites/cabin/config.yaml init ~/Cabin.knxproj --gateway 10.0.0.20

sed -e "s#YOUR_USER#$USER#g" -e "s#INSTALL_DIR#$PWD#g" deploy/knxscada@.service \
    | sudo tee /etc/systemd/system/knxscada@.service
sudo systemctl daemon-reload
sudo systemctl enable --now knxscada@cabin        # server for sites/cabin
journalctl -u knxscada@cabin -f                   # its log
./scada -c sites/cabin/config.yaml                # its visualisation
```

The main installation (`config.yaml` in the program directory, service `knxscada`) and any number of
`knxscada@<site>` services run side by side:

```bash
systemctl status knxscada knxscada@cabin knxscada@office
sudo systemctl restart knxscada@office            # after changing sites/office/config.yaml
```

If a site's KNX interfaces are reached through a VPN (Tailscale, WireGuard), let the service start after it:
`sudo systemctl edit knxscada@office` and add `[Unit]` / `After=tailscaled.service`.

## Use over SSH

```bash
ssh -t user@server ~/ekbpro-knx-terminal-python/scada
```

`-t` gives a real terminal, and it is required.

Each SSH user can choose their own colour theme (`Ctrl+T`) and language (`Ctrl+L`). For one session in another
language: `ssh -t user@server ~/ekbpro-knx-terminal-python/scada tui --language nb`.

**Recommended terminals:**

- Linux: GNOME Terminal, Konsole, Kitty, WezTerm
- macOS: iTerm2
- Windows: Windows Terminal
- iPhone/iPad: Blink Shell, Termius or Prompt 3. Very simple terminal apps (no alternate screen, no xterm
  queries) cannot show full-screen programs: the text `$p$p` or `22;default` appears on the screen.

**Terminal settings:**

- `TERM=xterm-256color`. If the terminal supports truecolor, you get more accurate colours with `COLORTERM=truecolor`.
- A font with box-drawing and braille characters (`─ │ ┌ ⣿`), for example DejaVu Sans Mono, JetBrains Mono or Cascadia Mono.
- At least approx. 120 × 35 characters. The overview makes use of larger windows.
- **PuTTY:** set *Window → Translation* to UTF-8 and *Terminal → Keyboard → Function keys* to "Xterm R6".

### Dedicated SSH user that goes straight into the visualisation

```bash
sudo useradd -m -s /bin/bash -G $USER scada      # the group of the program owner gives access to the socket
sudo passwd scada                                 # or add an SSH key
sed "s#INSTALL_DIR#$PWD#g" deploy/sshd_scada.conf | sudo tee /etc/ssh/sshd_config.d/scada.conf
sudo systemctl reload ssh
```

Run it from the program directory as the program owner (`INSTALL_DIR` in `ForceCommand` is filled in by `sed`).
After that, `ssh scada@server` goes straight into
the visualisation. When it exits, the session is closed. To fix the language for this user, add
`--language nb` to the `ForceCommand` line (or let the user choose with `Ctrl+L`).

The `scada` user must be able to read the program directory and `var/`, and write to the socket. Check that the
owner's home directory has `chmod o+x` or group access.

## Access and security

- **Anyone who can connect to the socket can operate the installation.** The socket has permissions `660` (owner
  and group). Only give trusted users membership of the group. Better: install the program under its own
  account (e.g. in `/opt`), so the group gives access to the socket but not write access to the program files.
- Every operation is logged in the event log (F6) with the Linux account – taken from the kernel, not from
  the client – and the SSH client's IP. Only addresses in the ETS project can be written.
- `config.yaml` can hold passwords (KNX Secure keyring, PLC): `init` creates it with mode `600`; keep it so
  (`chmod 600 config.yaml`).
- The systemd unit in `deploy/` is hardened (read-only file system except `var/`, no new privileges, no
  capabilities). The SSH configuration for the `scada` user disables all forwarding, also of the socket.
- Use SSH keys, not passwords, and consider `AllowUsers` in `sshd_config`.
- The server opens no network ports. It only listens on the local Unix socket.
- Keep the KNX network separate from the internet. Use KNX Secure where possible.

## Backup

Keep these:

| File | Contents |
|---|---|
| `config.yaml`, `plan.yaml` | Your configuration |
| `*.knxproj`, `*.knxkeys` | ETS project and keyring, if any. The keyring file is sensitive. |
| `var/knxscada.db` | History: telegrams, events and alarms |
| `plc_map.yaml`, the generated `GVL_*.st` | PLC mapping, if you use the PLC bridge |
| `var/opcua/` | OPC UA client certificate and key. If lost, a new one is created and the PLC must trust it again. Sensitive. |

The database can be copied while the server is running:

```bash
sqlite3 var/knxscada.db ".backup 'backup-$(date +%F).db'"
```

`var/project_cache.json` is rebuilt automatically and needs no backup. The personal settings in
`~/.config/knxscada/ui.json` can also be left out.

## Resource usage

Measured on an x86 PC (Python 3.13) with demo traffic of approx. 1.3 telegrams/s:

| | Memory (RSS) | CPU |
|---|---|---|
| Server | approx. 60 MB | approx. 0.2 % in operation. A short peak at start-up while the project is read. |
| Visualisation, per session | approx. 75 MB | a few % up to approx. 10 % while the screen updates |
| Database | approx. 115 bytes per telegram | – |

Over SSH, only changes on the screen are sent.

The database grows by approx. 10 MB per day at 1 telegram/s. That is approx. 0.9 GB over 90 days.
Reduce `retention_days` if disk space is short. On a Raspberry Pi the CPU usage is higher than in the table,
but it has not been measured.
