#!/usr/bin/env python3
"""BRICK CODE. The whole of this project's hardware access, in one file.

Runs on the EV3 brick under ev3dev, on CPython 3.5 with the standard
library and nothing else. Copied to /tmp/ev3_agent.py and launched as

    ssh robot@ev3dev.local python3 -u /tmp/ev3_agent.py

Reads one JSON command per line on stdin, writes exactly one JSON
response per command on stdout. stdout carries JSON and nothing else;
everything human-readable goes to stderr, so that a running session and
a person typing at a prompt can share the same program.

This file exists because the host cannot touch the hardware. rich does
not run here, ev3dev2 is not installed here, and nothing can be
installed here. The host owns rendering; this file owns sysfs. The two
are coupled by the protocol below and by nothing else.

Why a watchdog lives in here rather than on the host: a motor commanded
through run-direct keeps turning when the link dies. The host cannot
stop a motor over a cable that has been pulled. Only this side can, so
only this side is trusted to.
"""

import errno
import fcntl
import glob
import json
import os
import re
import select
import struct
import sys
import threading
import time

VERSION = "0.1.0"

TACHO_CLASS = "/sys/class/tacho-motor"
SENSOR_CLASS = "/sys/class/lego-sensor"
PORT_CLASS = "/sys/class/lego-port"
POWER_CLASS = "/sys/class/power_supply"

# If no command arrives for this long and a motor has been commanded
# non-zero in this session, every motor is stopped. One second is short
# enough that a pulled cable is not frightening and long enough that an
# ordinary slow round trip on a 300 MHz ARM9 does not trip it.
WATCHDOG_TIMEOUT_S = 1.0
WATCHDOG_TICK_S = 0.1

# A guard against a driver reporting a nonsense num_values and this
# program then trying to open thousands of files at 5 Hz.
MAX_SENSOR_VALUES = 32

COMMANDS = (
    "hello", "scan", "poll", "motor_run", "drive", "motor_stop",
    "motor_reset", "set_stop_action", "sensor_mode", "stop_all",
    "gamepad_open", "gamepad_state", "gamepad_reset_window",
    "gamepad_close", "bye",
)

# The values the tacho-motor driver accepts for stop_action. Read off
# this hardware on 2026-08-29: stop_actions is "coast brake hold", and
# the default is coast.
STOP_ACTIONS = ("coast", "brake", "hold")


class CommandError(Exception):
    """A command that failed for a reason the host should be told."""

    def __init__(self, message, kind="error"):
        # type: (str, str) -> None
        Exception.__init__(self, message)
        self.kind = kind


# ---------------------------------------------------------------------
# sysfs primitives
#
# Every read is allowed to fail on its own. One unreadable attribute
# reports itself as null and the scan carries on: a device that is being
# unplugged as we read it must not take the whole inventory with it.
# ---------------------------------------------------------------------

def read_attr(node_dir, name):
    # type: (str, str) -> str or None
    try:
        with open(os.path.join(node_dir, name), "r") as handle:
            return handle.read().strip()
    except Exception:
        return None


def read_int(node_dir, name):
    # type: (str, str) -> int or None
    text = read_attr(node_dir, name)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def read_words(node_dir, name):
    # type: (str, str) -> list or None
    text = read_attr(node_dir, name)
    if text is None:
        return None
    return text.split()


def write_attr(node_dir, name, value):
    # type: (str, str, object) -> str or None
    """Write one attribute. Returns None on success, else a reason."""
    try:
        with open(os.path.join(node_dir, name), "w") as handle:
            handle.write(str(value))
        return None
    except Exception as exc:
        return "{0}: {1}".format(name, exc)


def list_nodes(class_dir):
    # type: (str) -> list
    try:
        return sorted(os.listdir(class_dir))
    except Exception:
        return []


def port_key(address):
    # type: (str) -> str
    """The bare port name out of a driver address.

    This brick reports "ev3-ports:outA", not "outA". Both forms are in
    circulation - the bare one in most documentation and in this
    project's own port grid, the prefixed one in the driver - so
    comparisons are made on the bare form and neither is assumed to be
    the one that will turn up. Read on 2026-08-29 from
    /sys/class/tacho-motor/motor0/address on kernel 4.14.117-ev3dev.
    """
    if address is None:
        return None
    return address.rsplit(":", 1)[-1]


def find_by_address(class_dir, address):
    # type: (str, str) -> str or None
    """Return the node directory whose address attribute matches.

    Node names are not port names. motor0 is not port A and sensor0 is
    not port 1; the numbering is the order the kernel happened to bind
    the devices in, and it changes when something is replugged. The only
    way to learn a port is to read the address attribute of each device,
    every time, which is what this does.

    Either address form is accepted, so a host that says "outA" and a
    driver that says "ev3-ports:outA" still find each other.
    """
    target = port_key(address)
    for node in list_nodes(class_dir):
        node_dir = os.path.join(class_dir, node)
        found = read_attr(node_dir, "address")
        if found is None:
            continue
        if found == address or port_key(found) == target:
            return node_dir
    return None


def node_snapshot():
    # type: () -> dict
    """The set of device nodes, for change detection on the host.

    Three listdir calls. The host compares this between polls and asks
    for a fresh scan only when it changes, instead of paying for a full
    inventory at every refresh.
    """
    return {
        "tacho-motor": list_nodes(TACHO_CLASS),
        "lego-sensor": list_nodes(SENSOR_CLASS),
        "lego-port": list_nodes(PORT_CLASS),
    }


