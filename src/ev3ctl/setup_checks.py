"""HOST CODE. What `ev3ctl setup` checks, and how it installs.

Split from the wizard so the decisions can be tested without a brick, a
terminal or a child. Nothing here draws anything; `cli/setup.py` turns
these verdicts into screens.

The check list is deliberately the same list, in the same order, as
`DEFAULT_CHECKLIST` in errors.py - which was already written as
"cheapest and most likely fix first" and is the only place in this
repository that records the Brickman menu path. A beginner who meets a
failure here should meet the same wording later if `scan` or `drive`
fails, rather than two different accounts of one problem.
"""

import hashlib
import os
import subprocess

# The two programs that make the robot usable, and where each lives on
# the brick. /home/robot rather than /tmp: /tmp does not survive a
# reboot, and Brickman's File Browser is where a child will look.
#
# They are in separate folders on purpose. tank_drive is started for a
# session and then stopped; pad_buttons is meant to be running all the
# time, so it is not part of "the driving stuff" and does not sit with
# it. Its pidfile and log land beside it, because the program derives
# both from its own location.
DRIVE_DIR = "/home/robot/tanks_1"
BUTTONS_DIR = "/home/robot/pad_buttons"

PROGRAMS = {
    "tank_drive.py": DRIVE_DIR,
    "pad_buttons.py": BUTTONS_DIR,
}

# Where pad_buttons used to live, so an install can clear it away rather
# than leave a second copy that Brickman will happily run.
OLD_LOCATIONS = (
    "/home/robot/tanks_1/pad_buttons.py",
    "/home/robot/tanks_1/pad_buttons.pid",
    "/home/robot/tanks_1/pad_buttons.log",
    "/home/robot/pad_buttons.py",
    "/home/robot/tank_drive.py",
)

# The systemd user unit. A user unit, not a system one: it runs as
# robot, who is already in the `input` group, and needs no more.
UNIT_NAME = "pad-buttons.service"
UNIT_DIR = "/home/robot/.config/systemd/user"
LINGER_COMMAND = "sudo loginctl enable-linger robot"

# Verdicts. `ok` is fine, `bad` needs the operator to do something,
# `unknown` means it has not been looked at yet.
OK = "ok"
BAD = "bad"
UNKNOWN = "unknown"

SSH_TIMEOUT_S = 20


def agent_dir():
    """The repository's `agent/` directory, found by walking up.

    The same trick `link.py:find_agent_source` uses, and for the same
    reason: `agent/` is deliberately not packaged, so it is found on
    disk rather than through importlib.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(os.path.dirname(here)), "agent")


def brick_path(name):
    """Where one program belongs on the brick."""
    return os.path.join(PROGRAMS[name], name)


def local_digest(name):
    """The md5 of a file in `agent/`, or None if it is not there."""
    path = os.path.join(agent_dir(), name)
    try:
        with open(path, "rb") as handle:
            return hashlib.md5(handle.read()).hexdigest()
    except Exception:
        return None


def _ssh(host, command, timeout=SSH_TIMEOUT_S, data=None):
    """Run one command on the brick. Returns (ok, output).

    BatchMode so a missing key fails immediately instead of stopping to
    ask for a password underneath a full-screen menu.
    """
    argv = ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
            host, command]
    try:
        done = subprocess.run(
            argv, input=data, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "timed out"
    except OSError as exc:
        return False, str(exc)
    output = done.stdout.decode("utf-8", "replace").strip()
    if done.returncode != 0:
        return False, (done.stderr.decode("utf-8", "replace").strip()
                       or output or "exit {0}".format(done.returncode))
    return True, output


def check_link(host):
    """Can we reach the brick at all?

    `ssh ... true`, not ping. On this setup mDNS hands back an IPv4
    address that does not work alongside an IPv6 one that does, so plain
    ping reports total loss for a brick that is answering perfectly -
    the reason is recorded beside DEFAULT_CHECKLIST in errors.py.
    """
    ok, detail = _ssh(host, "true", timeout=15)
    return (OK if ok else BAD), detail


def check_motors(host):
    """At least two motors, found by reading each one's address."""
    ok, out = _ssh(
        host,
        "cat /sys/class/tacho-motor/*/address 2>/dev/null | wc -l")
    if not ok:
        return BAD, detail_or(out, "cannot ask the robot")
    try:
        count = int(out.strip() or "0")
    except ValueError:
        count = 0
    return (OK if count >= 2 else BAD), "{0} found".format(count)


def check_gamepad(host):
    """Is the controller connected right now?

    By exact name in /proc/bus/input/devices. hid-sony makes three
    devices for one controller, so the count is of exact matches.
    """
    ok, out = _ssh(
        host,
        "grep -c '^N: Name=\"Wireless Controller\"$' "
        "/proc/bus/input/devices 2>/dev/null || true")
    if not ok:
        return BAD, detail_or(out, "cannot ask the robot")
    try:
        count = int(out.strip() or "0")
    except ValueError:
        count = 0
    return (OK if count >= 1 else BAD), "{0} connected".format(count)


