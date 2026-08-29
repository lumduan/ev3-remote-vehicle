"""HOST CODE. `ev3ctl live` - the dashboard and the control loop.

One thread, one select, two file descriptors: the SSH pipe and the tty.
That is the whole design. A blocking read on the pipe would freeze key
handling every time the brick was slow, and a blocking read on the tty
would freeze the display until somebody pressed something. Selecting on
both, with a short slice, means neither can stall the other and no async
framework is needed to say so.

Commands from keys are sent, not awaited. Their responses arrive through
the same pump as the polls, so a keypress costs nothing in latency and a
refused command lands in the footer instead of interrupting the frame.
"""

import select
import time

from rich.live import Live

from .. import ui
from ..console import console
from ..keys import Keyboard, NotATerminal
from ..model import OUTPUT_PORTS, nodes_changed
from ..session import clamp_duty, connect

# 5 Hz. The brick is a 300 MHz ARM9 on the far end of a USB link, and
# polling it faster buys nothing but a queue.
POLL_INTERVAL_S = 0.2
RENDER_INTERVAL_S = 0.2

# How long one turn of the loop waits before going round again. Short
# enough that a keypress feels immediate, long enough not to spin.
SLICE_S = 0.02

# A poll that never comes back would otherwise stop the heartbeat for
# good, and the agent's watchdog would cut the motors while the operator
# was still holding the key down.
POLL_RETRY_AFTER_S = 2.0

# No answer for this long and the header says so rather than pretending.
STALE_AFTER_S = 1.0

DUTY_STEP = 10

SELECT_OUTPUT = {"a": "outA", "b": "outB", "c": "outC", "d": "outD"}
# Requirement: `s` cycles "the selected input port's" sensor mode. The
# key bindings name no way to select an input port, so 1 to 4 do it -
# the digits are otherwise unused, 0 being stop-all.
SELECT_INPUT = {"1": "in1", "2": "in2", "3": "in3", "4": "in4"}


class Dashboard(object):
    """Everything the loop needs to know, and nothing it does not."""

    def __init__(self, session):
        self.session = session
        self.selected_out = "outA"
        self.selected_in = "in1"
        self.intended = dict((address, 0) for address in OUTPUT_PORTS)
        self.last_error = None
        self.pending = {}
        self.poll_id = None
        self.poll_sent_at = 0.0
        self.scan_id = None
        self.last_response_at = time.monotonic()
        self.quit = False

    # -- bookkeeping --------------------------------------------------

    def track(self, request_id, name):
        self.pending[request_id] = name
        return request_id

    def note(self, message):
        self.last_error = message

    def link_status(self):
        idle = time.monotonic() - self.last_response_at
        if idle > STALE_AFTER_S:
            return "[warn]slow - nothing back for {0:.1f}s[/warn]".format(
                idle)
        return "[ok]connected[/ok]"

    # -- responses ----------------------------------------------------

    def handle_response(self, response):
        self.last_response_at = time.monotonic()
        request_id = response.get("id")
        name = self.pending.pop(request_id, None)
        if request_id is not None and request_id == self.poll_id:
            self.poll_id = None
        if request_id is not None and request_id == self.scan_id:
            self.scan_id = None

        if not response.get("ok"):
            self.note("{0}: {1}".format(
                name or response.get("kind") or "agent",
                response.get("error") or "refused"))
            return

        result = response.get("result") or {}
        if name == "poll":
            snapshot = self.session.apply_poll(result)
            # Re-scan only when the device set changed. Doing it every
            # frame would cost a full inventory at 5 Hz; doing it never
            # would leave an unplugged motor on screen forever.
            if self.scan_id is None and nodes_changed(
                    self.session.inventory.nodes, snapshot.nodes):
                self.scan_id = self.track(self.session.send_scan(), "scan")
        elif name == "scan":
            self.session.apply_scan(result)

    # -- keys ---------------------------------------------------------

    def handle_key(self, key):
        if key == "q":
            self.quit = True
        elif key == "CTRL_C":
            raise KeyboardInterrupt
        elif key in SELECT_OUTPUT:
            self.selected_out = SELECT_OUTPUT[key]
        elif key in SELECT_INPUT:
            self.selected_in = SELECT_INPUT[key]
        elif key == "RIGHT":
            self._set_duty(self.intended[self.selected_out] + DUTY_STEP)
        elif key == "LEFT":
            self._set_duty(self.intended[self.selected_out] - DUTY_STEP)
        elif key == "SPACE":
            self._set_duty(0)
        elif key == "0":
            self._stop_all()
        elif key == "r":
            self._reset()
        elif key == "s":
            self._cycle_sensor_mode()

    def _set_duty(self, duty):
        address = self.selected_out
        duty = clamp_duty(duty)
        self.intended[address] = duty
        self.track(self.session.send_motor_run(address, duty),
                   "motor_run " + address)

    def _stop_all(self):
        for address in OUTPUT_PORTS:
            self.intended[address] = 0
        self.track(self.session.send_stop_all(), "stop_all")

    def _reset(self):
        address = self.selected_out
        self.intended[address] = 0
        self.track(self.session.send_motor_reset(address),
                   "motor_reset " + address)

    def _cycle_sensor_mode(self):
        address = self.selected_in
        sensor = self.session.inventory.sensor(address)
        if sensor is None:
            self.note("no sensor in port {0}".format(address))
            return
        modes = sensor.get("modes") or []
        if not modes:
            self.note("{0} reports no mode list".format(address))
            return
        live = self.session.snapshot.sensor(address) or {}
        current = live.get("mode") or sensor.get("mode")
        try:
            index = modes.index(current)
        except ValueError:
            index = -1
        target = modes[(index + 1) % len(modes)]
        self.track(self.session.send_sensor_mode(address, target),
                   "sensor_mode " + address)

    # -- frame --------------------------------------------------------

    def render(self):
        return ui.dashboard(
            self.session,
            self.session.inventory,
            self.session.snapshot,
            self.selected_out,
            self.selected_in,
            self.intended,
            last_error=self.last_error,
            link_status=self.link_status(),
        )


