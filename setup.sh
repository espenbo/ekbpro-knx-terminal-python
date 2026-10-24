#!/usr/bin/env bash
# Install EKBPRO KNX Terminal Python: Python virtual environment and dependencies.
set -e
cd "$(dirname "$0")"
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
chmod +x scada

if [ -f config.yaml ]; then
    ./scada import
else
    echo
    echo "Installed. Set up your own project with:"
    echo "  ./scada init /path/to/Project.knxproj --gateway <IP of the KNX IP interface> [--language nb]"
    echo "or try it without an installation:"
    echo "  ./scada init /path/to/Project.knxproj && ./scada server --demo"
fi
