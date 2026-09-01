"""HOST CODE. `ev3ctl gamepad` - the mapping wizard and its loop.

The same one-thread, one-select, two-descriptor shape as `live`: the SSH
pipe and the tty, neither able to stall the other. What differs is what
the loop is for. `live` renders a dashboard; this renders a *question*,
and answers it by watching the counters change.

Every step shows its own advance condition and how close the operator is
to satisfying it, because the failure this replaces was a capture that
could not be seen until it was over. A step that will not advance has to
say which axis has not moved far enough, not merely that it is waiting.

The wizard never assumes anything about the controller. It knows that
evdev code 0 is called ABS_X; it does not know, until a step shows it,
which stick moves it. The one thing a circular sweep genuinely cannot
reveal - which of a stick's two axes is horizontal - is written to the
mapping as null rather than filled in from a layout on the internet.
"""

import datetime
import json
import os
import select
import time
from pathlib import Path

from rich.live import Live

from .. import evdev_codes, gamepad, ui
from ..console import console
from ..errors import Diagnosis, LinkError
from ..keys import Keyboard, NotATerminal
from ..session import connect

# 5 Hz, as required, and the same cadence live.py settled on for the
# same reason: the brick is a 300 MHz ARM9 and polling it faster buys a
# queue rather than information.
POLL_INTERVAL_S = 0.2
RENDER_INTERVAL_S = 0.2
SLICE_S = 0.02

# A state poll that never comes back would otherwise stop the display
# for good. Clearing the id lets the next iteration send another.
POLL_RETRY_AFTER_S = 2.0

# Step 0 retries the open at this interval while the pad is off. Slower
# than the poll because each attempt reads /proc and probes 64 axes.
OPEN_RETRY_AFTER_S = 1.0

# A window reset that never comes back would leave the step disarmed
# for good, and disarmed is silent: state replies keep arriving and keep
# being ignored, so the step simply never advances and nothing says why.
# Step 6 now resets once per button, so there are fifteen more chances
# for a lost reply to strand the wizard than there used to be.
RESET_RETRY_AFTER_S = 2.0

DEFAULT_OUTPUT = os.path.join("docs", "gamepad-mapping.json")

CONNECT = gamepad.CONNECT
REST = gamepad.REST
LEFT = gamepad.LEFT
RIGHT = gamepad.RIGHT
BUTTONS = gamepad.BUTTONS
SUMMARY = gamepad.SUMMARY
SWEEP = gamepad.SWEEP
HOLD_RIGHT = gamepad.HOLD_RIGHT
HOLD_UP = gamepad.HOLD_UP
STEPS = gamepad.STEPS
RESETTING_STEPS = gamepad.RESETTING_STEPS
STICK_CONTROLS = gamepad.STICK_STEPS
TRIGGER_CONTROLS = gamepad.TRIGGER_STEPS


