#!/usr/bin/env bash
# Install EKBPRO KNX Terminal Python: Python virtual environment and dependencies.
#
#   ./setup.sh                  asks which protocol modules to install (Enter = knx; without a terminal: knx)
#   ./setup.sh knx modbus       installs these protocol modules (see docs/en/11-protocols-gateways.md)
#   ./setup.sh modbus opcua     a site without KNX: the KNX packages are left out
set -e
cd "$(dirname "$0")"
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
chmod +x scada

modules="$*"
if [ -z "$modules" ] && [ -t 0 ]; then
    echo
    ./scada modules
    read -r -p "Protocol modules to install, e.g. knx modbus opcua (Enter = knx): " modules
fi
# KNX is what most installations use, and what this program started with: the default when nothing is chosen.
modules="${modules:-knx}"
if [ -n "$modules" ]; then
    # shellcheck disable=SC2086 # one word per module
    ./scada modules install $modules
fi

if [ -f config.yaml ] && ./scada modules | grep -q "knx.*installed\|knx.*installert"; then
    ./scada import
else
    echo
    echo "Installed. Set up your own project with the questions of:"
    echo "  ./scada setup"
    echo "or in one line:"
    echo "  ./scada init /path/to/Project.knxproj --gateway <IP of the KNX IP interface> [--language nb]"
    echo "or try it without an installation:"
    echo "  ./scada init /path/to/Project.knxproj && ./scada server --demo"
    echo "Without KNX (only Modbus, MQTT, OPC UA): see docs/en/11-protocols-gateways.md"
fi
