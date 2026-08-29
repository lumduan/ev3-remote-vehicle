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

import glob
import json
import os
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
    "hello", "scan", "poll", "motor_run", "motor_stop", "motor_reset",
    "sensor_mode", "stop_all", "bye",
)


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


def find_by_address(class_dir, address):
    # type: (str, str) -> str or None
    """Return the node directory whose address attribute matches.

    Node names are not port names. motor0 is not port A and sensor0 is
    not port 1; the numbering is the order the kernel happened to bind
    the devices in, and it changes when something is replugged. The only
    way to learn a port is to read the address attribute of each device,
    every time, which is what this does.
    """
    for node in list_nodes(class_dir):
        node_dir = os.path.join(class_dir, node)
        if read_attr(node_dir, "address") == address:
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

    def run(self, address, duty):
        # type: (str, object) -> dict
        duty = clamp_duty(duty)
        with self._lock:
            node_dir = self._resolve_motor(address)
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
            return {"address": address, "duty": duty}

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
    if name == "motor_stop":
        return control.stop(require(command, "address"))
    if name == "motor_reset":
        return control.reset(require(command, "address"))
    if name == "sensor_mode":
        return do_sensor_mode(require(command, "address"),
                              require(command, "mode"))
    if name == "stop_all":
        return control.stop_all()
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
        control.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