class Wizard(object):
    """Everything the frame is drawn from, and the step machine."""

    def __init__(self, session, output_path, name=None, uniq=None):
        self.session = session
        self.output_path = output_path
        self.name = name
        self.uniq = uniq
        self.step = CONNECT
        self.quit = False
        self.aborted = False
        self.finished = False
        self.last_error = None
        self.note_text = None

        self.pending = {}
        self.poll_id = None
        self.poll_sent_at = 0.0
        self.open_id = None
        self.open_sent_at = 0.0
        self.last_response_at = time.monotonic()

        # Set once the device is open, and never guessed at.
        self.device = None
        self.drivers = {}
        self.declared_axes = []
        self.columns_ok = True
        self.blocked = None

        # Reset by every step entry. `armed` is False until the reset
        # for this step has been acknowledged, so no state reply from
        # before the window boundary is ever judged.
        self.armed = False
        self.reset_id = None
        self.reset_sent_at = 0.0

        self.codes = {}
        self.total_events = 0
        self.present = False
        self.device_gone = False

        self.rest = {}
        self.rest_windows = {}
        self.rest_since = None
        self.assignments = {}
        self.step_axes = {}
        # The window each step was judged on, kept because `codes` only
        # ever holds the current step and the summary needs all of them.
        self.step_windows = {}
        self.continuity = {}
        # Where a stick step has got to: the sweep, then the two
        # directional holds that tell horizontal from vertical.
        self.phase = SWEEP
        self.hold_since = None
        self.hold_outcome = None
        self.orientation = {}
        self.polarity = {}
        self.hold_deviations = {}
        self.buttons = []
        self.button_index = 0
        self.wrong_stick = False
        self.written_to = None
        self.write_error = None

    # -- bookkeeping --------------------------------------------------

    def track(self, request_id, name):
        self.pending[request_id] = name
        return request_id

    def note(self, message):
        self.last_error = message

    def step_title(self):
        return STEPS[self.step][1]

    def instruction(self):
        """What to do now, which inside a stick step depends on phase."""
        if self.step in STICK_CONTROLS and self.phase != SWEEP:
            side = "LEFT" if self.step == LEFT else "RIGHT"
            pushed = gamepad.HOLD_DIRECTIONS[self.phase][0]
            return ("Push the {0} stick fully {1} and hold it there, "
                    "along that one direction only.".format(side, pushed))
        return STEPS[self.step][2]

    def claimed(self):
        """Every axis a previous step has already named."""
        claimed = set()
        for keys in self.step_axes.values():
            claimed.update(keys)
        return claimed

    def link_status(self):
        idle = time.monotonic() - self.last_response_at
        if idle > 1.0:
            return "[warn]slow - nothing back for {0:.1f}s[/warn]".format(
                idle)
        return "[ok]connected[/ok]"

    # -- responses ----------------------------------------------------

    def handle_response(self, response):
        self.last_response_at = time.monotonic()
        request_id = response.get("id")
        name = self.pending.pop(request_id, None)
        if name == "state":
            self.poll_id = None
        if name == "reset" and request_id == self.reset_id:
            self.reset_id = None

        if not response.get("ok"):
            self._handle_failure(name, response)
            return
        result = response.get("result") or {}
        if name == "open":
            self.open_id = None
            self._opened(result)
        elif name == "state":
            self._state(result)
        elif name == "reset":
            self.armed = True
            self.rest_since = None

    def _handle_failure(self, name, response):
        kind = response.get("kind") or "error"
        message = response.get("error") or "the brick refused that command"
        if kind == "ambiguous_gamepad":
            # Keyed on the kind, not on which request this was a reply
            # to. Only gamepad_open can produce it, and refusing to
            # proceed must not depend on the bookkeeping having kept up:
            # the two transports use different HID report layouts, so a
            # mapping taken from the wrong one is wrong without ever
            # looking wrong.
            self.blocked = message
            return
        if name == "open":
            self.open_id = None
            if kind == "no_gamepad":
                self.note(message)
                return
        self.note("{0}: {1}".format(kind, message))

    def _opened(self, result):
        self.device = result
        self.columns_ok = gamepad.columns_match(result.get("columns"))
        if not self.columns_ok:
            self.note(
                "the agent returned a different column order than this "
                "version understands; update the copy on the brick")
        drivers = {}
        for code_text, info in (result.get("absinfo") or {}).items():
            try:
                code = int(code_text)
            except (TypeError, ValueError):
                continue
            drivers[(evdev_codes.EV_ABS, code)] = info
        self.drivers = drivers
        self.declared_axes = gamepad.axis_codes_from_mask(
            result.get("abs_mask"))
        self.enter(REST)

    def _state(self, result):
        self.present = bool(result.get("present"))
        self.device_gone = bool(result.get("device_gone"))
        self.total_events = result.get("total_events") or 0
        if self.armed:
            self.codes = gamepad.rows_to_codes(result.get("rows"))

    # -- the step machine ---------------------------------------------

    def enter(self, step):
        """Move to a step, clearing whatever it is about to measure."""
        self.step = step
        self.armed = step not in RESETTING_STEPS
        self.rest_since = None
        self.hold_since = None
        self.hold_outcome = None
        self.phase = SWEEP
        self.wrong_stick = False
        self.codes = {}
        if step in RESETTING_STEPS:
            self.rearm_window()
        if step == SUMMARY:
            self._write_mapping()

    def redo(self):
        """Start the current step again, forgetting what it found.

        During a directional hold this restarts only that hold. The
        sweep already established which pair of axes the stick owns, and
        making the operator sweep again to correct a diagonal push would
        throw away a good measurement to fix a different one.
        """
        if self.step in STICK_CONTROLS and self.phase != SWEEP:
            for key in self.step_axes.get(self.step, ()):
                self.orientation.pop(key, None)
                self.polarity.pop(key, None)
            self.enter_hold(self.phase)
            return
        self.step_axes.pop(self.step, None)
        for key in list(self.orientation):
            if self.assignments.get(key) == STICK_CONTROLS.get(self.step):
                self.orientation.pop(key, None)
                self.polarity.pop(key, None)
                self.hold_deviations.pop(key, None)
        for key, control in list(self.assignments.items()):
            if control == STICK_CONTROLS.get(self.step) or \
                    control == TRIGGER_CONTROLS.get(self.step):
                self.assignments.pop(key, None)
        if self.step == BUTTONS:
            self.buttons = []
            self.button_index = 0
        if self.step == REST:
            self.rest = {}
        self.enter(self.step)

    def skip(self):
        """Give up on this step and go on, leaving its axes unassigned."""
        if self.step == SUMMARY:
            self.quit = True
            return
        if self.step == CONNECT:
            # Nothing after this works without a device. Skipping would
            # strand the wizard on a step that can never be satisfied,
            # which is a worse answer than saying so.
            self.note("There is nothing to skip to until the controller "
                      "is found. Press PS, or q to give up.")
            return
        self.enter(self.step + 1)

    def tick(self, now):
        """Advance the machine if this step's condition is satisfied."""
        if self.blocked or self.quit:
            return
        if self.step == CONNECT:
            self._tick_connect(now)
            return
        if not self.device:
            return
        if not self.armed:
            if now - self.reset_sent_at > RESET_RETRY_AFTER_S:
                self.note("window reset timed out, retrying")
                self.rearm_window()
            return
        if self.step == REST:
            self._tick_rest(now)
        elif self.step in STICK_CONTROLS:
            self._tick_stick(now)
        elif self.step in TRIGGER_CONTROLS:
            self._tick_trigger()
        elif self.step == BUTTONS:
            self._tick_buttons()

    def _tick_connect(self, now):
        """Ask for the pad, one request at a time.

        Never more than one open outstanding. Each gamepad_open closes
        whatever is already open before it reopens, so a second attempt
        sent while the first was still travelling would tear down the
        reader thread the first one had just started, on a brick slow
        enough to have caused the retry in the first place.
        """
        if self.device is not None:
            return
        if self.open_id is not None:
            if now - self.open_sent_at <= OPEN_RETRY_AFTER_S:
                return
            self.open_id = None
        self.open_sent_at = now
        self.open_id = self.track(
            self.session.send_gamepad_open(self.name), "open")

    def _tick_rest(self, now):
        if not self.present:
            # Any gap in the data restarts the three seconds. A pad that
            # dropped out and came back has not been resting throughout.
            self.rest_since = None
            return
        if self.rest_since is None:
            self.rest_since = now
            return
        if now - self.rest_since < gamepad.REST_SECONDS:
            return
        self.rest = gamepad.rest_report(self.codes)
        # Kept so that an axis which is never swept still reports what
        # it was seen doing, rather than being written out as unseen.
        self.rest_windows = dict(self.codes)
        self.enter(LEFT)

    def rest_progress(self, now):
        if self.rest_since is None:
            return 0.0
        return min(1.0, (now - self.rest_since) / gamepad.REST_SECONDS)

    def _tick_stick(self, now):
        if self.phase == SWEEP:
            self._tick_sweep()
        else:
            self._tick_hold(now)

    def _tick_sweep(self):
        """Which pair of axes this stick owns. Unchanged, and still first.

        The holds are added after this passes, not instead of it: the
        pair has to be known before there is anything to disambiguate.
        """
        found = gamepad.qualifying_axes(
            self.codes, self.drivers, self.rest)
        previous = self.step_axes.get(LEFT, ())
        if self.step == RIGHT and previous:
            if set(found) == set(previous) and found:
                # Named rather than silently accepted: two steps that
                # both name the same pair would produce a mapping in
                # which the right stick does not exist.
                self.wrong_stick = True
                return
            self.wrong_stick = False
            found = [key for key in found if key not in previous]
        if len(found) != gamepad.STICK_AXES_PER_STEP:
            return
        self.step_axes[self.step] = tuple(found)
        for key in found:
            self.assignments[key] = STICK_CONTROLS[self.step]
            # Captured before the holds reset the window, so the file
            # reports the range the sweep saw rather than the much
            # smaller one a held stick produces.
            self.step_windows[key] = self.codes[key]
        self.enter_hold(HOLD_RIGHT)

    def enter_hold(self, phase):
        """Begin a directional hold, measuring only from here on.

        The window is reset so the deviations describe this hold alone.
        That also makes a retry cleaner than a first attempt: the stick
        is usually already where it should be by then, so the axis that
        should not move starts and stays at its rest value.
        """
        self.phase = phase
        self.hold_since = None
        self.hold_outcome = None
        self.rearm_window()

    def _tick_hold(self, now):
        pair = self.step_axes.get(self.step, ())
        if len(pair) != gamepad.STICK_AXES_PER_STEP:
            return
        if not self.present:
            self.hold_since = None
            return
        if self.hold_since is None:
            self.hold_since = now
            return
        if now - self.hold_since < gamepad.HOLD_SECONDS:
            return

        outcome = gamepad.resolve_hold(
            pair, self.codes, self.rest, self.drivers)
        self.hold_outcome = outcome
        if outcome["verdict"] != "ok":
            # Say what went wrong and measure again from here, so that
            # correcting the push is enough. The whole step is not
            # restarted: the sweep already established the pair, and
            # making the operator sweep again to fix a diagonal would
            # punish them for the tool's question being imprecise.
            self.note(_hold_complaint(self.phase, outcome))
            self.enter_hold(self.phase)
            # Re-attached after the re-entry, which clears it: the
            # verdict that was just rejected is the most useful thing on
            # the screen while the operator corrects their push.
            self.hold_outcome = outcome
            return

        key, orientation, direction = gamepad.hold_outcome(
            self.phase, outcome)
        if self.phase == HOLD_UP and self.orientation.get(key) is not None:
            # The axis that led this hold is the one the right hold
            # already named horizontal, so the stick was pushed sideways
            # again rather than up. Naming it vertical as well would put
            # a contradiction in the file and leave the other axis of the
            # pair with no orientation at all.
            self.note(
                "{0} is the axis that led the RIGHT hold, so that was "
                "another sideways push. Push the stick UP - at a right "
                "angle to the last one - and hold.".format(
                    evdev_codes.label(*key)))
            self.enter_hold(self.phase)
            self.hold_outcome = outcome
            return
        self.orientation[key] = orientation
        self.polarity[key] = direction
        self._record_deviations(outcome)
        self.note(None)

        if self.phase == HOLD_RIGHT:
            self.enter_hold(HOLD_UP)
            return
        # The other axis of the pair is the one this hold did not name,
        # and the sweep already proved it belongs to this stick.
        self.enter(self.step + 1)

    def _record_deviations(self, outcome):
        """Keep what both axes did, so the decision can be re-checked."""
        label = "right_hold" if self.phase == HOLD_RIGHT else "up_hold"
        for key, value in outcome["deviations"].items():
            record = self.hold_deviations.setdefault(key, {})
            record[label] = round(value, 2) if value is not None else None

    def hold_progress(self, now):
        if self.hold_since is None:
            return 0.0
        return min(1.0, (now - self.hold_since) / gamepad.HOLD_SECONDS)

    def live_hold(self):
        """The outcome as it stands right now, for the live display."""
        pair = self.step_axes.get(self.step, ())
        if len(pair) != gamepad.STICK_AXES_PER_STEP:
            return None
        return gamepad.resolve_hold(
            pair, self.codes, self.rest, self.drivers)

    def _tick_trigger(self):
        found = gamepad.qualifying_triggers(
            self.codes, self.drivers, exclude=self.claimed())
        if len(found) != 1:
            return
        key = found[0]
        self.step_axes[self.step] = (key,)
        self.assignments[key] = TRIGGER_CONTROLS[self.step]
        self.continuity[key] = gamepad.continuity(self.codes.get(key))
        self.step_windows[key] = self.codes[key]
        self.enter(self.step + 1)

    def _tick_buttons(self):
        """Record whatever arrives while a label is being prompted.

        Both event types are accepted, and that is not defensive
        programming: on this hardware the D-pad genuinely is not
        EV_KEY. hid-sony declares ABS_HAT0X and ABS_HAT0Y and maps the
        four directions onto those two hat axes, so a step listening
        only for EV_KEY would record nothing for four of the fifteen
        prompts and look like a broken controller.

        Axes a stick or trigger step already claimed are ignored, so a
        nudged stick cannot be recorded as a button.
        """
        if self.button_index >= len(gamepad.BUTTON_PROMPTS):
            return
        label = gamepad.BUTTON_PROMPTS[self.button_index]
        claimed = self.claimed()
        for key, entry in sorted(self.codes.items()):
            if entry["count"] == 0 or key in claimed:
                continue
            value = gamepad.press_value(key, entry)
            if value is None:
                continue
            self.buttons.append((key[0], key[1], label, value))
            self.button_index += 1
            # Start a fresh window for the next prompt. Without this the
            # hat axes accumulate: up leaves -1 in the window's minimum
            # and down leaves +1 in its maximum, so the third and fourth
            # D-pad prompts would find nothing new on either axis and
            # the step would stall with two directions unrecorded.
            self.rearm_window()
            return

    def rearm_window(self):
        """Reset the accumulated window and wait for it to take effect."""
        self.armed = False
        self.codes = {}
        self.reset_sent_at = time.monotonic()
        self.reset_id = self.track(
            self.session.send_gamepad_reset_window(), "reset")

    def button_prompt(self):
        if self.button_index >= len(gamepad.BUTTON_PROMPTS):
            return None
        return gamepad.BUTTON_PROMPTS[self.button_index]

    # -- keys ---------------------------------------------------------

    def handle_key(self, key):
        if key == "CTRL_C":
            raise KeyboardInterrupt
        if key == "q":
            self.aborted = self.step != SUMMARY
            self.quit = True
        elif key == "s":
            self.skip()
        elif key == "r":
            self.redo()

    # -- output -------------------------------------------------------

    def _write_mapping(self):
        """Build the document and write it. Never raises into the loop."""
        document = self.mapping()
        try:
            directory = os.path.dirname(self.output_path)
            if directory:
                Path(directory).mkdir(parents=True, exist_ok=True)
            with open(self.output_path, "w") as handle:
                json.dump(document, handle, indent=2, sort_keys=False)
                handle.write("\n")
            self.written_to = self.output_path
        except OSError as exc:
            # The capture is the expensive part and it is already done.
            # Losing the file is bad; losing it *and* the summary table
            # on screen would mean repeating the whole session.
            self.write_error = str(exc)
            self.note("could not write {0}: {1}".format(
                self.output_path, exc))
        self.finished = True

    def mapping(self):
        device = self.device or {}
        return gamepad.build_mapping(
            device={
                "name": device.get("name"),
                "transport": device.get("transport"),
                "transport_agreement": device.get("transport_agreement"),
                "bus": device.get("bus"),
                "phys": device.get("phys"),
                "uniq": device.get("uniq"),
                "vendor": device.get("vendor"),
                "product": device.get("product"),
                "event_path": device.get("path"),
            },
            captured_at=_timestamp(),
            assignments=self.assignments,
            rest=self.rest,
            codes=self.codes_for_output(),
            drivers=self.drivers,
            buttons=self.buttons,
            declared_axes=self.declared_axes,
            orientation=self.orientation,
            polarity=self.polarity,
            hold_deviations=self.hold_deviations,
        )

    def codes_for_output(self):
        """Every axis the run measured, merged across the steps.

        The live `codes` dict only holds the current step's window, and
        the summary needs every axis each earlier step measured. The
        per-step observation is what is kept, because that is the window
        the assignment was made from.
        """
        merged = dict(self.step_windows)
        for source in (self.rest_windows, self.codes):
            for key, entry in source.items():
                merged.setdefault(key, entry)
        return merged

    def render(self):
        return ui.gamepad_dashboard(self)


