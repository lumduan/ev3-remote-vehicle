"""Tests for the gamepad wizard's pure logic, on both sides of the link.

The wizard cannot be tried out. It needs a charged controller, a brick,
and a person with two hands, and a wrong comparison in an advance gate
does not crash - it produces a step that waits forever while the
operator wonders which of the two of them is broken. These tests are the
only thing standing between that and a hardware session.

The brick's half is exercised by reading `agent/ev3_agent.py` as text and
exec-ing it in a throwaway namespace. That is not an import: the module
is never added to sys.modules, nothing under src/ can reach it, and the
one-way boundary in CLAUDE.md holds. It is done this way because the
/proc parser is the single most format-sensitive piece of code in the
feature and the brick is not always reachable.
"""

import ast
import json
import struct
from pathlib import Path

import pytest

from ev3ctl import evdev_codes, gamepad

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def agent():
    """The brick module's namespace, exec'd rather than imported."""
    namespace = {"__name__": "ev3_agent_under_test"}
    source = (ROOT / "agent" / "ev3_agent.py").read_text()
    exec(compile(source, "agent/ev3_agent.py", "exec"), namespace)
    return namespace


# The shape hid-sony actually produces: one controller, three input
# devices. Taken from the fields ROADMAP.md records for this pad.
THREE_DEVICES = '''\
I: Bus=0019 Vendor=0001 Product=0001 Version=0100
N: Name="EV3 Brick Buttons"
P: Phys=/dev/input/event0
S: Sysfs=/devices/platform/gpio_keys/input/input0
U: Uniq=
H: Handlers=kbd event0
B: EV=100003

I: Bus=0005 Vendor=054c Product=09cc Version=8100
N: Name="Wireless Controller"
P: Phys=00:17:ec:ed:46:29
S: Sysfs=/devices/virtual/misc/uhid/0005:054C:09CC.0004/input/input8
U: Uniq=00:22:68:f2:5c:b6
H: Handlers=event4 js0
B: EV=20000b
B: ABS=3003f

I: Bus=0005 Vendor=054c Product=09cc Version=8100
N: Name="Wireless Controller Touchpad"
P: Phys=00:17:ec:ed:46:29
U: Uniq=00:22:68:f2:5c:b6
H: Handlers=event5 mouse1
B: ABS=260800000000003

I: Bus=0005 Vendor=054c Product=09cc Version=8100
N: Name="Wireless Controller Motion Sensors"
P: Phys=00:17:ec:ed:46:29
U: Uniq=00:22:68:f2:5c:b6
H: Handlers=event6
B: ABS=7fff000000000000
'''

# The same pad arriving over Bluetooth and USB at once. Requirement: two
# exact-name matches must refuse to proceed, because the two transports
# use different HID report layouts.
TWO_TRANSPORTS = '''\
I: Bus=0005 Vendor=054c Product=09cc Version=8100
N: Name="Wireless Controller"
P: Phys=00:17:ec:ed:46:29
U: Uniq=00:22:68:f2:5c:b6
H: Handlers=event4 js0
B: ABS=3003f

I: Bus=0003 Vendor=054c Product=09cc Version=8111
N: Name="Wireless Controller"
P: Phys=usb-ohci-omap3.1-1.2/input0
U: Uniq=
H: Handlers=event7 js1
B: ABS=3003f
'''


# ---------------------------------------------------------------------
# Discovery, on the brick
# ---------------------------------------------------------------------

def test_parses_every_block(agent):
    blocks = agent["parse_input_devices"](THREE_DEVICES)
    assert len(blocks) == 4


def test_exact_name_match_finds_one_of_the_three(agent):
    """The whole reason discovery matches on equality, not substring.

    hid-sony creates three devices whose names all contain "Wireless
    Controller". A substring match returns all three on every run, and
    the ambiguity guard would then refuse to start every single time.
    """
    blocks = agent["parse_input_devices"](THREE_DEVICES)
    exact = [b for b in blocks if b["name"] == "Wireless Controller"]
    assert len(exact) == 1
    assert exact[0]["event"] == "event4"

    substring = [b for b in blocks
                 if "Wireless Controller" in (b["name"] or "")]
    assert len(substring) == 3