# ---------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------

def read_sensor_values(node_dir, num_values):
    # type: (str, int or None) -> list
    if num_values is None or num_values < 1:
        # The count is unreadable on this driver. value0 is still worth
        # trying; a sensor with no readable count usually still has one.
        single = read_int(node_dir, "value0")
        if single is None:
            return []
        return [single]
    count = min(num_values, MAX_SENSOR_VALUES)
    values = []
    for index in range(count):
        values.append(read_int(node_dir, "value" + str(index)))
    return values


def scan_motors():
    # type: () -> list
    motors = []
    for node in list_nodes(TACHO_CLASS):
        node_dir = os.path.join(TACHO_CLASS, node)
        motors.append({
            "node": node,
            "address": read_attr(node_dir, "address"),
            "driver_name": read_attr(node_dir, "driver_name"),
            "commands": read_words(node_dir, "commands"),
            "count_per_rot": read_int(node_dir, "count_per_rot"),
            "max_speed": read_int(node_dir, "max_speed"),
            "polarity": read_attr(node_dir, "polarity"),
            "stop_action": read_attr(node_dir, "stop_action"),
            "position": read_int(node_dir, "position"),
            "speed": read_int(node_dir, "speed"),
            "duty_cycle": read_int(node_dir, "duty_cycle"),
            "duty_cycle_sp": read_int(node_dir, "duty_cycle_sp"),
            "state": read_words(node_dir, "state"),
        })
    return motors


def scan_sensors():
    # type: () -> list
    sensors = []
    for node in list_nodes(SENSOR_CLASS):
        node_dir = os.path.join(SENSOR_CLASS, node)
        num_values = read_int(node_dir, "num_values")
        sensors.append({
            "node": node,
            "address": read_attr(node_dir, "address"),
            "driver_name": read_attr(node_dir, "driver_name"),
            "mode": read_attr(node_dir, "mode"),
            "modes": read_words(node_dir, "modes"),
            "num_values": num_values,
            "decimals": read_int(node_dir, "decimals"),
            "units": read_attr(node_dir, "units"),
            "values": read_sensor_values(node_dir, num_values),
        })
    return sensors


def scan_ports():
    # type: () -> list
    """Every lego-port, including the ones with nothing attached.

    An empty port has to be visible as empty. A port that simply vanishes
    from the inventory is indistinguishable from a port this program
    failed to read, and the whole purpose of the tool is to tell those
    two apart.
    """
    ports = []
    for node in list_nodes(PORT_CLASS):
        node_dir = os.path.join(PORT_CLASS, node)
        ports.append({
            "node": node,
            "address": read_attr(node_dir, "address"),
            "driver_name": read_attr(node_dir, "driver_name"),
            "mode": read_attr(node_dir, "mode"),
            "modes": read_words(node_dir, "modes"),
            "status": read_attr(node_dir, "status"),
        })
    return ports


def read_battery():
    # type: () -> dict
    """Battery state, from whatever power_supply node exists.

    The node name is not hardcoded because it has not been read off this
    brick. voltage_now and current_now are microvolts and microamps by
    the Linux power-supply convention; this returns them raw and lets the
    host convert and sanity-check, because a reading far outside the
    plausible range is information rather than a number to print.
    """
    entries = sorted(glob.glob(os.path.join(POWER_CLASS, "*")))
    if not entries:
        return {"node": None, "voltage_now": None, "current_now": None}
    node_dir = entries[0]
    return {
        "node": os.path.basename(node_dir),
        "voltage_now": read_int(node_dir, "voltage_now"),
        "current_now": read_int(node_dir, "current_now"),
    }


# ---------------------------------------------------------------------
# Motor control and the watchdog
# ---------------------------------------------------------------------

def clamp_duty(value):
    # type: (object) -> int
    try:
        duty = int(value)
    except (TypeError, ValueError):
        raise CommandError("duty must be a number", "bad_request")
    if duty > 100:
        return 100
    if duty < -100:
        return -100
    return duty


