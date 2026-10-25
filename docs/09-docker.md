# Docker (e.g. on a WAGO PFC200/PFC300)

The program can run in a container on any Linux machine with Docker – also on a PLC such as the WAGO PFC300,
which has Docker but no Python. The image contains only the program; everything that belongs to *your*
installation lives in a directory on the host that is mounted as `/data`:

```
/home/knx/                     on the host  =  /data in the container
  config.yaml                  your configuration (created by init)
  MyHouse.knxproj              the ETS project
  plan.yaml                    floor plan (optional)
  var/                         database, project cache and socket
  .config/knxscada/ui.json     colour theme and language chosen in the TUI
```

The server runs as the container's main process. The visualisation (TUI) is opened inside the same container
with `docker exec`, from an SSH session on the host.

## Requirements

- Docker (on the WAGO PFC: enable it in the web-based management, *Configuration → Docker*).
- Network access from the host to the KNX IP interface.
- About 250 MB disk space for the image. On the PFC, Docker stores its data on the large `/home` partition.

Tested on a WAGO PFC300 (750-8302, firmware 04.08.09, aarch64, Docker 25): all dependencies install as prebuilt
packages, no compiler is needed.

## 1. Get the program onto the host

The PFC has no `git`, but `curl` and `tar`:

```sh
cd /home
curl -L https://github.com/espenbo/ekbpro-knx-terminal-python/archive/refs/heads/main.tar.gz | tar xz
cd ekbpro-knx-terminal-python-main
```

## 2. Build the image

```sh
docker build -t ekbpro-knx .
```

This downloads the Python base image and the dependencies – a few minutes on a PFC.

## 3. Create the configuration

Copy your ETS project to the data directory (for example with `scp` from your PC), then let `init` write
`config.yaml` there:

```sh
mkdir -p /home/knx
# from the PC:  scp MyHouse.knxproj root@<plc>:/home/knx/
docker run --rm -v /home/knx:/data ekbpro-knx init /data/MyHouse.knxproj --gateway 192.168.1.50 --language nb
cp docker/knx-host.sh /home/knx/            # helper that opens the visualisation (step 5)
cp plan.example.yaml /home/knx/plan.yaml    # optional: floor plan to edit
```

Edit `/home/knx/config.yaml` with `vi` if needed (see [Configuration](04-configuration.md)).

## 4. Start the server

```sh
docker run -d --name knx \
  --restart unless-stopped \
  --network host \
  --cpus 1 --memory 512m \
  --log-opt max-size=5m --log-opt max-file=2 \
  -e TZ=Europe/Oslo \
  -v /home/knx:/data \
  ekbpro-knx
docker logs -f knx                          # Ctrl+C leaves the log, the server keeps running
```

| Option | Why |
|---|---|
| `--restart unless-stopped` | Starts again after a reboot or a crash (Docker itself must start at boot). |
| `--network host` | KNX tunneling uses UDP and routing uses multicast – both work without port mapping this way. |
| `--cpus 1 --memory 512m` | On a PLC the control program (e.g. CODESYS) must always get enough CPU. The server needs far less. |
| `--log-opt …` | Limits the container log, which saves the flash memory. |
| `-e TZ=…` | Local time in the event log and the charts. |

## 5. Open the visualisation

On the host (for example after `ssh root@<plc>`):

```sh
/home/knx/knx-host.sh                       # the visualisation
/home/knx/knx-host.sh tui --language en     # in English for this session
/home/knx/knx-host.sh info                  # project summary
```

Or directly from your PC: `ssh -t root@<plc> /home/knx/knx-host.sh`

The helper passes your SSH client address and terminal type into the container, so the event log shows who
operated what (`root@192.168.1.10`). Without it: `docker exec -it knx knx`.

## Try it without an installation

```sh
docker run --rm -it --network host -v /home/knx:/data --name knx-demo ekbpro-knx server --demo
# in a second session:
KNX_CONTAINER=knx-demo /home/knx/knx-host.sh
```

The demo uses its own database (`var/knxscada-demo.db`) and never touches the bus.

## Update

```sh
cd /home
rm -rf ekbpro-knx-terminal-python-main
curl -L https://github.com/espenbo/ekbpro-knx-terminal-python/archive/refs/heads/main.tar.gz | tar xz
cd ekbpro-knx-terminal-python-main
docker build -t ekbpro-knx .
docker rm -f knx                            # stops the server (it shuts down cleanly)
# … then the docker run command from step 4 again
docker image prune -f                       # removes the old image
```

Configuration, database and preferences stay in `/home/knx`. After changing the `.knxproj`, run
`/home/knx/knx-host.sh import` and restart the server with `docker restart knx`.

## Notes for the WAGO PFC

- **PLC bridge:** with `--network host` the container reaches the PLC's own OPC UA server at
  `opc.tcp://127.0.0.1:4840` – see [PLC bridge](10-plc-opcua.md). Run `plc-map` in the container
  (`/home/knx/knx-host.sh plc-map`); `plc_map.yaml` and `GVL_KNX.st` end up in `/home/knx`.

- **Flash memory:** the server writes the telegram log continuously (about 10–20 MB per day at 1–2
  telegrams/s, committed once per second). Keep the data directory on `/home`, and lower `retention_days` in
  `config.yaml` if the disk fills up.
- **No telegrams at start-up:** by default the server sends nothing to the bus when it starts
  (`read_on_start: false`, see [Configuration](04-configuration.md#server)).
- **One tunnel per server:** if the program also runs on another machine against the same KNX IP interface,
  each server uses its own tunnel, and each keeps its own log.
- **Firmware updates:** check afterwards that Docker is still enabled and the container is running
  (`docker ps`). If the image is missing, build it again (step 2); the data in `/home/knx` is kept.
- **Security:** the container runs with the host's network. Anyone who can run `docker` on the host can open the
  visualisation and operate the installation – on the PFC that is `root`.

## Backup

Everything that matters is in `/home/knx`. The database can be copied while the server is running:

```sh
docker exec knx python -c "import sqlite3; s=sqlite3.connect('/data/var/knxscada.db'); s.backup(sqlite3.connect('/data/var/backup.db'))"
```
