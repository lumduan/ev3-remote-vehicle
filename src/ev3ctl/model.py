"""HOST CODE. The shapes the agent sends, and the arithmetic on them.

Rendering lives in ui.py and hardware lives on the brick. This module is
the thin layer between: it holds the port grid the tables are drawn on,
and it turns raw driver integers into the numbers a person can read.

Every conversion here refuses to guess. A scale factor that has not been
read off the device produces None, and None renders as a dash. A number
with no basis is worse than a blank, because the reader will believe it.
"""

# The tables always show these rows, in this order, whether or not
# anything is plugged in. A port that vanishes from the inventory has to
# still be visible as empty, otherwise "nothing is plugged into C" and
# "this tool failed to read C" look identical.
OUTPUT_PORTS = ("outA", "outB", "outC", "outD")
INPUT_PORTS = ("in1", "in2", "in3", "in4")

PORT_LABELS = {
    "outA": "A", "outB": "B", "outC": "C", "outD": "D",
    "in1": "1", "in2": "2", "in3": "3", "in4": "4",
}

# An EV3 pack sits in this range. A reading far outside it does not mean
# a flat battery; it means voltage_now was not in microvolts after all,
# and the honest response is to say so rather than print the number.
BATTERY_MIN_V = 6.0
BATTERY_MAX_V = 8.5

DASH = "-"


def port_key(address):
    """The bare port name out of a driver address.

    The brick reports "ev3-ports:outA"; the grid above, and most
    documentation, use "outA". Everything on this side is indexed on the
    bare form so the two line up, while each device keeps its own raw
    address for sending commands back.

    Read off the hardware on 2026-08-29; see ROADMAP.md. Before that,
    this project assumed the bare form and the motors table showed four
    empty rows with two motors plugged in.
    """
    if not address:
        return None
    return address.rsplit(":", 1)[-1]


def scaled(raw, decimals):
    """A sensor's real reading: value / 10 ** decimals."""
    if raw is None:
        return None
    if decimals is None:
        # The driver did not say. Returning the raw integer would be a
        # silent claim that decimals is 0, so say nothing instead.
        return None
    try:
        return float(raw) / (10 ** int(decimals))
    except (TypeError, ValueError, OverflowError):
        return None


def format_scaled(raw, decimals):
    value = scaled(raw, decimals)
    if value is None:
        return str(raw) if raw is not None else DASH
    if decimals in (0, None):
        return "{0:.0f}".format(value)
    return "{0:.{1}f}".format(value, min(int(decimals), 4))


def degrees(position, count_per_rot):
    """Motor position in degrees.

    count_per_rot is read from the device and is not assumed to be 360.
    If it is missing or zero there is no conversion to make, and the
    caller gets None rather than a plausible wrong answer.
    """
    if position is None or not count_per_rot:
        return None
    try:
        return float(position) / float(count_per_rot) * 360.0
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def battery_volts(voltage_now):
    """Microvolts to volts."""
    if voltage_now is None:
        return None
    return float(voltage_now) / 1_000_000.0


def battery_milliamps(current_now):
    """Microamps to milliamps."""
    if current_now is None:
        return None
    return float(current_now) / 1000.0


def battery_is_plausible(volts):
    if volts is None:
        return False
    return BATTERY_MIN_V <= volts <= BATTERY_MAX_V


def number(value, digits=0):
    if value is None:
        return DASH
    return "{0:.{1}f}".format(value, digits)


def text(value):
    if value is None or value == "":
        return DASH
    return str(value)


class Inventory(object):
    """One `scan` result, indexed by port address."""

    def __init__(self, payload=None):
        payload = payload or {}
        self.motors = _by_address(payload.get("motors") or [])
        self.sensors = _by_address(payload.get("sensors") or [])
        self.ports = _by_address(payload.get("ports") or [])
        self.battery = payload.get("battery") or {}
        self.nodes = payload.get("nodes") or {}

    def motor(self, address):
        return self.motors.get(address)

    def sensor(self, address):
        return self.sensors.get(address)

    def port(self, address):
        return self.ports.get(address)


class Snapshot(object):
    """One `poll` result. Fast-changing values only, keyed by address."""

    def __init__(self, payload=None):
        payload = payload or {}
        self.motors = _rekey(payload.get("motors"))
        self.sensors = _rekey(payload.get("sensors"))
        self.battery = payload.get("battery") or {}
        self.nodes = payload.get("nodes") or {}

    def motor(self, address):
        return self.motors.get(address)

    def sensor(self, address):
        return self.sensors.get(address)


def _by_address(items):
    indexed = {}
    for item in items:
        key = port_key(item.get("address"))
        if key:
            indexed[key] = item
    return indexed


def _rekey(mapping):
    """Re-key a poll payload onto the bare port names the grid uses."""
    rekeyed = {}
    for address, value in (mapping or {}).items():
        key = port_key(address)
        if key:
            rekeyed[key] = value
    return rekeyed


def nodes_changed(before, after):
    """True when the set of device nodes differs.

    This is the whole reason `poll` carries a node list. Re-scanning on
    every refresh would cost a full inventory at 5 Hz on a 300 MHz CPU;
    re-scanning when this returns True costs one scan per replug.
    """
    if not before or not after:
        return before != after
    for key in ("tacho-motor", "lego-sensor", "lego-port"):
        if list(before.get(key) or []) != list(after.get(key) or []):
            return True
    return False