def test_two_transports_is_two_exact_matches(agent):
    """The case the ambiguity guard exists for still trips it."""
    blocks = agent["parse_input_devices"](TWO_TRANSPORTS)
    exact = [b for b in blocks if b["name"] == "Wireless Controller"]
    assert len(exact) == 2
    assert {b["bus"] for b in exact} == {0x05, 0x03}


def test_fields_are_read_off_the_block(agent):
    blocks = agent["parse_input_devices"](THREE_DEVICES)
    pad = [b for b in blocks if b["name"] == "Wireless Controller"][0]
    assert pad["phys"] == "00:17:ec:ed:46:29"
    assert pad["uniq"] == "00:22:68:f2:5c:b6"
    assert pad["bus"] == 0x05
    assert pad["vendor"] == 0x054C
    assert pad["abs_mask"] == "3003f"
    assert "js0" in pad["handlers"]


def test_empty_uniq_is_none_not_empty_string(agent):
    blocks = agent["parse_input_devices"](THREE_DEVICES)
    buttons = [b for b in blocks if b["name"] == "EV3 Brick Buttons"][0]
    assert buttons["uniq"] is None


def test_handler_without_an_event_node_is_none(agent):
    text = 'N: Name="Odd"\nH: Handlers=js0 mouse1 \n'
    block = agent["parse_input_devices"](text)[0]
    assert block["event"] is None


def test_garbage_lines_are_skipped_not_raised(agent):
    text = 'N: Name="Pad"\nthis is not a field\nH: Handlers=event2 \n'
    block = agent["parse_input_devices"](text)[0]
    assert block["name"] == "Pad"
    assert block["event"] == "event2"


def test_empty_file_yields_nothing(agent):
    assert agent["parse_input_devices"]("") == []
    assert agent["parse_input_devices"](None) == []


# ---------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------

@pytest.mark.parametrize("bus,phys,expected,agreement", [
    (0x05, "00:17:ec:ed:46:29", "bluetooth", "agree"),
    (0x03, "usb-ohci-omap3.1-1.2/input0", "usb", "agree"),
    (0x05, "usb-ohci-omap3.1-1.2/input0", "bluetooth", "disagree"),
    (0x05, "", "bluetooth", "bus-only"),
    (0x19, "00:17:ec:ed:46:29", "bluetooth", "phys-only"),
    (0x19, "/dev/input/event0", None, "unknown"),
])
def test_transport(agent, bus, phys, expected, agreement):
    assert agent["transport_of"](bus, phys) == (expected, agreement)


def test_bluetooth_phys_with_a_suffix_still_reads_as_bluetooth(agent):
    transport, _ = agent["transport_of"](None, "00:17:ec:ed:46:29/input0")
    assert transport == "bluetooth"


# ---------------------------------------------------------------------
# The 16-byte input_event
# ---------------------------------------------------------------------

EVENT = struct.Struct("=llHHi")


def test_event_struct_is_sixteen_bytes(agent):
    """Not 24. The native layout is 24 on any 64-bit development host.

    struct timeval is two 32-bit longs on the brick's 32-bit ARM kernel,
    so a record is 4 + 4 + 2 + 2 + 4. Parsing 16-byte records as 24-byte
    ones does not raise; it silently yields nonsense.
    """
    assert agent["EVENT_STRUCT"].size == 16
    assert struct.calcsize("=llHHi") == 16


def test_decodes_whole_records_and_keeps_the_tail(agent):
    whole = EVENT.pack(1, 2, 3, 0, 128) + EVENT.pack(1, 3, 3, 1, 200)
    partial = EVENT.pack(1, 4, 1, 304, 1)[:8]
    events, tail = agent["decode_events"](whole + partial)
    assert events == [(3, 0, 128), (3, 1, 200)]
    assert len(tail) == 8


def test_a_split_record_survives_being_rejoined(agent):
    packed = EVENT.pack(1, 4, 1, 304, 1)
    first, tail = agent["decode_events"](packed[:9])
    assert first == []
    second, rest = agent["decode_events"](tail + packed[9:])
    assert second == [(1, 304, 1)]
    assert rest == b""


def test_negative_values_survive_the_round_trip(agent):
    """ABS_HAT0Y is -1 for up. An unsigned field would read 4294967295."""
    events, _ = agent["decode_events"](EVENT.pack(1, 2, 3, 0x11, -1))
    assert events == [(3, 0x11, -1)]


