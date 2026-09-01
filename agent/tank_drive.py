#!/usr/bin/env python3
"""BRICK CODE. Tank drive from the DualShock 4's left stick.

Runs on the EV3 itself, on CPython 3.5 with the standard library and
nothing else. No computer is involved once it is started: the gamepad is
trusted, so it reconnects on its own, and this program reads it straight
from /dev/input. That is the whole point of spending the brick's one
Bluetooth radio on the pad.

    ssh robot@ev3dev.local python3 -u ~/tank_drive.py

or, with no computer present at all, from Brickman's File
Browser: /home/robot/tank_drive.py. Brickman's Back button
sends SIGTERM, which stops both motors on the way out.

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

# Top duty as a percentage, applied last to both sides equally. Hold L1
# for three seconds to step to the next one, wrapping round. The first
# entry is where every run starts, so it is the gentle one.
SPEED_LEVELS = (50, 75, 100)
SPEED_HOLD_S = 1.5

# Buttons, held down and read back with EVIOCGKEY on 2026-09-01 rather
# than taken from a table: L1 reported 310 and R1 reported 311.
BTN_L1 = 310
BTN_R1 = 311
EV_KEY = 0x01

# Per loop iteration. A stick slammed to full would otherwise hand the
# gearbox a step change; this spreads it over about 250 ms.
SLEW_PCT_PER_LOOP = 10

LOOP_PERIOD_S = 0.05          # 20 Hz

# What stop means. These motors ship on `coast`, which freewheels for
# about 0.48 s after the drive is cut - measured on this hardware and
# recorded in ROADMAP.md, where brake falls from 229 to 32 deg/s in
# 0.11 s instead. Coasting is why a wheel appeared to "run slow" when
# the stick was released or R1 was held: it was not being driven, it was
# still turning. Braking makes stopping look like stopping.
STOP_ACTION = "brake"
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
        show_message("NO GAMEPAD", "press PS on the controller, then "
                                   "start this again")
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
# The EV3 screen
#
# Through ev3dev2.display, which is the one thing on this brick built for
# it. **This is the single exception to the standard-library-only rule
# for agent/**, and it is a narrow one: ev3dev2 ships in the stock image
# at /usr/lib/python3/dist-packages/, so the rule's actual reason -
# nothing is installed on the brick and there is no pip to install it
# with - does not apply. See agent/README.md.
#
# Everything else was tried first and measured:
#
#   print() to the console works and the operator can read it, but the
#   console is 44x21 characters in a 4x6 pixel font, and enlarging it
#   needs setfont, which needs root.
#
#   Writing /dev/fb0 directly works over ssh while brickman is idle, and
#   loses to it otherwise. fbcon owns whichever console is displayed and
#   repaints over anything drawn underneath.
#
#   The documented fix is `chvt` to an unused console. That needs root,
#   sudo here wants a password, and unbinding fbcon
#   (/sys/class/vtconsole/vtcon1/bind) is root-only too.
#
# The import is guarded, so a brick without ev3dev2 falls back to
# printing rather than refusing to drive. Driving is the point; the
# screen is a convenience.
#
# Confirmed by the operator on 2026-09-01: launched from Brickman's File
# Browser, the screens are visible and readable. That could not be
# checked from a development machine, because the VT switch Brickman
# performs needs root.
# ---------------------------------------------------------------------

try:
    # `fonts`, plural. There is no ev3dev2.font, and the singular form
    # fails as an ImportError that a bare except would hide - which it
    # did, silently, until the exception was actually printed.
    from ev3dev2 import fonts as _fonts
    from ev3dev2.display import Display as _Display
    HAVE_DISPLAY = True
except Exception:
    HAVE_DISPLAY = False

# Bigger than the console's 4x6. Loaded once; load() is not free.
FONT_BIG = "luBS14"
FONT_HUGE = "luBS24"

_LCD = {"display": None, "big": None, "huge": None}


def _lcd():
    # type: () -> object
    """The Display, made on first use. None if there is none."""
    if not HAVE_DISPLAY:
        return None
    if _LCD["display"] is None:
        try:
            _LCD["display"] = _Display()
            _LCD["big"] = _fonts.load(FONT_BIG)
            try:
                _LCD["huge"] = _fonts.load(FONT_HUGE)
            except Exception:
                _LCD["huge"] = _LCD["big"]
        except Exception:
            return None
    return _LCD["display"]


def _bar(draw, x, y, width, height, percent):
    # type: (object, int, int, int, int, int) -> None
    """An outlined bar filled to `percent`. Read faster than digits."""
    draw.rectangle((x, y, x + width, y + height), outline="black")
    filled = int((width - 4) * max(0, min(100, percent)) / 100.0)
    if filled > 0:
        draw.rectangle((x + 2, y + 2, x + 2 + filled, y + height - 2),
                       fill="black")


# An EV3 pack is six AA cells. Anything outside this band is not a
# reading, it is a scale factor applied wrongly.
PLAUSIBLE_VOLTS = (1.0, 20.0)


def _volts(raw):
    # type: (str) -> float or None
    """Volts from a power_supply attribute, whatever scale it used.

    **This driver does not use one scale.** Read on 2026-09-01:
    voltage_now is 7829066 and is microvolts, while voltage_min_design
    and voltage_max_design are 60000000 and 84000000, which are 6.0 V
    and 8.4 V at ten times that scale. Dividing all three by a million
    put the range at 60-84 V, left the real 7.83 V below the bottom of
    it, and reported a healthy pack as flat at 0 percent.

    So the divisor is chosen by which one lands in a plausible band for
    six AA cells rather than assumed, and an implausible result comes
    back as None rather than as a number somebody might believe.
    """
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    for divisor in (1e6, 1e7, 1e3, 1.0):
        volts = value / divisor
        if PLAUSIBLE_VOLTS[0] <= volts <= PLAUSIBLE_VOLTS[1]:
            return volts
    return None


# ---------------------------------------------------------------------
# The status LEDs
#
# brickman repaints the whole framebuffer about once a second, measured
# on 2026-09-01: a frame drawn once was gone inside a second, and when a
# program is started from the File Browser brickman shows its own screen
# over the top. So the LCD cannot hold anything on its own.
#
# The LEDs can. They are the only output that survives without fighting
# for it, which makes them the right place for the one thing the driver
# needs to know at a glance: which speed level is selected.
# ---------------------------------------------------------------------

LED_GREEN = "/sys/class/leds/led0:green:brick-status/"
LED_RED = "/sys/class/leds/led0:red:brick-status/"

# Green, amber, red as the speed rises. Ordered to match SPEED_LEVELS.
LED_FOR_LEVEL = ((255, 0), (255, 255), (0, 255))


def _write_file(path, value):
    # type: (str, object) -> None
    try:
        with open(path, "w") as handle:
            handle.write(str(value))
    except Exception:
        pass


def set_speed_leds(index):
    # type: (int) -> None
    """Show the speed level on the brick's status LED.

    The trigger has to be cleared first: green ships on `default-on`,
    and a trigger overrides anything written to brightness.
    """
    green, red = LED_FOR_LEVEL[index % len(LED_FOR_LEVEL)]
    _write_file(LED_GREEN + "trigger", "none")
    _write_file(LED_RED + "trigger", "none")
    _write_file(LED_GREEN + "brightness", green)
    _write_file(LED_RED + "brightness", red)


def restore_leds():
    # type: () -> None
    """Put the brick's LED back the way brickman had it. Never raises."""
    _write_file(LED_RED + "brightness", 0)
    _write_file(LED_RED + "trigger", "none")
    _write_file(LED_GREEN + "trigger", "default-on")
    _write_file(LED_GREEN + "brightness", 255)


