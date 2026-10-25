#!/bin/sh
# On the Docker host (e.g. the WAGO PFC): open the visualisation in the running container.
# Passes the SSH client address (for the event log: "user@ip") and the terminal type.
#   ./knx-host.sh                 -> visualisation
#   ./knx-host.sh tui --language en
#   ./knx-host.sh info
CONTAINER="${KNX_CONTAINER:-knx}"
TTY=""
[ -t 0 ] && TTY="-t"
exec docker exec -i $TTY \
    -e SSH_CLIENT="${SSH_CLIENT:-}" \
    -e TERM="${TERM:-xterm-256color}" \
    -e USER="${USER:-root}" \
    "$CONTAINER" knx "$@"
