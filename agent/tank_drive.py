#!/usr/bin/env python3
"""BRICK CODE. Tank drive from the DualShock 4's left stick.

Runs on the EV3 itself, on CPython 3.5 with the standard library and
nothing else. No computer is involved once it is started: the gamepad is
trusted, so it reconnects on its own, and this program reads it straight
from /dev/input. That is the whole point of spending the brick's one
Bluetooth radio on the pad.

    ssh robot@ev3dev.local python3 -u /tmp/tank_drive.py

Deliberately imports nothing from ev3_agent.py, exactly as
battery_report.py does, so that it still works when the agent is not
running - and so that a mistake here cannot reach the agent's motor
watchdog.

Every constant below that describes the controller was measured on this
hardware on 2026-09-01 and is named as such. Nothing is taken from a
published DualShock 4 layout: which axis is horizontal, and which way is
positive, were established by pushing the stick and watching, because a
mapping that is guessed is a mapping that steers backwards.

**Motors latch.** An ev3dev tacho-motor commanded through run-direct
keeps turning when this program exits, when it is killed, and when the
cable is pulled. Every path out of here stops both motors.
"""

import errno
import fcntl
import os
import re
import select
import signal
import struct
import sys
import time

# --- what the wizard measured, 2026-09-01 ----------------------------
#
# Left stick. ABS_X is horizontal and positive means right; ABS_Y is
# vertical and positive means down. Both established by holding the
# stick and reading which axis led, at ratios of 5.2 and 28.9 against
# the 3.0 required.
AXIS_X = 0
AXIS_Y = 1

# The driver's own deadzone hint, read with EVIOCGABS: flat=15 on every
# stick axis. Used in preference to the measured jitter, which was 1-2
# counts in one sitting and 0 in another - a still DualShock 4 emits no
# events at all. Resting values also move between connections (ABS_X was
# seen at 130, 133, 134.5, 135 and 138 on one afternoon), so 15 is the
# number that covers the drift rather than the noise.
DEADZONE = 15

# Motors. Found by reading each device's `address`, never by node name:
# motor0 is not port A, and the numbering changes between runs.
LEFT_PORT = "outA"
RIGHT_PORT = "outD"

# Top duty as a percentage, applied last to both sides equally. The one
# number to lower for a beginner or a heavy chassis.
SPEED_SCALE = 50

# Per loop iteration. A stick slammed to full would otherwise hand the
# gearbox a step change; this spreads it over about 250 ms.
SLEW_PCT_PER_LOOP = 10

LOOP_PERIOD_S = 0.05          # 20 Hz
STICK_PATH_NAME = "Wireless Controller"
INPUT_DEVICES = "/proc/bus/input/devices"
INPUT_DIR = "/dev/input"
TACHO_CLASS = "/sys/class/tacho-motor"

# struct input_event: struct timeval (two 32-bit longs on this kernel),
# then __u16 type, __u16 code, __s32 value. Sixteen bytes, confirmed by
# running struct.calcsize on the brick. The native "@llHHi" is 24 on a
# 64-bit development machine and parsing 16-byte records as 24-byte ones
# yields nonsense without raising.
EVENT = struct.Struct("=llHHi")
ABSINFO = struct.Struct("=6i")
EVIOCGABS_BASE = 0x80184540
EV_ABS = 0x03

_EVENT_NODE = re.compile(r"^event\d+$")


# ---------------------------------------------------------------------
# The gamepad
# ---------------------------------------------------------------------

def find_pad(name=STICK_PATH_NAME):
    # type: (str) -> str
    """The pad's event node, found by name. Never a hardcoded path.

    /dev/input/by-id does not exist on this brick and event4 is an
    observation from one session, not a stable path. hid-sony creates
    three devices for one controller, so the Name is matched on equality
    and the gamepad function is picked out by the one capability its two
    siblings lack: it declares BTN_SOUTH.
    """
    try:
        with open(INPUT_DEVICES, "r") as handle:
            text = handle.read()
    except Exception as exc:
        raise SystemExit("cannot read {0}: {1}".format(INPUT_DEVICES, exc))

    node = None
    block_name = None
    key_mask = None
    handlers = []
    for line in text.splitlines() + [""]:
        line = line.strip()
        if not line:
            if block_name == name and _has_btn_south(key_mask):
                for handler in handlers:
                    if _EVENT_NODE.match(handler):
                        node = handler
                        break
            block_name, key_mask, handlers = None, None, []
            continue
        if line.startswith("N: Name="):
            block_name = line[8:].strip().strip('"')
        elif line.startswith("H: Handlers="):
            handlers = line[12:].split()
        elif line.startswith("B: KEY="):
            key_mask = line[7:].strip()
    if node is None:
        raise SystemExit(
            "no gamepad found. Press PS on the controller and try again.")
    return os.path.join(INPUT_DIR, node)


