"""HOST CODE. `ev3ctl drive` - tank steering from WASD.

Identical over USB and over Bluetooth PAN. There is no transport layer
here and there deliberately is not one: both are the same SSH
invocation with a different `--host`. What changes between them is
latency, and latency is handled by refusing to build a backlog rather
than by knowing which cable is in use.

Two things about a terminal shape this whole file.

A terminal delivers key *presses* and never key releases. There is no
event that says the driver let go of `w`. So a key is treated as held
until a timeout expires, refreshed by the operating system's own
auto-repeat, and released when the refreshes stop arriving. That is why
tapping a key runs the motors for about `INITIAL_HOLD_MS` and why
letting go stops them about `REPEAT_HOLD_MS` later. It is a cost of the
input device, not a fault.

And a terminal is slow to talk to a brick. One `drive` is in flight at
a time; newer state replaces the pending state rather than queueing
behind it, so what reaches the motors is always the most recent
intention and never a backlog of stale ones.
"""

import select
import time
from collections import deque

from rich.live import Live

from .. import ui
from ..console import console
from ..errors import AgentError, Diagnosis, LinkError
from ..keys import Keyboard, NotATerminal
from ..mixer import MOVEMENT_KEYS, axes, duties, slew
from ..model import port_key
from ..session import connect

# The operating system's initial auto-repeat delay is the number this
# has to cover. Set it much below 600 ms and a held key stutters,
# because the gap between the first byte and the second is longer than
# the hold, and the key looks released in between.
INITIAL_HOLD_MS = 600

# Once auto-repeat is running, bytes arrive far faster than this. It is
# therefore also the worst-case latency between letting go of a key and
# the motors being told about it.
REPEAT_HOLD_MS = 150

# The one number to lower for a beginner. Applied last, to both sides.
SPEED_SCALE = 40

# Per side, per loop iteration. At 50 ms a loop and speed 40, reaching
# full takes about 7 loops, a third of a second.
SLEW_PCT_PER_LOOP = 6

LOOP_PERIOD_S = 0.05
RENDER_INTERVAL_S = 0.1

# A drive that never comes back would otherwise stall the loop for good
# and let the brick's watchdog cut the motors under a held key.
DRIVE_RETRY_AFTER_S = 2.0

# Long enough to catch a bad moment on Bluetooth PAN, short enough that
# one startup outlier does not dominate the reading for the whole run.
RTT_WINDOW_S = 10.0

STOP_ACTION = "brake"

QUIT_KEYS = ("q",)
ZERO_KEYS = ("SPACE",)
BOUND_KEYS = tuple(MOVEMENT_KEYS) + QUIT_KEYS + ZERO_KEYS + ("CTRL_C",)