class MotorControl(object):
    """Every write to a tacho-motor goes through here, and so does the
    watchdog that undoes them.

    run-direct latches. Once duty_cycle_sp is non-zero the motor turns
    until something writes stop, and neither this process exiting nor
    the SSH link dying counts as something. The host is not a safety
    mechanism, because the host is exactly what disappears when the cable
    is pulled. This class is the safety mechanism.
    """

    def __init__(self):
        # Re-entrant: the watchdog calls stop_all while already holding.
        self._lock = threading.RLock()
        self._commanded = {}                  # address -> last duty sent
        self._last_command_at = time.monotonic()
        self._tripped = False
        self._stopping = threading.Event()

    # -- bookkeeping --------------------------------------------------

    def touch(self):
        # type: () -> None
        """Record that the host is still talking to us."""
        with self._lock:
            self._last_command_at = time.monotonic()
            self._tripped = False

    def _commanded_nonzero(self):
        # type: () -> bool
        for duty in self._commanded.values():
            if duty:
                return True
        return False

    # -- the writes ---------------------------------------------------

    def _resolve_motor(self, address):
        # type: (str) -> str
        node_dir = find_by_address(TACHO_CLASS, address)
        if node_dir is None:
            raise CommandError(
                "no tacho-motor at address {0}".format(address),
                "no_device",
            )
        return node_dir

    def _apply_locked(self, node_dir, address, duty):
        # type: (str, str, int) -> None
        """Put one motor into run-direct at one duty. Lock held.

        Shared by run() and drive() so there is exactly one code path
        that ever starts a motor turning, and exactly one place where
        the commanded duty is recorded for the watchdog.
        """
        problem = write_attr(node_dir, "command", "run-direct")
        if problem is not None:
            raise CommandError(
                "{0}: could not enter run-direct: {1}".format(
                    address, problem),
                "write_failed",
            )
        problem = write_attr(node_dir, "duty_cycle_sp", duty)
        if problem is not None:
            # run-direct is already latched and the setpoint is
            # whatever it was before. Leaving the driver armed with
            # an unknown setpoint is the one outcome worth avoiding.
            write_attr(node_dir, "command", "stop")
            raise CommandError(
                "{0}: could not set duty: {1}".format(address, problem),
                "write_failed",
            )
        self._commanded[address] = duty

    def _readback_locked(self, node_dir, address):
        # type: (str, str) -> dict
        """What the driver says about this motor now. Lock held.

        Returned with every drive so the host can show commanded against
        actual without spending a second round trip on a poll. command is
        not among these: it is write-only on this hardware.

        Deliberately only two values. Measured over USB on 2026-08-29, a
        sysfs attribute read costs about 9 ms on this brick, and a drive
        that returned position, state and duty_cycle_sp as well spent
        144 ms per round trip against 18 ms of actual link time - the
        readback was 60% of the cost of driving. duty_cycle and speed
        are what the display needs; the rest is available from `poll`
        for anything that is not in a control loop.
        """
        return {
            "address": address,
            "duty_cycle": read_int(node_dir, "duty_cycle"),
            "speed": read_int(node_dir, "speed"),
        }

    def run(self, address, duty):
        # type: (str, object) -> dict
        duty = clamp_duty(duty)
        with self._lock:
            node_dir = self._resolve_motor(address)
            self._apply_locked(node_dir, address, duty)
            return {"address": address, "duty": duty}

    def drive(self, left_address, left_duty, right_address, right_duty):
        # type: (str, object, str, object) -> dict
        """Apply both sides of a tank drive in one message.

        Two motor_run commands per loop iteration would double the round
        trips, and over Bluetooth PAN the round trip is the whole budget.

        If either side fails, both are stopped before the error is
        raised. A vehicle with one wheel driving and one refusing is
        worse than a vehicle that has stopped, and the caller cannot fix
        it faster than this can.
        """
        sides = (
            ("left", left_address, clamp_duty(left_duty)),
            ("right", right_address, clamp_duty(right_duty)),
        )
        with self._lock:
            applied = []
            for name, address, duty in sides:
                try:
                    node_dir = self._resolve_motor(address)
                    self._apply_locked(node_dir, address, duty)
                except CommandError:
                    self._stop_all_locked()
                    raise
                applied.append((name, address, node_dir))
            result = {}
            for name, address, node_dir in applied:
                result[name] = self._readback_locked(node_dir, address)
            return result

    def set_stop_action(self, address, value):
        # type: (str, str) -> dict
        """Choose what stop means for this motor.

        coast removes the drive and lets the motor freewheel, which is
        the driver default and measured at 0.66 s of rolling after the
        watchdog fires. brake and hold both bring it up short. This is
        the attribute that decides whether a vehicle stops or coasts
        away when the link dies.
        """
        if value not in STOP_ACTIONS:
            raise CommandError(
                "stop_action must be one of: {0}".format(
                    " ".join(STOP_ACTIONS)),
                "bad_request",
            )
        with self._lock:
            node_dir = self._resolve_motor(address)
            previous = read_attr(node_dir, "stop_action")
            problem = write_attr(node_dir, "stop_action", value)
            if problem is not None:
                raise CommandError(
                    "{0}: could not set stop_action: {1}".format(
                        address, problem),
                    "write_failed",
                )
            return {
                "address": address,
                "previous": previous,
                "stop_action": read_attr(node_dir, "stop_action"),
                "available": read_words(node_dir, "stop_actions"),
            }

    def stop(self, address):
        # type: (str) -> dict
        with self._lock:
            node_dir = self._resolve_motor(address)
            problem = write_attr(node_dir, "command", "stop")
            self._commanded[address] = 0
            if problem is not None:
                raise CommandError(
                    "{0}: could not stop: {1}".format(address, problem),
                    "write_failed",
                )
            return {"address": address}

    def reset(self, address):
        # type: (str) -> dict
        with self._lock:
            node_dir = self._resolve_motor(address)
            problem = write_attr(node_dir, "command", "reset")
            self._commanded[address] = 0
            if problem is not None:
                raise CommandError(
                    "{0}: could not reset: {1}".format(address, problem),
                    "write_failed",
                )
            return {"address": address}

    def stop_all(self):
        # type: () -> dict
        with self._lock:
            return {"stopped": self._stop_all_locked()}

    def _stop_all_locked(self):
        # type: () -> list
        """Stop every tacho-motor. Never raises.

        This is called from finally blocks and from the watchdog, both of
        which run when something has already gone wrong. It walks the
        class directory rather than the address index on purpose: a
        device whose address attribute has become unreadable still has a
        command attribute, and still has to be stopped.
        """
        stopped = []
        for node in list_nodes(TACHO_CLASS):
            node_dir = os.path.join(TACHO_CLASS, node)
            problem = write_attr(node_dir, "command", "stop")
            stopped.append({
                "node": node,
                "address": read_attr(node_dir, "address"),
                "error": problem,
            })
        self._commanded = {}
        return stopped

    # -- the watchdog -------------------------------------------------

    def watchdog_loop(self):
        # type: () -> None
        while not self._stopping.wait(WATCHDOG_TICK_S):
            try:
                self._watchdog_tick()
            except Exception as exc:
                # A watchdog that dies on an unexpected error is worse
                # than no watchdog, because it looks like one.
                warn("watchdog tick failed: {0}".format(exc))

    def _watchdog_tick(self):
        # type: () -> None
        with self._lock:
            if self._tripped or not self._commanded_nonzero():
                return
            idle = time.monotonic() - self._last_command_at
            if idle < WATCHDOG_TIMEOUT_S:
                return
            self._tripped = True
            self._stop_all_locked()
        warn("watchdog: no command for {0:.1f}s, all motors stopped".format(
            idle))

    def shutdown(self):
        # type: () -> None
        """Stop the watchdog thread, then stop every motor. Never raises."""
        self._stopping.set()
        try:
            with self._lock:
                self._stop_all_locked()
        except Exception as exc:
            warn("shutdown stop_all failed: {0}".format(exc))