def _has_btn_south(mask):
    # type: (str) -> bool
    """BTN_SOUTH is bit 304: bit 16 of word 9, words being 32 bits here.

    The kernel prints these with %lx and no zero padding, so a word can
    be shorter than its full width and position is what counts, never
    text length.
    """
    if not mask:
        return False
    words = mask.split()
    if len(words) < 10:
        return False
    try:
        return bool((int(words[len(words) - 10], 16) >> 16) & 1)
    except ValueError:
        return False


def read_rest(fd, code):
    # type: (int, int) -> tuple
    """One axis's current value and limits, straight from the driver.

    EVIOCGABS rather than a guess at the midpoint. A DualShock 4 stick
    does not rest at the middle of its range: ABS_X was measured at 136
    of 0-255 on this controller, so it has 136 counts of travel one way
    and 119 the other. Reading it at startup also picks up the drift
    between connections instead of trusting a number written down once.
    """
    buffer = bytearray(ABSINFO.size)
    fcntl.ioctl(fd, EVIOCGABS_BASE + code, buffer, True)
    value, minimum, maximum = ABSINFO.unpack_from(bytes(buffer))[:3]
    return value, minimum, maximum


# ---------------------------------------------------------------------
# The arithmetic
# ---------------------------------------------------------------------

def axis_fraction(value, rest, low, high, deadzone=DEADZONE):
    """One axis as -1.0 to 1.0, each direction on its own scale.

    The two directions are not the same size, so they are normalised
    separately. Dividing both by one symmetric figure would make the
    vehicle pull to one side at full deflection - on this controller by
    about 14 percent.
    """
    delta = value - rest
    if abs(delta) <= deadzone:
        return 0.0
    if delta > 0:
        span = (high - rest) - deadzone
        if span <= 0:
            return 0.0
        return min(1.0, (delta - deadzone) / float(span))
    span = (rest - low) - deadzone
    if span <= 0:
        return 0.0
    return -min(1.0, (-delta - deadzone) / float(span))


def tank(throttle, turn):
    """Mix to two sides, preserving the ratio between them.

    left = throttle + turn reaches 2.0 at full. Clamping each side
    independently would quietly change the turn-to-throttle ratio and
    full-forward-plus-full-left would come out as straight ahead.
    Dividing both by the larger keeps the ratio and gives up only
    absolute speed, which is the one the driver can see and correct for.
    """
    left = throttle + turn
    right = throttle - turn
    largest = max(abs(left), abs(right))
    if largest > 1.0:
        left /= largest
        right /= largest
    return left, right


def slew(current, target, limit=SLEW_PCT_PER_LOOP):
    """Step toward the target by at most `limit` this iteration."""
    delta = target - current
    if delta > limit:
        return current + limit
    if delta < -limit:
        return current - limit
    return target


# ---------------------------------------------------------------------
# The motors
# ---------------------------------------------------------------------

class Motors(object):
    """Both drive motors, and the promise that they end up stopped."""

    def __init__(self, left_port, right_port):
        self.paths = {}
        self.duty = {}
        for side, port in (("left", left_port), ("right", right_port)):
            node = self._find(port)
            if node is None:
                self.stop_all()
                raise SystemExit(
                    "no motor at {0}. Ports with a motor: {1}".format(
                        port, ", ".join(self._addresses()) or "none"))
            self.paths[side] = node
            self.duty[side] = 0.0
        self._handles = {}
        for side, node in self.paths.items():
            self._write(node, "command", "run-direct")
            self._handles[side] = open(
                os.path.join(node, "duty_cycle_sp"), "w")

    @staticmethod
    def _find(port):
        # type: (str) -> str or None
        """By reading each device's address. Node names are not ports."""
        try:
            nodes = sorted(os.listdir(TACHO_CLASS))
        except Exception:
            return None
        for node in nodes:
            directory = os.path.join(TACHO_CLASS, node)
            try:
                with open(os.path.join(directory, "address")) as handle:
                    address = handle.read().strip()
            except Exception:
                continue
            if address == port or address.rsplit(":", 1)[-1] == port:
                return directory
        return None

    @staticmethod
    def _addresses():
        found = []
        try:
            for node in sorted(os.listdir(TACHO_CLASS)):
                path = os.path.join(TACHO_CLASS, node, "address")
                with open(path) as handle:
                    found.append(handle.read().strip())
        except Exception:
            pass
        return found

    @staticmethod
    def _write(node, name, value):
        try:
            with open(os.path.join(node, name), "w") as handle:
                handle.write(str(value))
        except Exception:
            pass

    def drive(self, left, right):
        """Command both sides. Percentages, -100 to 100."""
        for side, value in (("left", left), ("right", right)):
            clamped = max(-100, min(100, int(round(value))))
            handle = self._handles.get(side)
            if handle is None:
                continue
            try:
                handle.seek(0)
                handle.write(str(clamped))
                handle.flush()
            except Exception:
                pass

    def stop_all(self):
        """Stop both motors. Never raises, however it is reached.

        Called from the finally block, sometimes while another exception
        is already on its way out. One motor that cannot be stopped must
        not prevent the other from stopping.
        """
        for side, node in getattr(self, "paths", {}).items():
            try:
                handle = self._handles.get(side)
                if handle is not None:
                    handle.seek(0)
                    handle.write("0")
                    handle.flush()
            except Exception:
                pass
            self._write(node, "command", "stop")
        for handle in getattr(self, "_handles", {}).values():
            try:
                handle.close()
            except Exception:
                pass


