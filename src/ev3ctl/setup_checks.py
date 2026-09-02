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

# The two programs that make the robot usable, and where they live on
# the brick. /home/robot rather than /tmp: /tmp does not survive a
# reboot, and Brickman's File Browser is where a child will look.
BRICK_DIR = "/home/robot/tanks_1"
PROGRAMS = ("tank_drive.py", "pad_buttons.py")

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


def local_digest(name):
    """The md5 of a program in `agent/`, or None if it is not there."""
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
            " ".join(os.path.join(BRICK_DIR, n) for n in PROGRAMS)))
    if not ok:
        return BAD, "missing", detail_or(out, "cannot ask the robot")

    found = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            found[os.path.basename(parts[1])] = parts[0]

    missing = [n for n in PROGRAMS if n not in found]
    if missing:
        return BAD, "missing", ", ".join(missing)
    stale = [n for n in PROGRAMS if found[n] != wanted[n]]
    if stale:
        return BAD, "old", ", ".join(stale)
    return OK, "ready", "both up to date"


def install(host):
    """Copy both programs to the brick and prove the copy landed.

    `cat >` rather than scp, the same choice link.py makes and for the
    same reason: modern OpenSSH runs scp over SFTP, which needs
    sftp-server on the far end, while cat needs only a shell.

    Returns (ok, detail).
    """
    ok, detail = _ssh(host, "mkdir -p " + BRICK_DIR)
    if not ok:
        return False, detail

    for name in PROGRAMS:
        path = os.path.join(agent_dir(), name)
        try:
            with open(path, "rb") as handle:
                source = handle.read()
        except OSError as exc:
            return False, str(exc)
        target = os.path.join(BRICK_DIR, name)
        ok, detail = _ssh(
            host, "cat > {0} && chmod +x {0}".format(target),
            timeout=60, data=source)
        if not ok:
            return False, "{0}: {1}".format(name, detail)

    # Read it back. A copy that reported success and landed truncated
    # would otherwise be discovered by a child, on the floor, later.
    verdict, state, detail = check_programs(host)
    if verdict != OK:
        return False, "copied but {0}: {1}".format(state, detail)
    return True, detail


def detail_or(text, fallback):
    """First line of some output, or a fallback. Never empty."""
    line = (text or "").strip().splitlines()
    return line[0] if line else fallback