# ---------------------------------------------------------------------
# The gamepad
#
# Nothing here touches a motor, and nothing here may block the command
# loop above. A gamepad produces events far faster than a 5 Hz poll can
# collect them and much faster than this link can carry them, so a
# reader thread accumulates into counters and the host asks for the
# counters. Individual events are never queued and never sent.
# ---------------------------------------------------------------------

INPUT_DEVICES_PATH = "/proc/bus/input/devices"
INPUT_DIR = "/dev/input"

GAMEPAD_NAME = "Wireless Controller"

# struct input_event on a 32-bit ARM kernel: struct timeval (two 32-bit
# longs), then __u16 type, __u16 code, __s32 value. Sixteen bytes.
#
# "=" pins standard sizes, which is the whole point. The native "@llHHi"
# is 24 bytes on a 64-bit host because time_t is 8 bytes there, and 24 is
# the number that gets assumed by anyone who tried this on a laptop
# first. Parsing 16-byte records as 24-byte ones does not raise; it
# silently yields nonsense.
EVENT_STRUCT = struct.Struct("=llHHi")

# struct input_absinfo: six __s32 - value, minimum, maximum, fuzz, flat,
# resolution.
ABSINFO_STRUCT = struct.Struct("=6i")

# EVIOCGABS(code) = _IOR('E', 0x40 + code, struct input_absinfo), which
# expands to (2 << 30) | (24 << 16) | (ord('E') << 8) | (0x40 + code).
# The code lands in the low byte, so adding it to the base is the same
# as recomputing the macro. Python 3.5's fcntl.ioctl takes the request
# as an unsigned int, so this value needs no sign conversion.
EVIOCGABS_BASE = 0x80184540
ABS_CODE_MAX = 0x3F

EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03

# Event types worth accumulating. EV_SYN is a frame marker carrying no
# value, and EV_MSC on this driver is a scancode echo of a button that
# is already reported as EV_KEY; both would only add rows.
GAMEPAD_TYPES = (EV_KEY, EV_ABS)

GAMEPAD_READ_TICK_S = 0.05
GAMEPAD_READ_CHUNK = 64 * EVENT_STRUCT.size
GAMEPAD_JOIN_TIMEOUT_S = 1.0

# Distinct values remembered per code, so that a trigger reporting only
# its two extremes can be told from one sweeping through them. Bounded
# because this brick has 64 MB of RAM and an axis could otherwise
# accumulate a set as large as its range.
GAMEPAD_DISTINCT_CAP = 64

# The column order of one row from gamepad_state. Returned by
# gamepad_open as well, so that the host can check the two sides agree
# rather than silently reading the wrong field.
GAMEPAD_STATE_COLUMNS = (
    "type", "code", "latest", "min", "max", "count", "sum",
    "distinct", "interior", "overflow",
)

# Six colon-separated hex pairs: a Bluetooth adapter address. Anchored
# at the start only, because hid-sony appends a suffix on some kernels.
_MAC_PHYS = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}", re.IGNORECASE)

_EVENT_HANDLER = re.compile(r"^event\d+$")


def parse_input_devices(text):
    # type: (str) -> list
    """Every block of /proc/bus/input/devices, as a list of dicts.

    The format comes from input_devices_seq_show in
    drivers/input/input.c: one block per device, blocks separated by a
    blank line, every line prefixed by a letter and a colon, and
    Handlers space separated with a trailing space.

    A line that does not fit is skipped rather than raising. This file
    is read while devices are appearing and disappearing, and one
    malformed block must not cost the caller the device it wanted.
    """
    blocks = []
    current = _empty_input_block()
    seen = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            if seen:
                blocks.append(current)
            current = _empty_input_block()
            seen = False
            continue
        if len(line) < 2 or line[1] != ":":
            continue
        seen = True
        _absorb_input_line(current, line[0], line[2:].strip())
    if seen:
        blocks.append(current)
    return blocks


def _empty_input_block():
    # type: () -> dict
    return {
        "name": None, "phys": None, "uniq": None, "sysfs": None,
        "bus": None, "vendor": None, "product": None, "version": None,
        "handlers": [], "event": None, "abs_mask": None, "ev_mask": None,
    }


