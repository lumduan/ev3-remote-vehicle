#!/usr/bin/env python3
"""BRICK CODE. The gamepad's buttons, as the brick's own buttons.

Runs on the EV3 itself, on CPython 3.5 with the standard library and
nothing else. The gamepad then drives the brick: the D-pad moves the
highlight, Cross selects, and Share goes back - which is also what stops
a running program and, held, reaches the shutdown menu.

Two ways in, and they want opposite things of it:

    /home/robot/pad_buttons/pad_buttons.py

from Brickman's File Browser. It detaches, so the menu comes straight
back, and starting it a second time stops it.

    pad_buttons.py --foreground

from systemd, which wants the process to stay in the foreground and
would call the service dead the moment a double fork returned. The unit
is `agent/pad-buttons.service`, installed into the operator's own
`~/.config/systemd/user/`.

Autostart at boot needs `Linger=yes` for the robot user, and that is the
one thing here that needs root: `sudo loginctl enable-linger robot`,
once, ever. Measured on this brick on 2026-09-02 - there is no cron, no
/etc/rc.local, /etc/systemd/system is root-only, and the systemd user
instance does not start until someone logs in. `ev3ctl setup` prints
that command for the operator to run; nothing in this project runs sudo
itself.

It works by writing input events into the brick's own button device.
Writing to an evdev node is not a trick: `evdev_write` in the kernel
calls `input_inject_event`, so an event written there arrives exactly as
if the button had been pressed. Verified on this brick on 2026-09-02 -
the events read back out of the device, and Brickman redrew its menu in
response, changing 445 blocks of framebuffer against 0 for a screen left
alone.

Deliberately imports nothing from any other file here, as
battery_report.py and tank_drive.py do, so it still works when nothing
else does.

**It touches no motors, and must never grow a reason to.** The motor
rules in CLAUDE.md are scoped to code that commands a motor and do not
apply. What does apply is the shape they are written in, because this
program has its own latch: **a key injected as pressed stays pressed
until something releases it.** A stuck KEY_BACKSPACE walks the brick
into its shutdown menu on its own. Every path out of here releases
every key it is holding.

One interaction worth knowing. Brickman's Back button sends SIGTERM to
whatever it launched, so an injected Share is an injected SIGTERM to a
running program - that is the point of it, and it is also why Share
should not be leaned on absent-mindedly while the robot is driving.
"""

import errno
import os
import re
import select
import signal
import struct
import sys
import time

# --- measured on this hardware, 2026-09-02 ---------------------------
#
# Gamepad buttons, each held down and read back with EVIOCGKEY rather
# than taken from a table. Cross reported 304 and Share reported 314,
# the same way L1 reported 310 and R1 reported 311 for tank_drive.py.
BTN_CROSS = 304
BTN_SHARE = 314

# The D-pad is a hat, not four buttons. `B: ABS=3003f` on this pad
# declares ABS_HAT0X and ABS_HAT0Y, each reporting -1, 0 or +1, so the
# four directions are two axes and a sign. Code that listens for four
# EV_KEY codes records nothing at all when the D-pad is pressed.
ABS_HAT0X = 16
ABS_HAT0Y = 17

# The brick's own buttons, decoded from its `B: KEY=1680 0 0 10004000`
# on 2026-09-02: bits 14, 28, 103, 105, 106, 108. The names come from
# input-event-codes.h at v4.14, the kernel series this brick runs.
#
# Bit 14 is worth a line of its own. An earlier fixture in this
# repository had this mask as `...10000000`, without it, which would
# have meant the brick had no Back button at all. That fixture was
# invented rather than read. This one was read.
KEY_BACKSPACE = 14
KEY_ENTER = 28
KEY_UP = 103
KEY_LEFT = 105
KEY_RIGHT = 106
KEY_DOWN = 108

# The whole mapping, in one place.
BUTTON_MAP = {
    BTN_CROSS: KEY_ENTER,
    BTN_SHARE: KEY_BACKSPACE,
}

