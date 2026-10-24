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
  and group). Only give trusted users membership of the group.
- Every operation is logged with the Linux user and the SSH client's IP in the event log (F6).
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