def _absorb_input_line(block, prefix, body):
    # type: (dict, str, str) -> None
    if prefix == "I":
        for field in body.split():
            key, _, value = field.partition("=")
            if key.lower() in ("bus", "vendor", "product", "version"):
                block[key.lower()] = _parse_hex(value)
    elif prefix == "N":
        block["name"] = _unquote(_field_value(body, "Name"))
    elif prefix == "P":
        block["phys"] = _field_value(body, "Phys")
    elif prefix == "U":
        block["uniq"] = _field_value(body, "Uniq")
    elif prefix == "S":
        block["sysfs"] = _field_value(body, "Sysfs")
    elif prefix == "H":
        handlers = (_field_value(body, "Handlers") or "").split()
        block["handlers"] = handlers
        for handler in handlers:
            if _EVENT_HANDLER.match(handler):
                block["event"] = handler
                break
    elif prefix == "B":
        key, _, value = body.partition("=")
        name = key.strip().upper()
        if name == "ABS":
            block["abs_mask"] = value.strip() or None
        elif name == "EV":
            block["ev_mask"] = value.strip() or None


def _field_value(body, key):
    # type: (str, str) -> str or None
    head, sep, tail = body.partition("=")
    if not sep or head.strip() != key:
        return None
    return tail.strip() or None


def _unquote(value):
    # type: (str) -> str or None
    if value is None:
        return None
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def _parse_hex(value):
    # type: (str) -> int or None
    try:
        return int(value, 16)
    except (TypeError, ValueError):
        return None


def find_input_devices(name):
    # type: (str) -> list
    """Every input device whose Name is exactly `name`.

    Exact equality, never a substring test. hid-sony creates three input
    devices for one DualShock 4 - "Wireless Controller", "Wireless
    Controller Touchpad" and "Wireless Controller Motion Sensors" - so a
    substring match returns all three on every run, and the caller's
    refusal to proceed on an ambiguous match would fire every time.

    With exact matching, more than one result means what it is meant to
    mean: the same pad arriving over two transports at once. Those use
    different HID report layouts, so a mapping captured from the wrong
    one is wrong without ever looking wrong.
    """
    text = read_attr(os.path.dirname(INPUT_DEVICES_PATH),
                     os.path.basename(INPUT_DEVICES_PATH))
    if text is None:
        return []
    return [b for b in parse_input_devices(text) if b.get("name") == name]


def transport_of(bus, phys):
    # type: (int, str) -> tuple
    """The transport, and how the two independent readings got along.

    BUS_BLUETOOTH is 0x05 and BUS_USB is 0x03, so the bustype settles it
    outright. The shape of Phys is read as well and compared: an adapter
    address means Bluetooth, a path containing "usb" means USB. When the
    two disagree that is reported rather than resolved, because a
    mapping captured over the wrong transport is silently wrong and this
    is the only place it could be caught.
    """
    from_bus = None
    if bus == 0x05:
        from_bus = "bluetooth"
    elif bus == 0x03:
        from_bus = "usb"

    from_phys = None
    if phys:
        text = phys.strip()
        if _MAC_PHYS.match(text):
            from_phys = "bluetooth"
        elif "usb" in text.lower():
            from_phys = "usb"

    if from_bus and from_phys:
        if from_bus == from_phys:
            return from_bus, "agree"
        return from_bus, "disagree"
    if from_bus:
        return from_bus, "bus-only"
    if from_phys:
        return from_phys, "phys-only"
    return None, "unknown"


def read_absinfo(fd, code):
    # type: (int, int) -> dict or None
    """One axis's limits from the driver, or None if it will not say.

    This is what makes "80 percent of the range" a measurement rather
    than a guess: the minimum and maximum come from the driver that owns
    the device. `flat` is the driver's own deadzone hint and is worth
    carrying alongside the one derived from measured jitter.

    An axis the device does not have usually answers with all zeros
    rather than failing, so a zero-width range is treated as absent.
    """
    buffer = bytearray(ABSINFO_STRUCT.size)
    try:
        fcntl.ioctl(fd, EVIOCGABS_BASE + code, buffer, True)
    except Exception:
        return None
    try:
        fields = ABSINFO_STRUCT.unpack_from(bytes(buffer))
    except Exception:
        return None
    value, minimum, maximum, fuzz, flat, resolution = fields
    if maximum <= minimum:
        return None
    return {
        "value": value, "minimum": minimum, "maximum": maximum,
        "fuzz": fuzz, "flat": flat, "resolution": resolution,
    }


def decode_events(data):
    # type: (bytes) -> tuple
    """Whole input_event records out of a buffer, plus the remainder.

    A read can end mid-record. The tail is handed back so the caller can
    put it in front of the next read rather than discarding it, which
    would corrupt every record after the first short one.
    """
    size = EVENT_STRUCT.size
    events = []
    offset = 0
    while len(data) - offset >= size:
        fields = EVENT_STRUCT.unpack_from(data, offset)
        events.append((fields[2], fields[3], fields[4]))
        offset += size
    return events, data[offset:]