def read_battery():
    # type: () -> tuple
    """(brick volts, brick percent, pad percent, pad status).

    The brick reports microvolts and no capacity, so the percentage is
    computed against voltage_min_design and voltage_max_design, which
    read 6.0 V and 8.4 V on this hardware. The pad reports a capacity
    directly and no voltage. Anything unreadable comes back as None
    rather than a plausible number.
    """
    def read(path):
        try:
            with open(path) as handle:
                return handle.read().strip()
        except Exception:
            return None

    base = "/sys/class/power_supply/lego-ev3-battery/"
    volts = _volts(read(base + "voltage_now"))
    lo = _volts(read(base + "voltage_min_design"))
    hi = _volts(read(base + "voltage_max_design"))
    percent = None
    if volts is not None and lo is not None and hi is not None and hi > lo:
        percent = int(round((volts - lo) / (hi - lo) * 100))
        percent = max(0, min(100, percent))

    pad_percent = pad_status = None
    try:
        root = "/sys/class/power_supply/"
        for node in sorted(os.listdir(root)):
            if read(os.path.join(root, node, "scope")) != "Device":
                continue
            capacity = read(os.path.join(root, node, "capacity"))
            pad_status = read(os.path.join(root, node, "status"))
            if capacity is not None:
                pad_percent = int(capacity)
            break
    except Exception:
        pass
    return volts, percent, pad_percent, pad_status


