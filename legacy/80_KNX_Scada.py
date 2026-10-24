# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 EKB PRO
"""First prototype of the terminal SCADA (kept for reference only).

This script is NOT part of the program – use ``./scada`` instead. It polls
four hard-coded group addresses and prints them in a Rich panel.
"""

import asyncio
import time

from rich.console import Console
from rich.panel import Panel
from xknx import XKNX
from xknx.devices import Light, Sensor
from xknx.io import ConnectionConfig, ConnectionType

# KNX gateway (e.g. a WAGO PFC or Edge controller)
connection_config = ConnectionConfig(
    connection_type=ConnectionType.TUNNELING,
    gateway_ip="192.168.1.50",  # <-- set the correct IP here
    port=3671,
)

# Group addresses (hard-coded here; the real program reads them from the .knxproj)
LIGHT_KITCHEN = "1/1/10"
TEMP_LIVING_ROOM = "1/2/1"
HUMIDITY = "1/2/2"
DOOR_STATUS = "1/3/4"

console = Console()


async def main():
    xknx = XKNX(connection_config=connection_config)
    await xknx.start()

    light = Light(xknx, name="Kitchen light", group_address_switch=LIGHT_KITCHEN)
    temp = Sensor(xknx, name="Living room temperature", group_address_state=TEMP_LIVING_ROOM)
    humidity = Sensor(xknx, name="Humidity", group_address_state=HUMIDITY)
    door = Sensor(xknx, name="Door status", group_address_state=DOOR_STATUS)

    try:
        while True:
            for device in (light, temp, humidity, door):
                await device.sync()

            panel = Panel.fit(f"""
🏠 KNX SCADA

Living room temperature: {temp.resolve_state()} °C
Kitchen light:           [{"ON" if light.state else "OFF"}]
Door status:             [{door.resolve_state()}]
Humidity:                {humidity.resolve_state()} %

🕒 Last updated:         {time.strftime("%H:%M:%S")}
            """, title="80s terminal", subtitle="No sound 🔇", style="green")

            console.clear()
            console.print(panel)
            await asyncio.sleep(2)
    finally:
        await xknx.stop()


if __name__ == "__main__":
    asyncio.run(main())