# Hat axis -> (key for a negative value, key for a positive value).
HAT_MAP = {
    ABS_HAT0X: (KEY_LEFT, KEY_RIGHT),
    ABS_HAT0Y: (KEY_UP, KEY_DOWN),
}

# A click when a button registers, so the operator knows it landed
# without looking at the brick. The speaker is an input device too:
# `B: EV=40001` has bit 18, EV_SND, and `B: SND=6` has SND_BELL and
# SND_TONE - read off the brick on 2026-09-02. So it is written the same
# way the buttons are, with no aplay and no beep binary.
#
# Three pitches, so a press can be told apart by ear: a click for the
# arrows, something higher for select, something lower for back.
EV_SND = 0x12
SND_TONE = 0x02

CLICK = {
    KEY_UP: (1200, 12),
    KEY_DOWN: (1200, 12),
    KEY_LEFT: (1200, 12),
    KEY_RIGHT: (1200, 12),
    KEY_ENTER: (1800, 25),
    KEY_BACKSPACE: (700, 30),
}

PAD_NAME = "Wireless Controller"
BUTTONS_NAME = "EV3 Brick Buttons"
SPEAKER_NAME = "LEGO MINDSTORMS EV3 Speaker"
INPUT_DEVICES = "/proc/bus/input/devices"
INPUT_DIR = "/dev/input"

HERE = os.path.dirname(os.path.abspath(__file__))
PIDFILE = os.path.join(HERE, "pad_buttons.pid")
PROGRAM = os.path.basename(os.path.abspath(__file__))
# A seam, so the identity check below can be tested on a host
# that has no /proc. Never reassigned on the brick.
PROC = "/proc"
LOGFILE = os.path.join(HERE, "pad_buttons.log")

# struct input_event: struct timeval (two 32-bit longs on this kernel),
# then __u16 type, __u16 code, __s32 value. Sixteen bytes. The native
# "@llHHi" is 24 on a 64-bit machine, and mixing the two does not raise,
# it just produces nonsense.
EVENT = struct.Struct("=llHHi")
EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03
SYN_REPORT = 0

# How long to wait between looks for a pad that has gone away. The pad
# sleeps on its own and reconnects when PS is pressed, so this program
# is started once per boot rather than once per nap.
RECONNECT_POLL_S = 2.0

# Bounded so that a SIGTERM is noticed promptly even on an idle pad.
SELECT_TIMEOUT_S = 0.5


# ---------------------------------------------------------------------
# Finding the two devices
#
# Both by exact name, never by node number. `event1` and `event4` are
# observations from one session; the numbering is whatever order the
# kernel bound things in and it changes between runs.
# ---------------------------------------------------------------------

_EVENT_NODE = re.compile(r"^event\d+$")


def _blocks():
    # type: () -> list
    """Every device in /proc/bus/input/devices, as dicts."""
    try:
        with open(INPUT_DEVICES, "r") as handle:
            text = handle.read()
    except Exception:
        return []
    found = []
    current = {"name": None, "key": None, "handlers": []}
    for line in text.splitlines() + [""]:
        line = line.strip()
        if not line:
            if current["name"] is not None:
                found.append(current)
            current = {"name": None, "key": None, "handlers": []}
            continue
        if line.startswith("N: Name="):
            current["name"] = line[8:].strip().strip('"')
        elif line.startswith("H: Handlers="):
            current["handlers"] = line[12:].split()
        elif line.startswith("B: KEY="):
            current["key"] = line[7:].strip()
    return found


def _node_of(block):
    # type: (dict) -> str or None
    for handler in block["handlers"]:
        if _EVENT_NODE.match(handler):
            return os.path.join(INPUT_DIR, handler)
    return None