def _hold_complaint(phase, outcome):
    """Why a hold was not accepted, in terms the operator can act on."""
    pushed = gamepad.HOLD_DIRECTIONS[phase][0]
    verdict = outcome["verdict"]
    if verdict == "soft":
        return ("Nothing moved far enough to be a deliberate push. Hold "
                "the stick fully {0} against the rim and keep it "
                "there.".format(pushed))
    if verdict == "diagonal":
        ratio = outcome["ratio"]
        return ("Both axes moved by a similar amount ({0:.1f}x apart, "
                "and {1:.0f}x is wanted), so that was a diagonal. Push "
                "straight {2} - along one axis only - and hold."
                .format(ratio or 0.0, gamepad.HOLD_RATIO, pushed))
    return ("There is no rest measurement to compare against, so the "
            "deflection cannot be judged. Redo step 1.")


def _timestamp():
    """Local time with its offset, so a capture can be placed later."""
    return datetime.datetime.now(
        datetime.timezone.utc).astimezone().replace(
            microsecond=0).isoformat()


def _default_output():
    """`docs/gamepad-mapping.json` beside the repository, not the cwd.

    Found the same way link.py finds the agent: by walking up from this
    file. A capture run from somewhere else should still land with the
    project it belongs to.
    """
    root = Path(__file__).resolve().parents[3]
    return str(root / DEFAULT_OUTPUT)


