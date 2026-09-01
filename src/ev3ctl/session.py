"""HOST CODE. The commands, and the promise that motors end up stopped.

link.py knows how to move a JSON object. This module knows what the
objects mean, and owns the one rule that outranks the rest: every path
out of a session tries to stop every motor.

That try is not the safety mechanism. The agent's watchdog is, because
it still runs when this process is gone. What this module guarantees is
the ordinary case - a clean quit leaves nothing spinning for the second
it would take the watchdog to notice.
"""

from .link import Link
from .model import Inventory, Snapshot

TEARDOWN_TIMEOUT_S = 1.0


def clamp_duty(duty):
    """Clamp to -100..100 here, knowing the agent clamps again.

    Both ends clamp because neither end is entitled to assume the other
    is the version it was written against. The agent may be an older
    copy left in /tmp; this process may be talking to something else
    entirely.
    """
    try:
        value = int(duty)
    except (TypeError, ValueError):
        return 0
    return max(-100, min(100, value))


class Session(object):
    """A live conversation with one brick."""

    def __init__(self, link):
        self.link = link
        self.inventory = Inventory()
        self.snapshot = Snapshot()

    # -- facts --------------------------------------------------------

    @property
    def host(self):
        return self.link.host

    @property
    def kernel(self):
        return self.link.hello.get("kernel")

    @property
    def python(self):
        version = self.link.hello.get("python") or ""
        return version.split()[0] if version else None

    @property
    def hostname(self):
        return self.link.hello.get("hostname")

    @property
    def ev3dev_release(self):
        return self.link.hello.get("ev3dev_release")

    # -- inventory ----------------------------------------------------

    def scan(self):
        self.inventory = Inventory(self.link.request("scan"))
        return self.inventory

    def poll(self):
        self.snapshot = Snapshot(self.link.request("poll"))
        return self.snapshot

    # -- motors -------------------------------------------------------

    def motor_run(self, address, duty):
        return self.link.request(
            "motor_run", address=address, duty=clamp_duty(duty))

    def motor_stop(self, address):
        return self.link.request("motor_stop", address=address)

    def motor_reset(self, address):
        return self.link.request("motor_reset", address=address)

    def sensor_mode(self, address, mode):
        return self.link.request("sensor_mode", address=address, mode=mode)

    def stop_all(self):
        return self.link.request("stop_all")

    def set_stop_action(self, address, value):
        """Choose what stop means for one motor: coast, brake or hold.

        Blocking on purpose. It runs once at startup, before the loop,
        and the answer is worth printing before anything moves.
        """
        return self.link.request(
            "set_stop_action", address=address, value=value)

    # -- the gamepad --------------------------------------------------
    #
    # Opening, resetting and closing are blocking: each happens once at
    # a step boundary, where the wizard has nothing else to do and the
    # answer decides what it shows next. Only the state poll is sent
    # without waiting, because that one runs five times a second.

    def send_gamepad_open(self, name=None, uniq=None):
        """Ask for the pad without waiting.

        Step 0 of the wizard retries this until the operator presses PS,
        so it cannot be the blocking form: a `request` that timed out
        would freeze the display for eight seconds at exactly the moment
        the operator is watching it for a sign of life.
        """
        fields = {}
        if name:
            fields["name"] = name
        if uniq:
            fields["uniq"] = uniq
        return self.link.send("gamepad_open", **fields)

    def send_gamepad_reset_window(self):
        """Clear the window without waiting.

        The reset happens on the brick at a definite instant, and
        everything before it is discarded there. The wizard ignores
        state replies until this one's response lands, which is what
        makes a step's window start exactly here.
        """
        return self.link.send("gamepad_reset_window")

    def gamepad_close(self):
        """Stop the reader and close the device. Never raises.

        Called from the wizard's teardown on every path, including
        abort, so it swallows everything. The agent closes the device in
        its own finally as well; this is the tidy case, that is the
        backstop for the case where this process is already gone.
        """
        try:
            return self.link.request(
                "gamepad_close", timeout=TEARDOWN_TIMEOUT_S)
        except Exception:
            return {}

    def send_gamepad_state(self):
        return self.link.send("gamepad_state")

    # -- the same commands, without waiting ---------------------------
    #
    # The live loop uses these. A keypress that blocked until the brick
    # answered would make the dashboard stutter every time a motor was
    # nudged, and would let a slow round trip hold the terminal. The
    # responses come back through the loop's own pump, and an error
    # lands in the footer instead of stopping anything.

    def send_poll(self):
        return self.link.send("poll")

    def send_scan(self):
        return self.link.send("scan")

    def send_motor_run(self, address, duty):
        return self.link.send(
            "motor_run", address=address, duty=clamp_duty(duty))

    def send_motor_stop(self, address):
        return self.link.send("motor_stop", address=address)

    def send_motor_reset(self, address):
        return self.link.send("motor_reset", address=address)

    def send_sensor_mode(self, address, mode):
        return self.link.send("sensor_mode", address=address, mode=mode)

    def send_stop_all(self):
        return self.link.send("stop_all")

    def send_drive(self, left_address, left_duty, right_address, right_duty):
        """Both sides of a tank drive in one message.

        Two motor_run commands per loop iteration would double the round
        trips, and over Bluetooth PAN the round trip is the budget the
        whole control loop has to live inside.
        """
        return self.link.send(
            "drive",
            left_address=left_address, left_duty=clamp_duty(left_duty),
            right_address=right_address, right_duty=clamp_duty(right_duty))

    def apply_scan(self, payload):
        self.inventory = Inventory(payload)
        return self.inventory

    def apply_poll(self, payload):
        self.snapshot = Snapshot(payload)
        return self.snapshot

    # -- teardown -----------------------------------------------------

    def shutdown(self):
        """Stop everything and end the session. Never raises.

        Runs from finally blocks, including ones entered because the
        link already died, so every step is individually optional and
        every failure is swallowed. A teardown that raises would replace
        the real error with a less useful one.
        """
        for command in ("stop_all", "bye"):
            try:
                self.link.request(command, timeout=TEARDOWN_TIMEOUT_S)
            except Exception:
                # Deliberately everything. This runs while another
                # exception may already be propagating, and a
                # teardown that raises replaces the real error.
                pass
        self.link.close()


def connect(host, agent_source=None, timeout=None, multiplex=True):
    """Open a session, handshake complete. Raises LinkError on failure."""
    link = Link(host=host, agent_source=agent_source, multiplex=multiplex,
                **({"timeout": timeout} if timeout else {}))
    link.open()
    return Session(link)
