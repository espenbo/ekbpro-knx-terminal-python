#!/usr/bin/env bash
# Install EKBPRO KNX Terminal Python: Python virtual environment and dependencies.
#
#   ./setup.sh                  asks which protocol modules to install (Enter = none)
#   ./setup.sh modbus mqtt      installs these protocol modules too (see docs/en/11-protocols-gateways.md)
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
    read -r -p "Protocol modules to install, e.g. modbus mqtt (Enter = none): " modules
fi
if [ -n "$modules" ]; then
    # shellcheck disable=SC2086 # one word per module
    ./scada modules install $modules
fi

if [ -f config.yaml ]; then
    ./scada import
else
    echo
    echo "Installed. Set up your own project with:"
    echo "  ./scada init /path/to/Project.knxproj --gateway <IP of the KNX IP interface> [--language nb]"
    echo "or try it without an installation:"
    echo "  ./scada init /path/to/Project.knxproj && ./scada server --demo"
    echo "Without KNX (only Modbus, MQTT, OPC UA): see docs/en/11-protocols-gateways.md"
fi