def run(args):
    keyboard = Keyboard()
    if not keyboard.is_tty:
        raise NotATerminal(
            "stdin is not a terminal, so the wizard has nothing to read "
            "keys from"
        )

    output_path = getattr(args, "output", None) or _default_output()

    # Connect before touching the terminal, so that anything which might
    # prompt for a password happens on an ordinary shell rather than
    # underneath a full-screen render.
    session = connect(
        host=args.host,
        agent_source=args.agent,
        timeout=args.timeout,
        multiplex=not args.no_multiplex,
    )
    try:
        wizard = Wizard(session, output_path,
                        name=getattr(args, "name", None),
                        uniq=getattr(args, "uniq", None))
        keyboard.open()
        try:
            _loop(session, keyboard, wizard)
        finally:
            # Local, instant, and cannot fail on a link that may already
            # be dead. Everything that talks to the brick comes after.
            keyboard.restore()
        _report(wizard)
        if wizard.blocked:
            raise LinkError(Diagnosis(
                summary="More than one input device carries that name",
                cause=wizard.blocked,
                checklist=(
                    "unplug the USB cable if the pad is also charging "
                    "from this brick",
                    "or switch the pad off, unplug it, and press PS to "
                    "bring it back over Bluetooth alone",
                    "then run `ev3ctl gamepad` again",
                ),
            ))
    finally:
        # The gamepad first, because it is this command's own state and
        # the agent will otherwise only close it at EOF; then the motors
        # and the link, by the same shutdown every other subcommand uses.
        try:
            session.gamepad_close()
        except Exception:
            pass
        session.shutdown()
    return 0