class GamepadReader(object):
    """One evdev device, read by a thread, summarised into counters.

    The thread never touches MotorControl and never calls touch(). A
    gamepad being waggled is not evidence that the host is alive, and
    letting it look like evidence would keep the motor watchdog quiet
    exactly when the link had died.
    """

    def __init__(self):
        # type: () -> None
        # The same shape as MotorControl: an RLock over all shared
        # state, an Event that is both the sleep and the exit check, and
        # a daemon thread so a wedged reader cannot hold the process up.
        self._lock = threading.RLock()
        self._stopping = threading.Event()
        self._thread = None
        self._fd = -1
        self._device = None
        self._absinfo = {}
        self._codes = {}
        self._total = 0
        self._gone = False
        self._residue = b""

    # -- lifecycle ----------------------------------------------------

    def open(self, name):
        # type: (str) -> dict
        """Find the pad, open it non-blocking, and start reading."""
        self.close()

        matches = find_input_devices(name)
        if not matches:
            raise CommandError(
                "no input device named {0!r}. The pad is off, or it has "
                "not reconnected: press PS".format(name),
                "no_gamepad",
            )
        if len(matches) > 1:
            raise CommandError(
                "{0} devices are named {1!r}: {2}. That is the same pad "
                "on two transports at once, which use different HID "
                "report layouts. Disconnect one and start again".format(
                    len(matches), name, _describe_matches(matches)),
                "ambiguous_gamepad",
            )

        device = matches[0]
        if not device.get("event"):
            raise CommandError(
                "{0!r} has no event handler in its Handlers line: {1}"
                .format(name, " ".join(device.get("handlers") or [])),
                "no_gamepad",
            )
        path = os.path.join(INPUT_DIR, device["event"])
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            raise CommandError(
                "could not open {0}: {1}".format(path, exc), "no_gamepad")

        transport, agreement = transport_of(device.get("bus"),
                                            device.get("phys"))
        absinfo = {}
        for code in range(ABS_CODE_MAX + 1):
            info = read_absinfo(fd, code)
            if info is not None:
                absinfo[code] = info

        with self._lock:
            self._fd = fd
            self._device = device
            self._absinfo = absinfo
            self._gone = False
            self._residue = b""
            self._total = 0
            self._codes = {}
            # Seed every axis from the value the driver reports right
            # now, so an axis nobody touches still reports where it is
            # resting instead of a zero it never sent.
            for code, info in absinfo.items():
                self._codes[(EV_ABS, code)] = _new_counter(info["value"])
            self._stopping.clear()
            self._thread = threading.Thread(target=self._read_loop)
            self._thread.daemon = True
            self._thread.start()

        return {
            "name": device.get("name"),
            "phys": device.get("phys"),
            "uniq": device.get("uniq"),
            "bus": device.get("bus"),
            "vendor": device.get("vendor"),
            "product": device.get("product"),
            "version": device.get("version"),
            "sysfs": device.get("sysfs"),
            "handlers": device.get("handlers"),
            "event": device.get("event"),
            "path": path,
            "abs_mask": device.get("abs_mask"),
            "transport": transport,
            "transport_agreement": agreement,
            "absinfo": dict(
                (str(code), info) for code, info in absinfo.items()),
            "columns": list(GAMEPAD_STATE_COLUMNS),
            "event_struct_size": EVENT_STRUCT.size,
        }

    def close(self):
        # type: () -> dict
        """Stop the thread and close the device. Never raises.

        Also called from the agent's teardown, after the motors have
        been dealt with, so every step is individually optional and the
        join is bounded. A reader that will not stop must not be able to
        delay anything.
        """
        thread = None
        with self._lock:
            was_open = self._fd >= 0
            self._stopping.set()
            thread = self._thread
            self._thread = None
        if thread is not None:
            try:
                thread.join(GAMEPAD_JOIN_TIMEOUT_S)
            except Exception:
                pass
        with self._lock:
            if self._fd >= 0:
                try:
                    os.close(self._fd)
                except Exception as exc:
                    warn("gamepad close failed: {0}".format(exc))
                self._fd = -1
            self._device = None
        return {"closed": bool(was_open)}

    # -- the reader ---------------------------------------------------

    def _read_loop(self):
        # type: () -> None
        while not self._stopping.is_set():
            try:
                self._read_once()
            except Exception as exc:
                # A reader that dies on an unexpected error is worse
                # than no reader, because it still looks like one.
                warn("gamepad read failed: {0}".format(exc))
                with self._lock:
                    self._gone = True
                return

    def _read_once(self):
        # type: () -> None
        with self._lock:
            fd = self._fd
        if fd < 0:
            self._stopping.wait(GAMEPAD_READ_TICK_S)
            return
        # select is both the sleep and the responsiveness to close: a
        # blocking read here would hold the device open for as long as
        # the operator left the pad alone.
        ready, _, _ = select.select([fd], [], [], GAMEPAD_READ_TICK_S)
        if not ready:
            return
        try:
            data = os.read(fd, GAMEPAD_READ_CHUNK)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR):
                # Nothing there after all, or a signal landed between the
                # select and the read. Transient, and treating it as the
                # pad walking away would stop the reader for good on a
                # controller that is still perfectly present.
                return
            # ENODEV is the pad actually walking away mid-session, which
            # is an ordinary thing for a Bluetooth device with a flat
            # battery to do. Report it and stop reading rather than
            # spinning on a dead descriptor.
            warn("gamepad gone: {0}".format(exc))
            with self._lock:
                self._gone = True
            self._stopping.set()
            return
        if not data:
            return
        with self._lock:
            events, self._residue = decode_events(self._residue + data)
            for event_type, code, value in events:
                if event_type not in GAMEPAD_TYPES:
                    continue
                self._total += 1
                self._accumulate(event_type, code, value)

    def _accumulate(self, event_type, code, value):
        # type: (int, int, int) -> None
        """Fold one event into its counters. Caller holds the lock."""
        key = (event_type, code)
        counter = self._codes.get(key)
        if counter is None:
            # A code seen for the first time starts where it is. Buttons
            # arrive this way; axes were seeded from absinfo at open.
            counter = _new_counter(value)
            self._codes[key] = counter
        counter["latest"] = value
        if value < counter["min"]:
            counter["min"] = value
        if value > counter["max"]:
            counter["max"] = value
        counter["count"] += 1
        counter["sum"] += value
        if len(counter["distinct"]) < GAMEPAD_DISTINCT_CAP:
            counter["distinct"].add(value)
        elif value not in counter["distinct"]:
            counter["overflow"] = True

    # -- the host's view ----------------------------------------------

    def state(self):
        # type: () -> dict
        """The counters as they stand. Returns at once, never waits."""
        with self._lock:
            if self._fd < 0 and not self._gone:
                raise CommandError(
                    "the gamepad is not open; send gamepad_open first",
                    "no_gamepad",
                )
            rows = []
            for key in sorted(self._codes):
                counter = self._codes[key]
                low = counter["min"]
                high = counter["max"]
                interior = len([v for v in counter["distinct"]
                                if low < v < high])
                rows.append([
                    key[0], key[1], counter["latest"], low, high,
                    counter["count"], counter["sum"],
                    len(counter["distinct"]), interior,
                    bool(counter["overflow"]),
                ])
            return {
                "present": self._fd >= 0 and not self._gone,
                "device_gone": bool(self._gone),
                "total_events": self._total,
                "rows": rows,
            }

    def reset_window(self):
        # type: () -> dict
        """Clear the window without closing the device.

        Each wizard step measures only what happened during it, so this
        resets the extremes to wherever the control is sitting right
        now. The device stays open: reopening it between steps would
        lose events during the gap and make the pad look intermittent.
        """
        with self._lock:
            if self._fd < 0:
                raise CommandError(
                    "the gamepad is not open; send gamepad_open first",
                    "no_gamepad",
                )
            for counter in self._codes.values():
                latest = counter["latest"]
                counter["min"] = latest
                counter["max"] = latest
                counter["count"] = 0
                counter["sum"] = 0
                counter["distinct"] = set([latest])
                counter["overflow"] = False
            self._total = 0
            return {"reset": True, "codes": len(self._codes)}