class Drive(object):
    """Everything the loop needs, and nothing it does not."""

    def __init__(self, session, left, right, args):
        self.session = session
        self.left = left
        self.right = right
        self.invert_left = args.invert_left
        self.invert_right = args.invert_right
        self.speed = args.speed
        self.slew_limit = SLEW_PCT_PER_LOOP
        self.initial_hold = args.initial_hold_ms / 1000.0
        self.repeat_hold = args.repeat_hold_ms / 1000.0

        self.held = {}
        self.duty_left = 0.0
        self.duty_right = 0.0
        self.throttle = 0.0
        self.turn = 0.0

        self.pending_id = None
        self.pending_sent_at = 0.0
        self.pending_duties = (0.0, 0.0)
        self.rtt = None
        self.rtt_samples = deque()

        self.readback = {}
        self.watchdog_trips = 0
        self._was_cut = False
        self._was_zero = True

        self.stop_action = {}
        self.last_error = None
        self.quit = False

    # -- keys ---------------------------------------------------------

    def take_keys(self, keys, now):
        """Fold one batch of key names into the held set.

        Any byte that is not a bound key is ignored completely. It must
        not look like movement and must not refresh anything.
        """
        for key in keys:
            if key not in BOUND_KEYS:
                continue
            if key == "CTRL_C":
                raise KeyboardInterrupt
            if key in QUIT_KEYS:
                self.quit = True
                continue
            if key in ZERO_KEYS:
                # The panic key. Zero both sides at once without waiting
                # for the held keys to time out, and without the slew
                # limit, which is the whole point of having it.
                self.held.clear()
                self.duty_left = 0.0
                self.duty_right = 0.0
                continue
            if key in self.held and now < self.held[key]:
                self.held[key] = now + self.repeat_hold
            else:
                self.held[key] = now + self.initial_hold

    def expire(self, now):
        for key in list(self.held):
            if self.held[key] <= now:
                del self.held[key]

    # -- mixing -------------------------------------------------------

    def step(self, now):
        """Advance the commanded duties one loop iteration."""
        self.expire(now)
        self.throttle, self.turn = axes(self.held)
        target_left, target_right = duties(self.held, self.speed)

        if target_left == 0.0 and target_right == 0.0:
            # Zero is never slewed toward. Letting go of the keys has to
            # command zero now, not ramp down over a third of a second,
            # and commanding zero is not the same as coasting: the motor
            # is still held in run-direct at duty 0.
            self.duty_left = 0.0
            self.duty_right = 0.0
        else:
            self.duty_left = slew(self.duty_left, target_left,
                                  SLEW_PCT_PER_LOOP)
            self.duty_right = slew(self.duty_right, target_right,
                                   SLEW_PCT_PER_LOOP)

    def wire_duties(self):
        """What actually goes on the wire, inversion applied last.

        Inversion is a wiring correction for a motor mounted mirrored.
        Keeping it out of the mixing means the display shows what the
        vehicle is being asked to do, not what the cabling made of it.
        """
        left = -self.duty_left if self.invert_left else self.duty_left
        right = -self.duty_right if self.invert_right else self.duty_right
        return int(round(left)), int(round(right))

    def is_zero(self):
        return self.duty_left == 0.0 and self.duty_right == 0.0

    # -- link ---------------------------------------------------------

    def send(self, now):
        left, right = self.wire_duties()
        self.pending_id = self.session.send_drive(
            self.left, left, self.right, right)
        self.pending_sent_at = now
        # Remembered per request, not read from self when the reply
        # lands. A reply describes the command it answers, and by the
        # time it arrives - a round trip later, 96 ms over USB and more
        # over PAN - the ramp has already moved on. Comparing it against
        # the current duty reports a cut on every ramp.
        self.pending_duties = (self.duty_left, self.duty_right)

    def maybe_send(self, now):
        """Send the current state if the link is free to take it.

        Sent every iteration rather than only on change, because this is
        also the heartbeat: the brick's watchdog cuts the motors after
        1000 ms of silence, and a driver holding `w` steadily produces
        no changes at all once the ramp is finished.
        """
        became_zero = self.is_zero() and not self._was_zero
        self._was_zero = self.is_zero()

        if self.pending_id is not None and (
                now - self.pending_sent_at > DRIVE_RETRY_AFTER_S):
            self.note("drive timed out, retrying")
            self.pending_id = None

        if self.pending_id is None:
            self.send(now)
        elif became_zero:
            # Stopping is the one thing worth sending while another
            # drive is still outstanding. One safety command is not a
            # backlog, and waiting a round trip to stop is not a trade
            # worth making.
            self.send(now)

    def handle_response(self, response):
        request_id = response.get("id")
        answered = request_id is not None and request_id == self.pending_id
        commanded = self.pending_duties
        if answered:
            self.pending_id = None
            self.observe_rtt(time.monotonic() - self.pending_sent_at)
        if not response.get("ok"):
            self.note(response.get("error") or "drive refused")
            return
        result = response.get("result") or {}
        if "left" in result and "right" in result:
            self.readback = result
            if answered:
                self.check_for_cut(result, commanded)

    def observe_rtt(self, seconds):
        now = time.monotonic()
        self.rtt = seconds
        self.rtt_samples.append((now, seconds))
        while self.rtt_samples and now - self.rtt_samples[0][0] > RTT_WINDOW_S:
            self.rtt_samples.popleft()

    def rtt_max(self):
        if not self.rtt_samples:
            return None
        return max(sample for _, sample in self.rtt_samples)

    def check_for_cut(self, result, commanded):
        """Infer a watchdog trip from commanded against actual.

        Measured on this hardware: when the watchdog fires it writes
        `stop`, which drops `duty_cycle` to zero and leaves
        `duty_cycle_sp` at whatever was last commanded. So the signature
        is actual duty zero while a non-zero duty was asked for, and
        `duty_cycle_sp` is no use for spotting it.

        `commanded` is the pair that went out with *this* reply's
        request, not the current one. Counted per transition rather than
        per sample; one trip that lasts three frames is one trip.
        """
        cut = False
        for side, asked in (("left", commanded[0]),
                            ("right", commanded[1])):
            if asked == 0:
                continue
            actual = (result.get(side) or {}).get("duty_cycle")
            if actual == 0:
                cut = True
        if cut and not self._was_cut:
            self.watchdog_trips += 1
        self._was_cut = cut

    def note(self, message):
        self.last_error = message

    # -- frame --------------------------------------------------------

    def render(self):
        return ui.drive_dashboard(self)