# ---------------------------------------------------------------------
# The ioctl request number
# ---------------------------------------------------------------------

def test_eviocgabs_matches_the_kernel_macro(agent):
    """EVIOCGABS(code) = _IOR('E', 0x40 + code, struct input_absinfo)."""
    size = agent["ABSINFO_STRUCT"].size
    assert size == 24
    expected = (2 << 30) | (size << 16) | (ord("E") << 8) | 0x40
    assert agent["EVIOCGABS_BASE"] == expected
    assert agent["EVIOCGABS_BASE"] == 0x80184540
    # The code lands in the low byte, so adding it is the same as
    # recomputing the macro, for every axis code that exists.
    for code in (0, 1, 5, 0x11, agent["ABS_CODE_MAX"]):
        recomputed = (2 << 30) | (size << 16) | (ord("E") << 8) | (0x40 + code)
        assert agent["EVIOCGABS_BASE"] + code == recomputed


# ---------------------------------------------------------------------
# Accumulation, on the brick
# ---------------------------------------------------------------------

def counter(agent, values, seed=None):
    """Fold a list of values through the brick's accumulator."""
    reader = agent["GamepadReader"]()
    start = seed if seed is not None else values[0]
    reader._codes = {(3, 0): agent["_new_counter"](start)}
    for value in values:
        reader._accumulate(3, 0, value)
    return reader._codes[(3, 0)]


def test_accumulator_tracks_extremes_and_mean_inputs(agent):
    entry = counter(agent, [10, 20, 30], seed=10)
    assert entry["min"] == 10
    assert entry["max"] == 30
    assert entry["count"] == 3
    assert entry["sum"] == 60
    assert entry["latest"] == 30


def test_distinct_set_is_capped_and_says_so(agent):
    cap = agent["GAMEPAD_DISTINCT_CAP"]
    entry = counter(agent, list(range(cap + 20)), seed=0)
    assert len(entry["distinct"]) == cap
    assert entry["overflow"] is True


def test_reset_window_keeps_the_device_and_the_current_value(agent):
    reader = agent["GamepadReader"]()
    reader._fd = 3  # pretend it is open; reset must not need a device
    reader._codes = {(3, 0): agent["_new_counter"](128)}
    reader._accumulate(3, 0, 5)
    reader._accumulate(3, 0, 250)
    reader.reset_window()
    entry = reader._codes[(3, 0)]
    assert entry["latest"] == 250
    assert entry["min"] == 250 and entry["max"] == 250
    assert entry["count"] == 0 and entry["sum"] == 0
    assert entry["distinct"] == {250}
    assert reader._fd == 3


def test_state_before_open_is_refused(agent):
    reader = agent["GamepadReader"]()
    with pytest.raises(agent["CommandError"]) as caught:
        reader.state()
    assert caught.value.kind == "no_gamepad"


def test_gamepad_close_on_a_reader_that_never_opened(agent):
    """Teardown runs from the agent's finally on every path."""
    assert agent["GamepadReader"]().close() == {"closed": False}


def test_gamepad_commands_are_registered(agent):
    for name in ("gamepad_open", "gamepad_state", "gamepad_reset_window",
                 "gamepad_close"):
        assert name in agent["COMMANDS"]


def test_the_reader_thread_cannot_pet_the_motor_watchdog():
    """A waggled gamepad is not evidence that the host is still there.

    If the reader called the watchdog's keep-alive, the brick would look
    commanded while the link was dead, and the motor watchdog would stay
    quiet exactly when it is the only thing left to stop the motors.

    Checked over the parse tree rather than the text. The first version
    of this test searched the source for the method name and failed on
    its own docstring, which is the same mistake in miniature: a comment
    that mentions a call is not a call.
    """
    tree = ast.parse((ROOT / "agent" / "ev3_agent.py").read_text())
    reader = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "GamepadReader"
    )
    called = {
        node.func.attr for node in ast.walk(reader)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    }
    assert "touch" not in called


# ---------------------------------------------------------------------
# The abs bitmask, on the host
# ---------------------------------------------------------------------