def show_battery():
    # type: () -> None
    """Both batteries, drawn if there is a display and printed if not."""
    volts, percent, pad_percent, pad_status = read_battery()
    lcd = _lcd()
    if lcd is None:
        print("")
        print("  -- BATTERY --")
        print("  EV3  {0}".format(
            "no reading" if volts is None else "{0:.2f} V  {1}".format(
                volts, "?" if percent is None else
                "{0}%".format(percent))))
        print("  PAD  {0}".format(
            "not connected" if pad_percent is None else
            "{0}%  {1}".format(pad_percent, pad_status or "")))
        sys.stdout.flush()
        return
    try:
        lcd.clear()
        draw = lcd.draw
        big = _LCD["big"]
        draw.text((4, 0), "BATTERY", font=big)
        draw.line((4, 20, 173, 20), fill="black")
        if volts is None:
            draw.text((4, 26), "EV3  no reading", font=big)
        else:
            draw.text((4, 26), "EV3 {0:.2f}V  {1}".format(
                volts, "?" if percent is None else
                "{0}%".format(percent)), font=big)
            if percent is not None:
                _bar(draw, 4, 46, 169, 14, percent)
        if pad_percent is None:
            draw.text((4, 66), "PAD  not conn.", font=big)
        else:
            draw.text((4, 66), "PAD {0}%".format(pad_percent), font=big)
            _bar(draw, 4, 86, 169, 14, pad_percent)
            if pad_status:
                draw.text((4, 106), pad_status, font=big)
        lcd.update()
    except Exception as exc:
        sys.stderr.write("display failed: {0}\n".format(exc))
        sys.stderr.flush()


def show_ready(speed, port_left, port_right):
    # type: (int, str, str) -> None
    """What the operator sees when there is no terminal to print to."""
    volts, percent, pad_percent, _ = read_battery()
    lcd = _lcd()
    if lcd is None:
        print("")
        print("  TANK READY")
        print("  speed {0}%   {1}/{2}".format(speed, port_left, port_right))
        if volts is not None:
            print("  EV3 {0:.1f}V {1}%".format(
                volts, "?" if percent is None else percent))
        print("  L1 hold = speed   R1 = battery")
        sys.stdout.flush()
        return
    try:
        lcd.clear()
        draw = lcd.draw
        big = _LCD["big"]
        draw.text((4, 0), "TANK READY", font=big)
        draw.line((4, 20, 173, 20), fill="black")
        draw.text((4, 26), "SPEED {0}%".format(speed), font=big)
        draw.text((4, 46), "{0} / {1}".format(port_left, port_right),
                  font=big)
        if volts is not None:
            draw.text((4, 66), "EV3 {0:.1f}V {1}%".format(
                volts, "?" if percent is None else percent), font=big)
        if pad_percent is not None:
            draw.text((4, 86), "PAD {0}%".format(pad_percent), font=big)
        draw.text((4, 108), "L1=SPEED  R1=BATTERY", font=big)
        lcd.update()
    except Exception as exc:
        sys.stderr.write("display failed: {0}\n".format(exc))
        sys.stderr.flush()


def show_message(title, detail=""):
    # type: (str, str) -> None
    """A failure the operator can act on, when stderr reaches nobody."""
    lcd = _lcd()
    if lcd is None:
        print("")
        print("  " + title)
        if detail:
            print("  " + detail)
        sys.stdout.flush()
        return
    try:
        lcd.clear()
        draw = lcd.draw
        big = _LCD["big"]
        draw.text((4, 0), title, font=big)
        draw.line((4, 20, 173, 20), fill="black")
        y = 28
        words = detail.split()
        line = ""
        while words and y < 112:
            candidate = (line + " " + words[0]).strip()
            if len(candidate) > 22:
                draw.text((4, y), line, font=big)
                y += 18
                line = ""
            else:
                line = candidate
                words.pop(0)
        if line and y < 112:
            draw.text((4, y), line, font=big)
        lcd.update()
    except Exception as exc:
        sys.stderr.write("display failed: {0}\n".format(exc))
        sys.stderr.flush()