def _has_bit(mask, bit):
    # type: (str, int) -> bool
    """Whether one bit is set in a `B: KEY=` bitmask.

    The kernel prints these as hex longs, most significant word first,
    with %lx and no zero padding - so a word can be shorter than its
    full width and position is what counts, never text length. Words are
    32 bits on this brick.
    """
    if not mask:
        return False
    words = mask.split()
    index = bit // 32
    if index >= len(words):
        return False
    try:
        return bool((int(words[len(words) - 1 - index], 16) >>
                     (bit % 32)) & 1)
    except ValueError:
        return False


def find_pad():
    # type: () -> str or None
    """The gamepad's event node, or None if it is not connected.

    hid-sony makes three devices for one controller, all with the same
    name prefix, so the gamepad function is picked out by the one
    capability its touchpad and motion siblings lack: BTN_SOUTH.
    """
    for block in _blocks():
        if block["name"] != PAD_NAME:
            continue
        if not _has_bit(block["key"], BTN_CROSS):
            continue
        node = _node_of(block)
        if node is not None:
            return node
    return None


def find_speaker():
    # type: () -> str or None
    """The brick's speaker, or None. Sound is a convenience, not a need."""
    for block in _blocks():
        if block["name"] == SPEAKER_NAME:
            return _node_of(block)
    return None


def find_buttons():
    # type: () -> str
    """The brick's own button device. Raises if it is not there."""
    for block in _blocks():
        if block["name"] != BUTTONS_NAME:
            continue
        node = _node_of(block)
        if node is not None:
            return node
    raise SystemExit(
        "no device named {0!r} in {1}".format(BUTTONS_NAME, INPUT_DEVICES))


# ---------------------------------------------------------------------
# Injection
# ---------------------------------------------------------------------

class Speaker(object):
    """Short tones, so a press is felt as well as seen.

    A tone latches exactly as a key does: written on, it stays on until
    something writes it off. A program killed between the two leaves the
    brick humming, so `silence` is called from the same teardown that
    releases the keys.
    """

    def __init__(self, path):
        # type: (str) -> None
        self.fd = -1
        if path is None:
            return
        try:
            self.fd = os.open(path, os.O_WRONLY)
        except OSError:
            self.fd = -1

    def _write(self, hz):
        # type: (int) -> None
        try:
            os.write(self.fd, EVENT.pack(0, 0, EV_SND, SND_TONE, hz))
            os.write(self.fd, EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0))
        except Exception:
            pass

    def click(self, key):
        # type: (int) -> None
        """One short tone for one brick key. Silent if there is no note."""
        if self.fd < 0:
            return
        note = CLICK.get(key)
        if note is None:
            return
        hz, ms = note
        self._write(hz)
        time.sleep(ms / 1000.0)
        self._write(0)

    def silence(self):
        # type: () -> None
        """Stop any tone. Never raises, however it is reached."""
        if self.fd >= 0:
            self._write(0)

    def close(self):
        # type: () -> None
        self.silence()
        if self.fd >= 0:
            try:
                os.close(self.fd)
            except Exception:
                pass
            self.fd = -1


class Keys(object):
    """The brick's buttons, pressed and released from software.

    Holds the set of keys it has pressed, so that every one of them can
    be released on the way out. That is this program's version of the
    motor rule: a key left down is a button held down forever, and the
    one that matters is Back, which walks the brick into shutdown.
    """

    def __init__(self, path, speaker=None):
        # type: (str, Speaker) -> None
        self.path = path
        self.fd = os.open(path, os.O_WRONLY)
        self.held = set()
        self.speaker = speaker

    def send(self, code, value):
        # type: (int, int) -> None
        """One key event, followed by the SYN a real driver would send."""
        try:
            os.write(self.fd, EVENT.pack(0, 0, EV_KEY, code, value))
            os.write(self.fd, EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0))
        except Exception as exc:
            sys.stderr.write("inject failed: {0}\n".format(exc))
            sys.stderr.flush()
            return
        if value:
            self.held.add(code)
            if self.speaker is not None:
                # On the press only. A click on the release as well
                # would double every keystroke.
                self.speaker.click(code)
        else:
            self.held.discard(code)

    def release_all(self):
        # type: () -> None
        """Let go of everything. Never raises, however it is reached."""
        for code in sorted(self.held):
            try:
                os.write(self.fd, EVENT.pack(0, 0, EV_KEY, code, 0))
                os.write(self.fd, EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0))
            except Exception:
                pass
        self.held.clear()

    def close(self):
        # type: () -> None
        self.release_all()
        try:
            os.close(self.fd)
        except Exception:
            pass