def test_abs_mask_names_the_axes_this_pad_declares():
    """`B: ABS=3003f` is bits 0-5 and 16-17, per ROADMAP.md:99."""
    codes = gamepad.axis_codes_from_mask("3003f")
    assert codes == [0, 1, 2, 3, 4, 5, 16, 17]
    names = [evdev_codes.code_name(evdev_codes.EV_ABS, c) for c in codes]
    assert names == ["ABS_X", "ABS_Y", "ABS_Z", "ABS_RX", "ABS_RY",
                     "ABS_RZ", "ABS_HAT0X", "ABS_HAT0Y"]


def test_abs_mask_handles_multiple_groups():
    codes = gamepad.axis_codes_from_mask("1 0")
    assert codes == [32]


def test_abs_mask_of_nothing():
    assert gamepad.axis_codes_from_mask(None) == []
    assert gamepad.axis_codes_from_mask("") == []


# ---------------------------------------------------------------------
# Host-side rows
# ---------------------------------------------------------------------

def row(event_type, code, latest, low, high, count=1, total=None,
        distinct=2, interior=0, overflow=False):
    if total is None:
        total = latest * count
    return [event_type, code, latest, low, high, count, total,
            distinct, interior, overflow]


def test_columns_must_match_the_agent():
    assert gamepad.columns_match(list(gamepad.STATE_COLUMNS))
    assert not gamepad.columns_match(["type", "code"])
    assert not gamepad.columns_match(None)


def test_a_short_row_is_dropped_rather_than_padded():
    """A truncated row means the two sides disagree about the wire.

    Padding it with zeros would turn a protocol mismatch into a
    plausible-looking reading, which is the failure this project keeps
    writing rules against.
    """
    codes = gamepad.rows_to_codes([[3, 0, 1, 2]])
    assert codes == {}


# ---------------------------------------------------------------------
# Rest statistics
# ---------------------------------------------------------------------

def test_rest_mean_and_spread():
    codes = gamepad.rows_to_codes(
        [row(3, 0, 130, 126, 131, count=4, total=514)])
    report = gamepad.rest_report(codes)
    assert report[(3, 0)]["mean"] == pytest.approx(128.5)
    assert report[(3, 0)]["spread"] == 5


def test_an_axis_that_never_moved_reports_its_seeded_value():
    """A perfectly still stick still has a resting position.

    The agent seeds each axis from absinfo.value when it opens the
    device, so an axis with no events reports where it actually is
    rather than a zero it never sent.
    """
    entry = gamepad.rows_to_codes(
        [row(3, 0, 127, 127, 127, count=0, total=0)])[(3, 0)]
    assert gamepad.mean_value(entry) == 127.0
    assert gamepad.spread(entry) == 0


def test_suggested_deadzone_is_three_times_the_jitter():
    assert gamepad.suggested_deadzone(3) == 9
    assert gamepad.suggested_deadzone(0) == 0
    assert gamepad.suggested_deadzone(None) is None


# ---------------------------------------------------------------------
# The sweep gate
# ---------------------------------------------------------------------

STICK = {"minimum": 0, "maximum": 255, "fuzz": 0, "flat": 15,
         "resolution": 0, "value": 128}
HAT = {"minimum": -1, "maximum": 1, "fuzz": 0, "flat": 0,
       "resolution": 0, "value": 0}
TRIGGER = {"minimum": 0, "maximum": 255, "fuzz": 0, "flat": 0,
           "resolution": 0, "value": 0}


def test_a_full_sweep_passes():
    entry = gamepad.rows_to_codes([row(3, 0, 128, 2, 253)])[(3, 0)]
    assert gamepad.swept(entry, 128.0, 0, 255)


def test_a_nudge_does_not_pass():
    """The failure the driver-supplied range exists to prevent.

    Against a self-referential "observed range" this would be a full
    sweep of everything seen so far, and the step would advance on a
    twitch.
    """
    entry = gamepad.rows_to_codes([row(3, 0, 128, 120, 136)])[(3, 0)]
    assert not gamepad.swept(entry, 128.0, 0, 255)


def test_one_direction_only_does_not_pass():
    entry = gamepad.rows_to_codes([row(3, 0, 250, 128, 253)])[(3, 0)]
    assert not gamepad.swept(entry, 128.0, 0, 255)