def _report(wizard):
    """What was captured, printed after the alternate screen is gone."""
    if wizard.blocked:
        # Not "no controller found" - two were, which is the problem.
        # The Diagnosis raised after this says what to do about it.
        return
    if wizard.device is None:
        console.print()
        console.print("[warn]No controller was found, so nothing was "
                      "captured.[/warn]")
        console.print()
        return
    device = wizard.device or {}
    source = device.get("identity_source")
    value = device.get("identity_value")
    console.print()
    if source == "uniq+name":
        console.print("[ok]Identified by[/ok] Uniq + Name " + str(value)
                      + "  [dim]the pair: Uniq is the controller, Name "
                      "is which of its three devices[/dim]")
    elif source == "uniq":
        console.print("[ok]Identified by[/ok] Uniq " + str(value)
                      + "  [dim]given on the command line[/dim]")
    elif source == "name":
        console.print("[warn]Identified by[/warn] Name " + repr(value)
                      + "  [dim]this device reports no Uniq, so the "
                      "fallback was used; Name can differ between "
                      "transports[/dim]")
    else:
        console.print("[warn]Identity field not reported by the "
                      "agent.[/warn]")
    console.print()
    console.print(ui.gamepad_summary_table(wizard))
    console.print()
    console.print(ui.gamepad_buttons_table(wizard))
    console.print()
    if wizard.written_to:
        console.print("[ok]Written[/ok] " + wizard.written_to)
    elif wizard.write_error:
        console.print("[fail]Not written[/fail] " + wizard.write_error)
    else:
        console.print("[warn]Aborted before the summary; nothing was "
                      "written.[/warn]")
    console.print()