# ---------------------------------------------------------------------
# Running in the background
# ---------------------------------------------------------------------

def _is_this_program(pid):
    # type: (int) -> bool
    """Whether /proc says that pid is running this program."""
    try:
        path = os.path.join(PROC, str(pid), "cmdline")
        with open(path, "rb") as handle:
            cmdline = handle.read()
    except Exception:
        # No readable /proc entry means we cannot identify it. Refusing
        # to signal a process we cannot name is the safe direction.
        return False
    return PROGRAM.encode() in cmdline


def running_pid():
    # type: () -> int or None
    """The pid in the pidfile, if it is alive and is this program.

    Liveness alone is not enough. The pidfile outlives a reboot and pids
    are reused, so a stale one can name an unrelated process that
    happened to be given the same number. Signalling that would be a
    real fault rather than a cosmetic one: at boot the numbers within
    reach belong to system daemons, and the service starts at boot.
    """
    try:
        with open(PIDFILE) as handle:
            pid = int(handle.read().strip())
    except Exception:
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    if not _is_this_program(pid):
        return None
    return pid


def stop_running(pid):
    # type: (int) -> None
    """Ask an already-running copy to stop, and wait for it to."""
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        print("could not signal {0}: {1}".format(pid, exc))
        return
    for _ in range(40):
        time.sleep(0.1)
        try:
            os.kill(pid, 0)
        except OSError:
            print("stopped {0}".format(pid))
            return
    print("{0} did not stop".format(pid))


def daemonise():
    # type: () -> None
    """Detach, so Brickman's menu comes back while this keeps running.

    The usual double fork. The second one matters here: after setsid
    this process leads a new session, and forking again means it is not
    a session leader, so it can never acquire a controlling terminal -
    which is what would otherwise tie it back to the console Brickman
    launched it on.
    """
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    sys.stdout.flush()
    sys.stderr.flush()
    null = os.open(os.devnull, os.O_RDONLY)
    log = os.open(LOGFILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(null, 0)
    os.dup2(log, 1)
    os.dup2(log, 2)
    if null > 2:
        os.close(null)
    if log > 2:
        os.close(log)


# ---------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------

def pump(pad_fd, keys, hats):
    # type: (int, Keys, dict) -> bool
    """Read what the pad has to say and inject it. False when it goes.

    A hat reports a position, not a press, so a change has to be turned
    into a release of the old direction and a press of the new one. The
    brick's buttons only understand presses.
    """
    try:
        data = os.read(pad_fd, EVENT.size * 64)
    except OSError as exc:
        if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR):
            return True
        # ENODEV: the pad walked away. Not an error, just a nap.
        sys.stderr.write("pad gone: {0}\n".format(exc))
        sys.stderr.flush()
        return False
    if not data:
        return True

    for offset in range(0, len(data) - EVENT.size + 1, EVENT.size):
        fields = EVENT.unpack_from(data, offset)
        kind, code, value = fields[2], fields[3], fields[4]
        if kind == EV_KEY and code in BUTTON_MAP:
            keys.send(BUTTON_MAP[code], 1 if value else 0)
        elif kind == EV_ABS and code in HAT_MAP:
            previous = hats.get(code, 0)
            if value == previous:
                continue
            negative, positive = HAT_MAP[code]
            if previous < 0:
                keys.send(negative, 0)
            elif previous > 0:
                keys.send(positive, 0)
            if value < 0:
                keys.send(negative, 1)
            elif value > 0:
                keys.send(positive, 1)
            hats[code] = value
    return True