def _pick_motors(session, args):
    """Resolve which two motors to drive, or explain why we cannot."""
    inventory = session.scan()
    found = sorted(inventory.motors)
    left = port_key(args.left) if args.left else None
    right = port_key(args.right) if args.right else None

    if left is None or right is None:
        if len(found) < 2:
            raise LinkError(Diagnosis(
                summary="Need two motors to drive, found {0}".format(
                    len(found)),
                cause=(
                    "Ports with a tacho-motor attached: {0}. Output ports "
                    "with nothing on them cannot be driven.".format(
                        ", ".join(found) if found else "none")
                ),
                checklist=(
                    "plug a motor into two output ports",
                    "run `ev3ctl scan` to see what the brick reports",
                    "or name the ports yourself with --left and --right",
                ),
            ))
        left = left or found[0]
        right = right or found[1]

    for name, address in (("--left", left), ("--right", right)):
        if inventory.motor(address) is None:
            raise LinkError(Diagnosis(
                summary="No motor on {0} for {1}".format(address, name),
                cause="Motors were found on: {0}.".format(
                    ", ".join(found) if found else "no ports at all"),
                checklist=("run `ev3ctl scan` to see what is attached",),
            ))
    if left == right:
        raise LinkError(Diagnosis(
            summary="--left and --right are both {0}".format(left),
            cause="One motor cannot steer a vehicle.",
            checklist=("give two different ports",),
        ))
    return left, right


def _brake_on_stop(session, drive, addresses):
    """Make stop mean stop, and say what it used to mean.

    The driver default is coast, measured at about 0.66 s of freewheel
    after the drive is cut. On a bench that is invisible. On something
    with wheels it means the vehicle rolls on after the link dies, which
    is exactly the moment it should not.
    """
    for address in addresses:
        try:
            result = session.set_stop_action(address, STOP_ACTION)
            drive.stop_action[address] = (
                result.get("previous"), result.get("stop_action"))
        except AgentError as exc:
            drive.stop_action[address] = (None, None)
            drive.note("{0}: could not set stop_action: {1}".format(
                address, exc))


def run(args):
    keyboard = Keyboard()
    if not keyboard.is_tty:
        raise NotATerminal(
            "stdin is not a terminal, so there are no keys to drive with"
        )

    session = connect(
        host=args.host,
        agent_source=args.agent,
        timeout=args.timeout,
        multiplex=not args.no_multiplex,
    )
    try:
        left, right = _pick_motors(session, args)
        drive = Drive(session, left, right, args)
        _brake_on_stop(session, drive, (left, right))

        keyboard.open()
        try:
            _loop(session, keyboard, drive)
        finally:
            # Local, instant, and cannot fail on a dead link. Doing this
            # first costs the motors nothing measurable; doing it last
            # would leave the operator's shell in cbreak for as long as
            # the teardown spends timing out. The brick's watchdog is
            # what actually stops the motors either way.
            keyboard.restore()
    finally:
        session.shutdown()
    return 0


def _loop(session, keyboard, drive):
    link = session.link
    key_fd = keyboard.fileno()

    with Live(drive.render(), console=console, screen=True,
              refresh_per_second=10, transient=False) as live:
        next_tick = time.monotonic()
        next_render = 0.0
        while not drive.quit:
            now = time.monotonic()

            timeout = max(0.0, min(next_tick - now, LOOP_PERIOD_S))
            ready, _, _ = select.select([link.stdout_fd, key_fd], [], [],
                                        timeout)
            if link.stdout_fd in ready:
                for response in link.pump():
                    drive.handle_response(response)
            if key_fd in ready:
                drive.take_keys(keyboard.read(), time.monotonic())

            now = time.monotonic()
            if now >= next_tick:
                next_tick = now + LOOP_PERIOD_S
                drive.step(now)
                drive.maybe_send(now)

            complaint = link.drain_stderr()
            if complaint.strip():
                drive.note("brick: " + " ".join(complaint.split()))

            if now >= next_render:
                live.update(drive.render())
                next_render = now + RENDER_INTERVAL_S