def _loop(session, keyboard, wizard):
    link = session.link
    key_fd = keyboard.fileno()

    with Live(wizard.render(), console=console, screen=True,
              refresh_per_second=5, transient=False) as live:
        next_poll = 0.0
        next_render = 0.0
        while not wizard.quit:
            now = time.monotonic()

            if wizard.poll_id is not None and (
                    now - wizard.poll_sent_at > POLL_RETRY_AFTER_S):
                wizard.note("state poll timed out, retrying")
                wizard.poll_id = None

            if (wizard.device is not None and wizard.poll_id is None
                    and now >= next_poll):
                wizard.poll_sent_at = now
                wizard.poll_id = wizard.track(
                    session.send_gamepad_state(), "state")
                next_poll = now + POLL_INTERVAL_S

            ready, _, _ = select.select([link.stdout_fd, key_fd], [], [],
                                        SLICE_S)
            if link.stdout_fd in ready:
                for response in link.pump():
                    wizard.handle_response(response)
            if key_fd in ready:
                for key in keyboard.read():
                    wizard.handle_key(key)
                    if wizard.quit:
                        break

            wizard.tick(time.monotonic())
            if wizard.blocked:
                break

            complaint = link.drain_stderr()
            if complaint.strip():
                wizard.note("brick: " + " ".join(complaint.split()))

            now = time.monotonic()
            if now >= next_render:
                live.update(wizard.render())
                next_render = now + RENDER_INTERVAL_S