def test_a_hat_is_rejected_however_far_it_swings():
    """A D-pad reaches both its limits the moment it is brushed.

    Its declared range is 2, so an 80 percent test passes trivially.
    Excluding it on the driver's declared range is what stops a brushed
    D-pad being named as a stick.
    """
    entry = gamepad.rows_to_codes([row(3, 0x11, 1, -1, 1)])[(3, 0x11)]
    low, high, source = gamepad.axis_range(HAT, entry)
    assert source == "driver"
    assert gamepad.too_coarse(low, high)
    assert not gamepad.swept(entry, 0.0, low, high)


def test_qualifying_axes_names_exactly_the_two_that_swept():
    codes = gamepad.rows_to_codes([
        row(3, 0, 128, 2, 253),      # swept
        row(3, 1, 128, 3, 250),      # swept
        row(3, 3, 128, 127, 129),    # barely moved
        row(3, 0x11, 1, -1, 1),      # D-pad brushed
    ])
    drivers = {(3, 0): STICK, (3, 1): STICK, (3, 3): STICK,
               (3, 0x11): HAT}
    rest = {(3, 0): {"mean": 128.0}, (3, 1): {"mean": 128.0},
            (3, 3): {"mean": 128.0}, (3, 0x11): {"mean": 0.0}}
    assert gamepad.qualifying_axes(codes, drivers, rest) == [(3, 0), (3, 1)]


def test_sweep_progress_reports_partial_travel():
    entry = gamepad.rows_to_codes([row(3, 0, 128, 128, 230)])[(3, 0)]
    up, down = gamepad.sweep_progress(entry, 128.0, 0, 255)
    assert up == pytest.approx(1.0)
    assert down == 0.0


def test_range_falls_back_to_observed_when_the_driver_will_not_say():
    entry = gamepad.rows_to_codes([row(3, 0, 128, 10, 240)])[(3, 0)]
    low, high, source = gamepad.axis_range(None, entry)
    assert (low, high, source) == (10, 240, "observed")


# ---------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------

def test_a_full_squeeze_and_release_passes():
    entry = gamepad.rows_to_codes([row(3, 2, 0, 0, 255)])[(3, 2)]
    assert gamepad.trigger_spanned(entry, 0, 255)


def test_a_half_squeeze_does_not_pass():
    entry = gamepad.rows_to_codes([row(3, 2, 0, 0, 120)])[(3, 2)]
    assert not gamepad.trigger_spanned(entry, 0, 255)


def test_a_trigger_never_released_does_not_pass():
    entry = gamepad.rows_to_codes([row(3, 2, 255, 200, 255)])[(3, 2)]
    assert not gamepad.trigger_spanned(entry, 0, 255)


def test_qualifying_triggers_excludes_axes_already_claimed():
    codes = gamepad.rows_to_codes([
        row(3, 2, 0, 0, 255),
        row(3, 0, 128, 0, 255),
    ])
    drivers = {(3, 2): TRIGGER, (3, 0): STICK}
    found = gamepad.qualifying_triggers(codes, drivers,
                                        exclude={(3, 0)})
    assert found == [(3, 2)]


def test_continuity_tells_analog_from_digital():
    """The finding that decides whether proportional throttle is possible.

    A trigger reporting only its two extremes is being delivered as a
    digital button, which rules it out.
    """
    analog = gamepad.rows_to_codes(
        [row(3, 2, 0, 0, 255, count=90, distinct=40, interior=38)])
    digital = gamepad.rows_to_codes(
        [row(3, 2, 0, 0, 255, count=6, distinct=2, interior=0)])
    coarse = gamepad.rows_to_codes(
        [row(3, 2, 0, 0, 255, count=9, distinct=5, interior=3)])
    assert gamepad.continuity(analog[(3, 2)]) == "continuous"
    assert gamepad.continuity(digital[(3, 2)]) == "extremes-only"
    assert gamepad.continuity(coarse[(3, 2)]) == "few"


def test_continuity_of_an_axis_that_never_moved_is_unknown():
    entry = gamepad.rows_to_codes(
        [row(3, 2, 0, 0, 0, count=0, distinct=1)])[(3, 2)]
    assert gamepad.continuity(entry) == "unknown"


# ---------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------