def _new_counter(value):
    # type: (int) -> dict
    return {
        "latest": value, "min": value, "max": value,
        "count": 0, "sum": 0, "distinct": set([value]), "overflow": False,
    }


def _describe_matches(matches):
    # type: (list) -> str
    parts = []
    for block in matches:
        parts.append("{0} phys={1} bus={2}".format(
            block.get("event") or "?",
            block.get("phys") or "?",
            block.get("bus")))
    return "; ".join(parts)


GAMEPAD = GamepadReader()


# ---------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------

def do_hello():
    # type: () -> dict
    info = os.uname()
    release = None
    try:
        with open("/etc/ev3dev-release", "r") as handle:
            release = handle.read().strip()
    except Exception:
        # Absent on some images. Null is the answer, not a failure.
        release = None
    return {
        "agent_version": VERSION,
        "hostname": info[1],
        "kernel": info[2],
        "uname": " ".join(info),
        "python": sys.version,
        "ev3dev_release": release,
    }


def do_scan():
    # type: () -> dict
    return {
        "motors": scan_motors(),
        "sensors": scan_sensors(),
        "ports": scan_ports(),
        "battery": read_battery(),
        "nodes": node_snapshot(),
    }


def do_poll():
    # type: () -> dict
    """Only the values that change, keyed by port address.

    num_values is re-read rather than cached because changing a sensor's
    mode changes how many values it reports, and a cached count would
    silently truncate or over-read after every mode change.
    """
    motors = {}
    for node in list_nodes(TACHO_CLASS):
        node_dir = os.path.join(TACHO_CLASS, node)
        address = read_attr(node_dir, "address")
        if address is None:
            continue
        motors[address] = {
            "position": read_int(node_dir, "position"),
            "speed": read_int(node_dir, "speed"),
            "duty_cycle": read_int(node_dir, "duty_cycle"),
            "duty_cycle_sp": read_int(node_dir, "duty_cycle_sp"),
            "state": read_words(node_dir, "state"),
        }
    sensors = {}
    for node in list_nodes(SENSOR_CLASS):
        node_dir = os.path.join(SENSOR_CLASS, node)
        address = read_attr(node_dir, "address")
        if address is None:
            continue
        num_values = read_int(node_dir, "num_values")
        sensors[address] = {
            "mode": read_attr(node_dir, "mode"),
            "decimals": read_int(node_dir, "decimals"),
            "units": read_attr(node_dir, "units"),
            "values": read_sensor_values(node_dir, num_values),
        }
    return {
        "motors": motors,
        "sensors": sensors,
        "battery": read_battery(),
        "nodes": node_snapshot(),
    }


def do_sensor_mode(address, mode):
    # type: (str, str) -> dict
    if not isinstance(mode, str) or not mode:
        raise CommandError("mode must be a non-empty string", "bad_request")
    node_dir = find_by_address(SENSOR_CLASS, address)
    if node_dir is None:
        raise CommandError(
            "no lego-sensor at address {0}".format(address), "no_device")
    problem = write_attr(node_dir, "mode", mode)
    if problem is not None:
        raise CommandError(
            "{0}: could not set mode {1}: {2}".format(address, mode, problem),
            "write_failed",
        )
    return {"address": address, "mode": read_attr(node_dir, "mode")}