def show_speed(speed):
    # type: (int) -> None
    """The new speed level, as large as the screen allows."""
    lcd = _lcd()
    if lcd is None:
        print("  speed {0}%".format(speed))
        sys.stdout.flush()
        return
    try:
        lcd.clear()
        draw = lcd.draw
        draw.text((4, 4), "SPEED", font=_LCD["big"])
        draw.text((30, 30), "{0}%".format(speed), font=_LCD["huge"])
        _bar(draw, 4, 96, 169, 20, speed)
        lcd.update()
    except Exception as exc:
        sys.stderr.write("display failed: {0}\n".format(exc))
        sys.stderr.flush()


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
                found = ", ".join(self._addresses()) or "none"
                show_message("NO MOTOR", "wanted {0}. found: {1}".format(
                    port, found))
                raise SystemExit(
                    "no motor at {0}. Ports with a motor: {1}".format(
                        port, found))
            self.paths[side] = node
            self.duty[side] = 0.0
        self._handles = {}
        self.stop_action = {}
        for side, node in self.paths.items():
            # Before run-direct, so the mode is settled before anything
            # can be commanded.
            previous = self._read(node, "stop_action")
            self._write(node, "stop_action", STOP_ACTION)
            self.stop_action[side] = (
                previous, self._read(node, "stop_action"))
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
    def _read(node, name):
        # type: (str, str) -> str or None
        try:
            with open(os.path.join(node, name)) as handle:
                return handle.read().strip()
        except Exception:
            return None

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
    level = 0
    sys.stderr.write(
        "motors: left {0}, right {1}   speed {2}%\n".format(
            LEFT_PORT, RIGHT_PORT, SPEED_LEVELS[level]))
    for side in sorted(motors.stop_action):
        was, now_action = motors.stop_action[side]
        sys.stderr.write("  {0}: stop_action {1} -> {2}\n".format(
            side, was, now_action))
    sys.stderr.write(
        "hold L1 {0:.1f}s to change speed, press R1 for battery. "
        "Ctrl-C to stop.\n".format(SPEED_HOLD_S))
    sys.stderr.flush()
    set_speed_leds(level)
    show_ready(SPEED_LEVELS[level], LEFT_PORT, RIGHT_PORT)

    latest = dict(rest)
    left_duty = 0.0
    right_duty = 0.0
    stopping = {"now": False}
    l1_down_at = None
    l1_fired = False

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
                    kind, code, value = fields[2], fields[3], fields[4]
                    if kind == EV_ABS and code in latest:
                        latest[code] = value
                    elif kind == EV_KEY and code == BTN_L1:
                        if value == 1:
                            l1_down_at = time.monotonic()
                            l1_fired = False
                        elif value == 0:
                            l1_down_at = None
                    elif (kind == EV_KEY and code == BTN_R1
                          and value == 1):
                        # A press, not a hold. Printing four lines costs
                        # nothing, so there is no reason to make the
                        # operator hold the button or to stop the robot
                        # while they read.
                        show_battery()

            now = time.monotonic()
            if now < next_tick:
                continue
            next_tick = now + LOOP_PERIOD_S

            # One step per hold, not one per second: the latch clears
            # only when L1 is released, so leaning on it does not run
            # through every level.
            if (l1_down_at is not None and not l1_fired
                    and now - l1_down_at >= SPEED_HOLD_S):
                level = (level + 1) % len(SPEED_LEVELS)
                l1_fired = True
                sys.stderr.write(
                    "speed {0}%\n".format(SPEED_LEVELS[level]))
                sys.stderr.flush()
                set_speed_leds(level)
                show_speed(SPEED_LEVELS[level])

            # Up is forward, and up drives ABS_Y toward its minimum on
            # this controller, so the vertical axis is negated. Measured,
            # not assumed - see the header.
            throttle = -axis_fraction(
                latest[AXIS_Y], rest[AXIS_Y], limits[AXIS_Y][0],
                limits[AXIS_Y][1])
            turn = axis_fraction(
                latest[AXIS_X], rest[AXIS_X], limits[AXIS_X][0],
                limits[AXIS_X][1])

            # The level scales the target, before the slew limiter, so
            # that changing speed while driving ramps rather than jumps.
            scale = SPEED_LEVELS[level]
            left_target, right_target = tank(throttle, turn)
            left_duty = slew(left_duty, left_target * scale)
            right_duty = slew(right_duty, right_target * scale)
            motors.drive(left_duty, right_duty)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        sys.stderr.write("fatal: {0}: {1}\n".format(
            type(exc).__name__, exc))
        show_message("STOPPED", "{0}: {1}".format(
            type(exc).__name__, exc))
        return 1
    finally:
        # Unconditional, and first. Everything else is tidying.
        motors.stop_all()
        restore_leds()
        try:
            os.close(fd)
        except Exception:
            pass
        sys.stderr.write("stopped.\n")
        sys.stderr.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
