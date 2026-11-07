#!/usr/bin/env bash
# Install EKBPRO KNX Terminal Python on a new Debian/Ubuntu/Raspberry Pi OS machine with one command:
#
#   curl -fsSL https://raw.githubusercontent.com/espenbo/ekbpro-knx-terminal-python/main/install.sh | bash
#   bash install.sh [DIR]          # from a copy of this file; DIR defaults to ~/ekbpro-knx-terminal-python
#
# Steps: the system packages (python3, venv, git – with sudo, only those that are missing), git clone (or git pull
# when DIR already has the program), ./setup.sh (Python environment and the KNX module) and ./scada setup (the
# questions: ETS project, KNX connection, modules, service). Nothing is sent on the KNX bus.
set -euo pipefail

REPO="${KNXSCADA_REPO:-https://github.com/espenbo/ekbpro-knx-terminal-python.git}"
DIR="${1:-$HOME/ekbpro-knx-terminal-python}"

need=()
for pkg in python3 python3-venv python3-pip git; do
    dpkg -s "$pkg" >/dev/null 2>&1 || need+=("$pkg")
done
if [ ${#need[@]} -gt 0 ]; then
    echo "Installing system packages: ${need[*]}"
    sudo apt-get update
    sudo apt-get install -y "${need[@]}"
fi

if [ -d "$DIR/.git" ]; then
    echo "Updating $DIR"
    git -C "$DIR" pull --ff-only
else
    git clone "$REPO" "$DIR"
fi
cd "$DIR"

# The modules are chosen in ./scada setup; KNX is installed here as the usual start.
./setup.sh knx

# Piped from curl, stdin is the script itself: ask the questions on the terminal instead.
if [ -t 0 ]; then
    ./scada setup
elif [ -r /dev/tty ]; then
    ./scada setup </dev/tty
else
    echo "Installed in $DIR. Run ./scada setup there to configure it."
fi
