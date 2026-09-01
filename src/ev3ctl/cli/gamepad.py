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

DEFAULT_OUTPUT = os.path.join("docs", "gamepad-mapping.json")

CONNECT = gamepad.CONNECT
REST = gamepad.REST
LEFT = gamepad.LEFT
RIGHT = gamepad.RIGHT
BUTTONS = gamepad.BUTTONS
SUMMARY = gamepad.SUMMARY
STEPS = gamepad.STEPS
RESETTING_STEPS = gamepad.RESETTING_STEPS
STICK_CONTROLS = gamepad.STICK_STEPS
TRIGGER_CONTROLS = gamepad.TRIGGER_STEPS


class Wizard(object):
    """Everything the frame is drawn from, and the step machine."""

    def __init__(self, session, output_path, name=None):
        self.session = session
        self.output_path = output_path
        self.name = name
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
        self.wrong_stick = False
        self.codes = {}
        if step in RESETTING_STEPS:
            self.reset_id = self.track(
                self.session.send_gamepad_reset_window(), "reset")
        if step == SUMMARY:
            self._write_mapping()

    def redo(self):
        """Start the current step again, forgetting what it found."""
        self.step_axes.pop(self.step, None)
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
        if not self.armed or not self.device:
            return
        if self.step == REST:
            self._tick_rest(now)
        elif self.step in STICK_CONTROLS:
            self._tick_stick()
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

    def _tick_stick(self):
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
            self.step_windows[key] = self.codes[key]
        self.enter(self.step + 1)

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

        Both event types are accepted. A D-pad on this pad is expected
        to arrive as hat axis movement rather than as EV_KEY presses, so
        insisting on EV_KEY would record nothing for four of the
        fifteen prompts and look like a broken controller.
        """
        if self.button_index >= len(gamepad.BUTTON_PROMPTS):
            return
        label = gamepad.BUTTON_PROMPTS[self.button_index]
        already = set((item[0], item[1], item[3]) for item in self.buttons)
        for key, entry in sorted(self.codes.items()):
            if entry["count"] == 0:
                continue
            value = entry["latest"]
            if key[0] == evdev_codes.EV_KEY:
                if value == 0:
                    continue
            elif key[0] == evdev_codes.EV_ABS:
                if value == 0 or key in self.claimed():
                    continue
            else:
                continue
            if (key[0], key[1], value) in already:
                continue
            self.buttons.append((key[0], key[1], label, value))
            self.button_index += 1
            return

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
                        name=getattr(args, "name", None))
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
