# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
#
# EKBPRO KNX Terminal Python in a container – e.g. on a WAGO PFC200/PFC300 or any
# Linux machine with Docker (amd64, arm64 or armv7). Guide: docs/09-docker.md
#
# Code lives in /app, everything that belongs to *your* installation in /data
# (a volume): config.yaml, the .knxproj, plan.yaml, the database and the socket.
#
#   docker build -t ekbpro-knx .
#   docker run -d --name knx --network host -v /home/knx:/data ekbpro-knx          # server
#   docker exec -it knx knx                                                         # visualisation

FROM python:3.13-slim

# tzdata: local time in logs and charts (set TZ when running the container).
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    TZ=Europe/Oslo \
    KNXSCADA_CONFIG=/data/config.yaml \
    XDG_CONFIG_HOME=/data/.config \
    PYTHONPATH=/app \
    PYTHONSAFEPATH=1

WORKDIR /app
COPY requirements.txt .
# Only prebuilt wheels: no compiler needed, also on ARM (aarch64, armv7).
RUN pip install --no-cache-dir --root-user-action=ignore --prefer-binary -r requirements.txt

COPY knxscada/ knxscada/
COPY config.example.yaml plan.example.yaml LICENSE ./
COPY docker/knx /usr/local/bin/knx
RUN chmod 755 /usr/local/bin/knx

VOLUME /data
WORKDIR /data
# The server stops cleanly on SIGTERM (docker stop).
ENTRYPOINT ["python", "-m", "knxscada"]
CMD ["server"]