def test_button_names_and_the_xbox_aliases():
    """BTN_A is BTN_SOUTH, and on a DualShock that is not the X symbol.

    The letter aliases follow the Xbox face layout. Recording both the
    directional name and the label the operator was asked to press is
    what keeps the file readable by someone holding the controller.
    """
    assert evdev_codes.code_name(evdev_codes.EV_KEY, 0x130) == "BTN_SOUTH"
    assert evdev_codes.alias_name(evdev_codes.EV_KEY, 0x130) == "BTN_A"
    assert evdev_codes.code_name(evdev_codes.EV_KEY, 0x133) == "BTN_NORTH"
    assert evdev_codes.alias_name(evdev_codes.EV_KEY, 0x133) == "BTN_X"


def test_an_unknown_code_renders_as_its_number_not_a_guess():
    assert evdev_codes.code_name(evdev_codes.EV_KEY, 0x999) is None
    assert evdev_codes.label(evdev_codes.EV_KEY, 0x999) == "EV_KEY:2457"


def test_hats_are_recognised():
    assert evdev_codes.is_hat(evdev_codes.EV_ABS, 0x10)
    assert not evdev_codes.is_hat(evdev_codes.EV_ABS, 0x00)
    assert not evdev_codes.is_hat(evdev_codes.EV_KEY, 0x10)


# ---------------------------------------------------------------------
# The mapping document
# ---------------------------------------------------------------------

def build():
    codes = gamepad.rows_to_codes([
        row(3, 0, 128, 2, 253, count=40, total=5120),
        row(3, 1, 127, 3, 250, count=40, total=5080),
        row(3, 2, 0, 0, 255, count=90, distinct=40, interior=38),
    ])
    drivers = {(3, 0): STICK, (3, 1): STICK, (3, 2): TRIGGER}
    rest = {
        (3, 0): {"mean": 128.0, "spread": 3},
        (3, 1): {"mean": 127.0, "spread": 2},
        (3, 2): {"mean": 0.0, "spread": 0},
    }
    assignments = {(3, 0): "left_stick", (3, 1): "left_stick",
                   (3, 2): "l2"}
    buttons = [(evdev_codes.EV_KEY, 0x130, "Cross", 1),
               (evdev_codes.EV_ABS, 0x11, "D-pad up", -1)]
    return gamepad.build_mapping(
        device={"name": "Wireless Controller", "transport": "bluetooth",
                "uniq": "00:22:68:f2:5c:b6"},
        captured_at="2026-09-01T12:00:00+07:00",
        assignments=assignments, rest=rest, codes=codes, drivers=drivers,
        buttons=buttons, declared_axes=[0, 1, 2, 3, 4, 5, 16, 17])


def test_every_declared_axis_appears():
    document = build()
    assert [a["code"] for a in document["axes"]] == [0, 1, 2, 3, 4, 5,
                                                     16, 17]


def test_every_axis_carries_a_rest_mean_and_a_deadzone_or_says_it_did_not():
    document = build()
    measured = [a for a in document["axes"] if a["control"]]
    assert len(measured) == 3
    for axis in measured:
        assert axis["rest_mean"] is not None
        assert axis["suggested_deadzone"] is not None


def test_an_unswept_axis_is_null_rather_than_guessed():
    """The constraint that outranks convenience in this feature."""
    document = build()
    unseen = [a for a in document["axes"] if a["code"] == 3][0]
    assert unseen["control"] is None
    assert unseen["range_source"] == "unseen"
    assert unseen["rest_mean"] is None


def test_stick_axes_do_not_claim_to_know_which_is_horizontal():
    document = build()
    for axis in document["axes"]:
        assert axis["axis_role"] is None
    assert "cannot say which of the two is horizontal" in \
        document["axis_role_note"]


def test_the_deadzone_note_says_it_is_not_a_tuned_value():
    document = build()
    assert "not a tuned value" in document["deadzone_note"]


def test_a_trigger_carries_its_continuity_and_a_stick_does_not():
    document = build()
    trigger = [a for a in document["axes"] if a["code"] == 2][0]
    stick = [a for a in document["axes"] if a["code"] == 0][0]
    assert trigger["continuous"] == "continuous"
    assert trigger["interior_values_seen"] == 38
    assert "continuous" not in stick