def run(args):
    keyboard = Keyboard()
    if not keyboard.is_tty:
        raise NotATerminal(
            "stdin is not a terminal, so the dashboard has nothing to "
            "read keys from"
        )

    # Connect before touching the terminal. Anything that might prompt
    # for a password happens here, on a normal shell, rather than
    # underneath a full-screen render.
    session = connect(
        host=args.host,
        agent_source=args.agent,
        timeout=args.timeout,
        multiplex=not args.no_multiplex,
    )
    try:
        session.scan()
        dashboard = Dashboard(session)
        keyboard.open()
        try:
            _loop(session, keyboard, dashboard)
        finally:
            # First, and locally: the terminal is put back before
            # anything talks to a link that may already be dead.
            keyboard.restore()
    finally:
        # Then the motors. The agent's watchdog is what makes this safe
        # to attempt rather than depend on.
        session.shutdown()
    return 0


def _loop(session, keyboard, dashboard):
    link = session.link
    key_fd = keyboard.fileno()

    with Live(dashboard.render(), console=console, screen=True,
              refresh_per_second=5, transient=False) as live:
        next_poll = 0.0
        next_render = 0.0
        while not dashboard.quit:
            now = time.monotonic()

            if dashboard.poll_id is not None and (
                    now - dashboard.poll_sent_at > POLL_RETRY_AFTER_S):
                dashboard.note("poll timed out, retrying")
                dashboard.poll_id = None

            if dashboard.poll_id is None and now >= next_poll:
                dashboard.poll_sent_at = now
                dashboard.poll_id = dashboard.track(
                    session.send_poll(), "poll")
                next_poll = now + POLL_INTERVAL_S

            ready, _, _ = select.select([link.stdout_fd, key_fd], [], [],
                                        SLICE_S)
            if link.stdout_fd in ready:
                for response in link.pump():
                    dashboard.handle_response(response)
            if key_fd in ready:
                for key in keyboard.read():
                    dashboard.handle_key(key)
                    if dashboard.quit:
                        break

            complaint = link.drain_stderr()
            if complaint.strip():
                # The watchdog announces itself here. It is the most
                # important thing the brick ever says, so it goes where
                # the operator is already looking.
                dashboard.note("brick: " + " ".join(complaint.split()))

            now = time.monotonic()
            if now >= next_render:
                live.update(dashboard.render())
                next_render = now + RENDER_INTERVAL_S