def require(command, field):
    # type: (dict, str) -> object
    if field not in command:
        raise CommandError("missing field: {0}".format(field), "bad_request")
    return command[field]


def dispatch(command, control):
    # type: (dict, MotorControl) -> dict
    name = command.get("cmd")
    if name == "hello":
        return do_hello()
    if name == "scan":
        return do_scan()
    if name == "poll":
        return do_poll()
    if name == "motor_run":
        return control.run(require(command, "address"),
                           require(command, "duty"))
    if name == "drive":
        return control.drive(require(command, "left_address"),
                             require(command, "left_duty"),
                             require(command, "right_address"),
                             require(command, "right_duty"))
    if name == "set_stop_action":
        return control.set_stop_action(require(command, "address"),
                                       require(command, "value"))
    if name == "motor_stop":
        return control.stop(require(command, "address"))
    if name == "motor_reset":
        return control.reset(require(command, "address"))
    if name == "sensor_mode":
        return do_sensor_mode(require(command, "address"),
                              require(command, "mode"))
    if name == "stop_all":
        return control.stop_all()
    if name == "gamepad_open":
        return GAMEPAD.open(command.get("name") or GAMEPAD_NAME)
    if name == "gamepad_state":
        return GAMEPAD.state()
    if name == "gamepad_reset_window":
        return GAMEPAD.reset_window()
    if name == "gamepad_close":
        return GAMEPAD.close()
    if name == "bye":
        return {"bye": True}
    raise CommandError(
        "unknown command: {0!r}. Known: {1}".format(
            name, " ".join(COMMANDS)),
        "unknown_command",
    )


# ---------------------------------------------------------------------
# Wire
# ---------------------------------------------------------------------

def warn(message):
    # type: (str) -> None
    """Human-readable output. Never stdout; stdout is the protocol."""
    try:
        sys.stderr.write("ev3_agent: " + message + "\n")
        sys.stderr.flush()
    except Exception:
        pass


def respond(payload):
    # type: (dict) -> None
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def process(line, control):
    # type: (str, MotorControl) -> dict
    """Turn one input line into exactly one response object.

    Every path through this function returns a response carrying the id
    it was given. A command that produces no reply would leave the host
    waiting on a link that is, from its point of view, simply slow.
    """
    try:
        command = json.loads(line)
    except ValueError as exc:
        return {"id": None, "ok": False, "kind": "bad_json",
                "error": "could not parse JSON: {0}".format(exc)}
    if not isinstance(command, dict):
        return {"id": None, "ok": False, "kind": "bad_request",
                "error": "expected a JSON object, got {0}".format(
                    type(command).__name__)}

    request_id = command.get("id")
    # Any command at all counts as the host being alive, including a
    # malformed one - it still proves the link carries bytes.
    control.touch()
    try:
        return {"id": request_id, "ok": True,
                "result": dispatch(command, control)}
    except CommandError as exc:
        return {"id": request_id, "ok": False, "kind": exc.kind,
                "error": str(exc)}
    except Exception as exc:
        return {"id": request_id, "ok": False, "kind": "internal",
                "error": "{0}: {1}".format(type(exc).__name__, exc)}


USAGE = """ev3_agent {version} - reads JSON commands on stdin, one per line.

  {{"id": 1, "cmd": "hello"}}
  {{"id": 2, "cmd": "scan"}}
  {{"id": 3, "cmd": "motor_run", "address": "outA", "duty": 30}}
  {{"id": 4, "cmd": "stop_all"}}

Commands: {commands}

Ctrl-D to exit. Every motor is stopped on exit.

Typing by hand: a motor commanded non-zero is stopped again about
{timeout:.0f}s later by the watchdog, because from here a person thinking
about what to type next looks exactly like a link that has died.
"""


def main():
    # type: () -> int
    control = MotorControl()
    watchdog = threading.Thread(target=control.watchdog_loop)
    watchdog.daemon = True
    watchdog.start()

    if sys.stdin.isatty():
        warn("interactive mode")
        sys.stderr.write(USAGE.format(
            version=VERSION,
            commands=" ".join(COMMANDS),
            timeout=WATCHDOG_TIMEOUT_S,
        ))
        sys.stderr.flush()

    try:
        while True:
            line = sys.stdin.readline()
            if not line:
                # EOF. The host closed the pipe, or the link died. This
                # is an ordinary exit and runs the same finally below.
                break
            line = line.strip()
            if not line:
                continue
            reply = process(line, control)
            respond(reply)
            if reply.get("ok") and reply.get("result", {}).get("bye"):
                break
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        warn("fatal: {0}: {1}".format(type(exc).__name__, exc))
        return 1
    finally:
        # Motors first, always, and by the same call as before. The
        # gamepad is not a hazard: a device left open costs a file
        # descriptor on a process that is about to exit, while a motor
        # left commanded keeps turning until the battery is pulled. So
        # the stop-all keeps its place at the head of the teardown and
        # cannot be delayed by anything below it.
        control.shutdown()
        try:
            GAMEPAD.close()
        except Exception as exc:
            warn("gamepad teardown failed: {0}".format(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
