"""The Textual terminal user interface (TUI) of the SCADA – a client of the server.

The TUI holds no bus connection of its own: :class:`~knxscada.tui.app.ScadaApp` (``app.py``) talks JSON with the
``ScadaServer`` over its Unix socket (``knxscada.client``), keeps a local copy of the state, telegrams, events and
alarms, and hands every server message to the views. Writes go to the server, which logs the user and sends them to
KNX or to a protocol connection; the screen changes only when the feedback arrives.

Main modules: ``app.py`` (menu, keys, message routing), ``views.py`` (overview, rooms, group addresses, bus monitor,
alarms, events, trends), ``widgets.py`` (card rows and fast table/log widgets), ``dialogs.py``, ``charts.py``
(plotext helpers), ``graphics.py`` (sparklines, big digits), ``home.py`` (per-room summary), ``splash.py`` (start-up and About), plus the views for the floor
plan, systems, energy, gateways, protocol monitor and the screensaver. Styles are in ``app.tcss``, themes in
``themes.py``, logos in ``logos/``.
"""