def check_programs(host):
    """Are both programs on the brick, and are they the current ones?

    Compared by md5 rather than by existence, because an old copy is a
    subtler problem than a missing one: it runs, and behaves like the
    version it was rather than the version you have.

    Returns (verdict, state, detail) where state is one of
    "ready", "old", "missing".
    """
    wanted = {}
    for name in PROGRAMS:
        digest = local_digest(name)
        if digest is None:
            return BAD, "missing", "{0} is not in agent/".format(name)
        wanted[name] = digest

    ok, out = _ssh(
        host,
        "md5sum {0} 2>/dev/null || true".format(
            " ".join(brick_path(n) for n in sorted(PROGRAMS))))
    if not ok:
        return BAD, "missing", detail_or(out, "cannot ask the robot")

    found = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            found[os.path.basename(parts[1])] = parts[0]

    missing = [n for n in sorted(PROGRAMS) if n not in found]
    if missing:
        return BAD, "missing", ", ".join(missing)
    stale = [n for n in sorted(PROGRAMS) if found[n] != wanted[n]]
    if stale:
        return BAD, "old", ", ".join(stale)
    return OK, "ready", "both up to date"


def check_autostart(host):
    """Will the buttons start by themselves at boot?

    Three things have to be true, and they fail in a useful order: the
    unit has to be installed, the robot user has to be lingering - the
    one that needs root, and so the one worth naming separately - and
    the unit has to be enabled and running.

    Every probe is asked to label its own answer. Substring matching
    would be wrong twice over here: "disabled" contains "enabled", and
    "inactive" contains "active", so a stopped, disabled service would
    report itself as running and enabled.

    Returns (verdict, state, detail) where state is one of
    "on", "needs_root", "off".
    """
    ok, out = _ssh(
        host,
        "echo unit=$(test -f {0}/{1} && echo yes || echo no); "
        "echo enabled=$(systemctl --user is-enabled {1} 2>/dev/null "
        "|| echo unknown); "
        "echo linger=$(loginctl show-user robot 2>/dev/null "
        "| sed -n 's/^Linger=//p' || echo unknown); "
        "echo active=$(systemctl --user is-active {1} 2>/dev/null "
        "|| echo unknown)".format(UNIT_DIR, UNIT_NAME))
    if not ok:
        return BAD, "off", detail_or(out, "cannot ask the robot")

    fields = {}
    for line in out.splitlines():
        key, _, value = line.strip().partition("=")
        fields[key] = value.strip().lower()

    if fields.get("unit") != "yes":
        return BAD, "off", "the service is not installed"
    if fields.get("linger") != "yes":
        return BAD, "needs_root", LINGER_COMMAND
    if fields.get("enabled") != "enabled":
        return BAD, "off", "installed but not enabled"
    if fields.get("active") != "active":
        return BAD, "off", "enabled but not running"
    return OK, "on", "running, and will start itself at boot"


def install(host):
    """Copy both programs, install the service, tidy old copies.

    Returns (ok, detail, notes) - notes being the things worth saying
    out loud, like a file that was moved out from under the operator.
    """
    notes = []

    for name, directory in sorted(PROGRAMS.items()):
        ok, detail = _ssh(host, "mkdir -p " + directory)
        if not ok:
            return False, detail, notes

    for name in sorted(PROGRAMS):
        path = os.path.join(agent_dir(), name)
        try:
            with open(path, "rb") as handle:
                source = handle.read()
        except OSError as exc:
            return False, str(exc), notes
        target = brick_path(name)
        ok, detail = _ssh(
            host, "cat > {0} && chmod +x {0}".format(target),
            timeout=60, data=source)
        if not ok:
            return False, "{0}: {1}".format(name, detail), notes

    # A copy left where it used to live is not harmless: Brickman lists
    # it and will happily run a second, older one.
    for stale in OLD_LOCATIONS:
        if stale in [brick_path(n) for n in PROGRAMS]:
            continue
        ok, out = _ssh(
            host, "test -f {0} && rm -f {0} && echo removed || "
                  "true".format(stale))
        if ok and "removed" in out:
            notes.append(stale)

    ok, detail = install_service(host)
    if not ok:
        return False, detail, notes

    # Read it back. A copy that reported success and landed truncated
    # would otherwise be discovered by a child, on the floor, later.
    verdict, state, detail = check_programs(host)
    if verdict != OK:
        return False, "copied but {0}: {1}".format(state, detail), notes
    return True, detail, notes


def install_service(host):
    """Put the systemd user unit in place and enable it.

    Enabling works without lingering - it only writes a symlink - so
    this is worth doing before the operator has run the one root
    command. It simply will not start at boot until they have.
    """
    path = os.path.join(agent_dir(), UNIT_NAME)
    try:
        with open(path, "rb") as handle:
            unit = handle.read()
    except OSError as exc:
        return False, str(exc)

    ok, detail = _ssh(host, "mkdir -p " + UNIT_DIR)
    if not ok:
        return False, detail
    ok, detail = _ssh(
        host, "cat > {0}/{1}".format(UNIT_DIR, UNIT_NAME),
        timeout=30, data=unit)
    if not ok:
        return False, detail
    # Failures here are not fatal: the file is in place, and the wizard
    # reports the state afterwards rather than trusting this to work.
    _ssh(host, "systemctl --user daemon-reload 2>/dev/null || true")
    _ssh(host, "systemctl --user enable {0} 2>/dev/null || true".format(
        UNIT_NAME))
    _ssh(host, "systemctl --user restart {0} 2>/dev/null || true".format(
        UNIT_NAME))
    return True, "service installed"


def detail_or(text, fallback):
    """First line of some output, or a fallback. Never empty."""
    line = (text or "").strip().splitlines()
    return line[0] if line else fallback
