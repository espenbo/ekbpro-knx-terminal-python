"""Overview model: setpoint and heating of rooms without ETS roles."""

from knxscada.tui.home import HomeModel


def _model():
    """A minimal project model: one room with a heating function whose datapoints have no ETS roles, only labels."""
    dps = [
        {"label": "Set Temp", "role": "", "kind": "setpoint", "ga": "2/1/0", "status": None, "dpt": "9.001"},
        {"label": "Er-Verdi", "role": "", "kind": "value", "ga": None, "status": "2/0/0", "dpt": "9.001"},
        {"label": "Varmepaadrag", "role": "", "kind": "value", "ga": None, "status": "2/3/0", "dpt": "5.001"},
    ]
    return {
        "gas": {},
        "floors": [{"name": "1. etg", "rooms": [{"id": "r1", "name": "H1001 Vf", "functions": ["f1"]}]}],
        "functions": {"f1": {"id": "f1", "name": "Varme", "usage": "heating radiator", "datapoints": dps}},
    }


def test_setpoint_and_valve_without_roles():
    """Room temperature, setpoint and valve are found from labels/kinds when ETS gives no roles, and a valve at 0 %
    means not heating.
    """
    home = HomeModel(_model(), {})
    (room,) = home.rooms
    assert (room.temp_ga, room.sp_ga, room.heat_ga) == ("2/0/0", "2/1/0", "2/3/0")
    state = {"2/0/0": {"value": 21.3}, "2/1/0": {"value": 21.0}, "2/3/0": {"value": 57}}
    st = home.room_status(room, state, [])
    assert (st["temp"], st["sp"], st["heating"]) == (21.3, 21.0, True)
    state["2/3/0"] = {"value": 0}
    assert home.room_status(room, state, [])["heating"] is False


def test_old_values_are_marked():
    """Values from the database of the last run are reported as old until the bus sends new ones."""
    home = HomeModel(_model(), {})
    (room,) = home.rooms
    state = {"2/0/0": {"value": 21.3, "old": True}, "2/1/0": {"value": 21.0}, "2/3/0": {"value": 57, "old": True}}
    assert home.room_status(room, state, [])["old"] == {"temp", "heating"}
    state["2/0/0"] = {"value": 21.4}
    assert home.room_status(room, state, [])["old"] == {"heating"}