def test_the_driver_range_travels_with_the_measurement():
    document = build()
    stick = [a for a in document["axes"] if a["code"] == 0][0]
    assert stick["range_source"] == "driver"
    assert stick["driver_min"] == 0 and stick["driver_max"] == 255
    assert stick["driver_flat"] == 15


def test_buttons_record_the_label_the_operator_was_asked_for():
    document = build()
    cross = document["buttons"][0]
    assert cross["label"] == "Cross"
    assert cross["name"] == "BTN_SOUTH"
    assert cross["alias"] == "BTN_A"
    assert cross["type"] == "EV_KEY"


def test_a_dpad_arriving_as_a_hat_axis_keeps_its_direction():
    """Up and down are the same code with opposite signs.

    On this pad the D-pad is expected on ABS_HAT0X/Y rather than as
    EV_KEY presses, so recording the code alone would merge two prompts
    into one entry.
    """
    document = build()
    up = document["buttons"][1]
    assert up["type"] == "EV_ABS"
    assert up["name"] == "ABS_HAT0Y"
    assert up["value"] == -1


def test_the_document_is_json_serialisable():
    json.dumps(build())


# ---------------------------------------------------------------------
# The step machine
#
# Two of these are regressions. Both were found by driving the wizard
# against a scripted controller rather than by reading it.
# ---------------------------------------------------------------------

class FakeSession(object):
    """Just enough of Session to run the step machine against."""

    hostname = "ev3dev"
    host = "robot@ev3dev.local"
    kernel = "4.14.117-ev3dev-2.3.5-ev3"

    def __init__(self):
        self.n = 0
        self.sent = []

    def _next(self, what):
        self.n += 1
        self.sent.append((self.n, what))
        return self.n

    def send_gamepad_open(self, name=None):
        return self._next("open")

    def send_gamepad_reset_window(self):
        return self._next("reset")

    def send_gamepad_state(self):
        return self._next("state")


def wizard(tmp_path):
    from ev3ctl.cli.gamepad import Wizard
    return FakeSession(), Wizard(
        FakeSession(), str(tmp_path / "gamepad-mapping.json"))


def test_ambiguity_refuses_even_if_the_reply_was_not_tracked(tmp_path):
    """Refusing must not depend on the bookkeeping having kept up.

    The first version keyed this off which request the reply belonged
    to, so an untracked reply downgraded "two devices carry this name"
    to a passing note and the wizard carried on retrying. Only
    gamepad_open can produce this kind, so the kind alone decides.
    """
    _, wiz = wizard(tmp_path)
    wiz.handle_response({
        "id": 999, "ok": False, "kind": "ambiguous_gamepad",
        "error": "2 devices are named 'Wireless Controller'",
    })
    assert wiz.blocked
    wiz.tick(2000.0)
    assert wiz.device is None


def test_only_one_open_is_ever_in_flight(tmp_path):
    """A second open would close the reader the first had just started.

    Every gamepad_open closes whatever is open before reopening, so
    retrying on a timer while one was still travelling tore down the
    thread on exactly the slow brick that caused the retry.
    """
    from ev3ctl.cli import gamepad as command
    session, wiz = wizard(tmp_path)
    wiz.session = session

    wiz.tick(2000.0)
    wiz.tick(2000.5)
    wiz.tick(2000.9)
    assert [k for _, k in session.sent].count("open") == 1

    # Only once the retry window has passed does a second one go out.
    wiz.tick(2000.0 + command.OPEN_RETRY_AFTER_S + 0.1)
    assert [k for _, k in session.sent].count("open") == 2


def test_a_step_is_not_judged_until_its_reset_is_acknowledged(tmp_path):
    """The window boundary is the reset, and it happens on the brick.

    A state reply that left the brick before the reset landed describes
    the previous step, so it must not count toward this one.
    """
    session, wiz = wizard(tmp_path)
    wiz.session = session
    wiz.enter(gamepad.LEFT)
    assert wiz.armed is False

    stale = wiz.track(session.send_gamepad_state(), "state")
    wiz.handle_response({"id": stale, "ok": True, "result": {
        "present": True, "device_gone": False, "total_events": 9,
        "rows": [row(3, 0, 128, 2, 253)]}})
    assert wiz.codes == {}

    wiz.handle_response({"id": wiz.reset_id, "ok": True,
                         "result": {"reset": True}})
    assert wiz.armed is True