def main():
    # type: () -> int
    # --foreground for systemd, which needs the process to stay put.
    # Anything else is a File Browser launch, which wants the opposite.
    foreground = "--foreground" in sys.argv[1:]

    existing = running_pid()
    if existing is not None and not foreground:
        print("pad_buttons was running as {0}; stopping it".format(
            existing))
        stop_running(existing)
        try:
            os.unlink(PIDFILE)
        except OSError:
            pass
        return 0
    if existing is not None:
        # --foreground takes over rather than toggling. systemd starting
        # this must never mean "stop the copy that is already running
        # and exit 0" - that exits cleanly, so Restart=on-failure would
        # not bring it back, and the service would sit dead.
        print("taking over from {0}".format(existing))
        stop_running(existing)

    buttons_path = find_buttons()
    print("brick buttons: {0}".format(buttons_path))
    if find_pad() is None:
        print("no gamepad yet - press PS; this will wait for it")
    if foreground:
        print("gamepad buttons -> brick buttons. Ctrl-C to stop.")
    else:
        print("gamepad buttons -> brick buttons. Start again to stop.")
    sys.stdout.flush()

    if not foreground:
        daemonise()

    with open(PIDFILE, "w") as handle:
        handle.write(str(os.getpid()))

    sys.stderr.write("started {0} at {1}\n".format(
        os.getpid(), time.strftime("%Y-%m-%d %H:%M:%S")))
    sys.stderr.flush()

    speaker = Speaker(find_speaker())
    keys = Keys(buttons_path, speaker)
    stopping = {"now": False}

    def request_stop(signum, frame):
        stopping["now"] = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    pad_fd = -1
    hats = {}
    next_look = 0.0
    try:
        while not stopping["now"]:
            if pad_fd < 0:
                now = time.monotonic()
                if now < next_look:
                    time.sleep(min(0.2, next_look - now))
                    continue
                next_look = now + RECONNECT_POLL_S
                node = find_pad()
                if node is None:
                    continue
                try:
                    pad_fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
                except OSError as exc:
                    # Keep retrying, and never bound this. EACCES here
                    # is normal and transient: udev creates the node,
                    # then applies the rule that puts it in the input
                    # group, and we can arrive in between. Observed on
                    # a real boot 2026-09-02 - two Permission denied
                    # two seconds apart, then success on the third
                    # look. A retry limit would turn a 4 second wait
                    # into "the pad does not work today".
                    sys.stderr.write("cannot open {0}: {1}\n".format(
                        node, exc))
                    sys.stderr.flush()
                    continue
                hats = {}
                sys.stderr.write("pad connected: {0}\n".format(node))
                sys.stderr.flush()
                continue

            ready, _, _ = select.select([pad_fd], [], [],
                                        SELECT_TIMEOUT_S)
            if not ready:
                continue
            if not pump(pad_fd, keys, hats):
                # Let go of whatever was down when it vanished, or the
                # brick keeps that button pressed with nothing holding it.
                keys.release_all()
                try:
                    os.close(pad_fd)
                except OSError:
                    pass
                pad_fd = -1
                hats = {}
                next_look = time.monotonic() + RECONNECT_POLL_S
    except Exception as exc:
        sys.stderr.write("fatal: {0}: {1}\n".format(
            type(exc).__name__, exc))
        sys.stderr.flush()
        return 1
    finally:
        # Unconditional, and first. A key left pressed is this program's
        # latched motor, and the one that matters is Back. A tone left
        # on is the same hazard with a different symptom.
        keys.close()
        speaker.close()
        if pad_fd >= 0:
            try:
                os.close(pad_fd)
            except OSError:
                pass
        try:
            os.unlink(PIDFILE)
        except OSError:
            pass
        sys.stderr.write("stopped.\n")
        sys.stderr.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