# ---------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------

def main():
    # type: () -> int
    path = find_pad()
    sys.stderr.write("gamepad: {0}\n".format(path))
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)

    rest = {}
    limits = {}
    for code in (AXIS_X, AXIS_Y):
        value, low, high = read_rest(fd, code)
        rest[code] = float(value)
        limits[code] = (low, high)
    sys.stderr.write(
        "rest: ABS_X {0:.0f} of {1}-{2}, ABS_Y {3:.0f} of {4}-{5}"
        "  (hands off the stick when starting)\n".format(
            rest[AXIS_X], limits[AXIS_X][0], limits[AXIS_X][1],
            rest[AXIS_Y], limits[AXIS_Y][0], limits[AXIS_Y][1]))

    motors = Motors(LEFT_PORT, RIGHT_PORT)
    sys.stderr.write(
        "motors: left {0}, right {1}   speed {2}%\n".format(
            LEFT_PORT, RIGHT_PORT, SPEED_SCALE))
    sys.stderr.write("driving. Ctrl-C to stop.\n")
    sys.stderr.flush()

    latest = dict(rest)
    left_duty = 0.0
    right_duty = 0.0
    stopping = {"now": False}

    def request_stop(signum, frame):
        stopping["now"] = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    try:
        next_tick = time.monotonic()
        while not stopping["now"]:
            timeout = max(0.0, next_tick - time.monotonic())
            ready, _, _ = select.select([fd], [], [], timeout)
            if ready:
                try:
                    data = os.read(fd, EVENT.size * 64)
                except OSError as exc:
                    if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK,
                                     errno.EINTR):
                        data = b""
                    else:
                        # ENODEV: the pad walked away. Stopping is the
                        # only safe response - the finally below does it.
                        sys.stderr.write(
                            "gamepad gone: {0}\n".format(exc))
                        break
                for offset in range(0, len(data) - EVENT.size + 1,
                                    EVENT.size):
                    fields = EVENT.unpack_from(data, offset)
                    if fields[2] == EV_ABS and fields[3] in latest:
                        latest[fields[3]] = fields[4]

            now = time.monotonic()
            if now < next_tick:
                continue
            next_tick = now + LOOP_PERIOD_S

            # Up is forward, and up drives ABS_Y toward its minimum on
            # this controller, so the vertical axis is negated. Measured,
            # not assumed - see the header.
            throttle = -axis_fraction(
                latest[AXIS_Y], rest[AXIS_Y], limits[AXIS_Y][0],
                limits[AXIS_Y][1])
            turn = axis_fraction(
                latest[AXIS_X], rest[AXIS_X], limits[AXIS_X][0],
                limits[AXIS_X][1])

            left_target, right_target = tank(throttle, turn)
            left_duty = slew(left_duty, left_target * SPEED_SCALE)
            right_duty = slew(right_duty, right_target * SPEED_SCALE)
            motors.drive(left_duty, right_duty)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        sys.stderr.write("fatal: {0}: {1}\n".format(
            type(exc).__name__, exc))
        return 1
    finally:
        # Unconditional, and first. Everything else is tidying.
        motors.stop_all()
        try:
            os.close(fd)
        except Exception:
            pass
        sys.stderr.write("stopped.\n")
        sys.stderr.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
