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


# Verbatim from this brick on 2026-09-01, with the controller connected
# over Bluetooth. Not a hypothetical: these are the exact three blocks
# hid-sony produces for one DualShock 4, with the real capability masks.
# The EV3's own buttons are prepended as they appear in the same file.
# The S: Sysfs lines are omitted: nothing here reads them and the real
# ones run past the line limit.
THREE_DEVICES = '''\
I: Bus=0019 Vendor=0001 Product=0001 Version=0100
N: Name="EV3 Brick Buttons"
P: Phys=/dev/input/event0
S: Sysfs=/devices/platform/gpio_keys/input/input0
U: Uniq=
H: Handlers=kbd event0
B: EV=100003
B: KEY=1680 0 0 10000000

I: Bus=0005 Vendor=054c Product=09cc Version=8100
N: Name="Wireless Controller Touchpad"
P: Phys=00:17:ec:ed:46:29
U: Uniq=00:22:68:f2:5c:b6
H: Handlers=event2
B: PROP=5
B: EV=b
B: KEY=2420 0 10000 0 0 0 0 0 0 0 0
B: ABS=2608000 0

I: Bus=0005 Vendor=054c Product=09cc Version=8100
N: Name="Wireless Controller Motion Sensors"
P: Phys=00:17:ec:ed:46:29
U: Uniq=00:22:68:f2:5c:b6
H: Handlers=event3
B: PROP=40
B: EV=19
B: ABS=3f
B: MSC=20

I: Bus=0005 Vendor=054c Product=09cc Version=8100
N: Name="Wireless Controller"
P: Phys=00:17:ec:ed:46:29
U: Uniq=00:22:68:f2:5c:b6
H: Handlers=event4
B: PROP=0
B: EV=20001b
B: KEY=7fdb0000 0 0 0 0 0 0 0 0 0
B: ABS=3003f
B: MSC=10
B: FF=1 7030000 0 0
'''

# One controller's gamepad function plus its touchpad, as a template for
# building multi-controller fixtures.
ONE_PAD = '''\
I: Bus=0005 Vendor=054c Product=09cc Version=8100
N: Name={name}
P: Phys=00:17:ec:ed:46:29
U: Uniq={uniq}
H: Handlers={event}
B: KEY=7fdb0000 0 0 0 0 0 0 0 0 0
B: ABS=3003f

I: Bus=0005 Vendor=054c Product=09cc Version=8100
N: Name="Wireless Controller Touchpad"
P: Phys=00:17:ec:ed:46:29
U: Uniq={uniq}
H: Handlers={touchpad}
B: KEY=2420 0 10000 0 0 0 0 0 0 0 0
B: ABS=2608000 0

'''


def pad(uniq, event, touchpad, name='"Wireless Controller"'):
    return ONE_PAD.format(uniq=uniq, event=event, touchpad=touchpad,
                          name=name)


# Two physically separate controllers of the same model. Same Name,
# different Uniq: the ambiguity that actually matters, because picking
# either would be picking one at random.
TWO_CONTROLLERS = (pad("00:22:68:f2:5c:b6", "event4", "event2")
                   + pad("00:22:68:aa:bb:cc", "event9", "event7"))


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


# ---------------------------------------------------------------------
# Identity: Uniq, with Name as the fallback
# ---------------------------------------------------------------------

def select(agent, text, name="Wireless Controller", uniq=None):
    blocks = agent["parse_input_devices"](text)
    return agent["select_gamepad"](blocks, name, uniq)


def test_identity_is_the_uniq_and_name_pair(agent):
    """Neither field alone identifies one function of one controller.

    All three of hid-sony's devices carry the same Uniq, so Uniq alone
    would return three and call every run ambiguous. Name alone would
    not tell two controllers of the same model apart.
    """
    chosen, source, value = select(agent, THREE_DEVICES)
    assert source == "uniq+name"
    assert value == "00:22:68:f2:5c:b6 / Wireless Controller"
    assert len(chosen) == 1
    assert chosen[0]["event"] == "event4"


def test_btn_south_separates_the_pad_from_its_two_siblings(agent):
    """The real KEY masks, read off this brick on 2026-09-01.

    An earlier version tested for a `js` handler. There is no js node on
    this brick and joydev is not loaded, so that test never fired.
    """
    blocks = agent["parse_input_devices"](THREE_DEVICES)
    declares = {b["name"]: agent["mask_has_bit"](b["key_mask"], 0x130)
                for b in blocks}
    assert declares["Wireless Controller"] is True
    assert declares["Wireless Controller Touchpad"] is False
    assert declares["Wireless Controller Motion Sensors"] is False


def test_the_mask_reader_counts_words_not_characters(agent):
    """%lx prints no leading zeros, so a word can be short.

    BTN_SOUTH is bit 304, which is bit 16 of word 9. The gamepad's mask
    has exactly ten words, and the leading one is only eight characters.
    """
    has = agent["mask_has_bit"]
    assert has("7fdb0000 0 0 0 0 0 0 0 0 0", 0x130) is True
    assert has("7fdb0000 0 0 0 0", 0x130) is False, "too few words"
    assert has("1 0", 32) is True
    assert has("1", 0) is True
    assert has(None, 0x130) is False
    assert has("", 0x130) is False


def test_same_uniq_with_different_names_is_one_controller(agent):
    """The ordinary case, and it must not be refused.

    This is what the hardware does on every connection: one controller,
    three functions, one Uniq, three Names.
    """
    blocks = agent["parse_input_devices"](THREE_DEVICES)
    chosen, _, _ = select(agent, THREE_DEVICES)
    assert agent["rival_controllers"](chosen) == []
    assert len({b["uniq"] for b in blocks if b["uniq"]}) == 1


def test_same_name_with_different_uniq_is_two_controllers(agent):
    """The ambiguity guard fires here and nowhere else."""
    chosen, source, _ = select(agent, TWO_CONTROLLERS)
    assert [b["event"] for b in chosen] == ["event4", "event9"]
    rivals = agent["rival_controllers"](chosen)
    assert len(rivals) == 2
    assert {b["uniq"] for b in rivals} == {"00:22:68:f2:5c:b6",
                                           "00:22:68:aa:bb:cc"}


def test_name_is_the_fallback_when_uniq_is_empty(agent):
    text = THREE_DEVICES.replace("U: Uniq=00:22:68:f2:5c:b6", "U: Uniq=")
    chosen, source, value = select(agent, text)
    assert source == "name"
    assert value == "Wireless Controller"
    assert len(chosen) == 1


def test_an_explicit_uniq_still_narrows_to_the_gamepad(agent):
    """--uniq picks the controller; BTN_SOUTH picks its gamepad half.

    Case-insensitive: bluetoothctl prints the address upper-case and
    /proc prints it lower-case, for the same controller.
    """
    chosen, source, value = select(
        agent, THREE_DEVICES, name="something else",
        uniq="00:22:68:F2:5C:B6")
    assert source == "uniq"
    assert [b["event"] for b in chosen] == ["event4"]


def test_a_device_with_no_event_node_is_never_a_candidate(agent):
    text = 'N: Name="Wireless Controller"\nU: Uniq=aa\nH: Handlers=js0\n'
    chosen, _, _ = select(agent, text)
    assert chosen == []


def test_fields_are_read_off_the_block(agent):
    blocks = agent["parse_input_devices"](THREE_DEVICES)
    pad = [b for b in blocks if b["name"] == "Wireless Controller"][0]
    assert pad["phys"] == "00:17:ec:ed:46:29"
    assert pad["uniq"] == "00:22:68:f2:5c:b6"
    assert pad["bus"] == 0x05
    assert pad["vendor"] == 0x054C
    assert pad["abs_mask"] == "3003f"
    assert pad["key_mask"] == "7fdb0000 0 0 0 0 0 0 0 0 0"


def test_there_is_no_joystick_handler_on_this_brick(agent):
    """joydev is not loaded here, so no js node is ever created.

    /dev/input/ holds event0 to event4 and by-path, and `lsmod` has no
    joydev entry. Recorded as a test because an earlier version of the
    device discovery leaned on a `js` handler to tell the gamepad from
    its two siblings, and that test could never have fired.
    """
    blocks = agent["parse_input_devices"](THREE_DEVICES)
    for block in blocks:
        for handler in block["handlers"]:
            assert not handler.startswith("js"), block["name"]


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
    orientation = {(3, 0): "horizontal", (3, 1): "vertical"}
    polarity = {(3, 0): "right", (3, 1): "down"}
    hold_deviations = {
        (3, 0): {"right_hold": 122.0, "up_hold": 1.0},
        (3, 1): {"right_hold": 2.0, "up_hold": -124.0},
    }
    return gamepad.build_mapping(
        device={"name": "Wireless Controller", "transport": "bluetooth",
                "uniq": "00:22:68:f2:5c:b6", "identity_source": "uniq"},
        captured_at="2026-09-01T12:00:00+07:00",
        assignments=assignments, rest=rest, codes=codes, drivers=drivers,
        buttons=buttons, declared_axes=[0, 1, 2, 3, 4, 5, 16, 17],
        orientation=orientation, polarity=polarity,
        hold_deviations=hold_deviations)


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


def test_stick_axes_carry_a_measured_orientation_and_polarity():
    """What the circular sweep could not say, the holds now do."""
    document = build()
    for code in (0, 1):
        axis = [a for a in document["axes"] if a["code"] == code][0]
        assert axis["orientation"] in ("horizontal", "vertical")
        assert axis["positive_direction"] in ("right", "left", "up",
                                              "down")
    assert "axis_role" not in document
    assert "axis_role_note" not in document
    assert "measured, not assumed" in document["orientation_note"]


def test_an_axis_no_hold_named_still_carries_no_orientation():
    document = build()
    trigger = [a for a in document["axes"] if a["code"] == 2][0]
    assert trigger["orientation"] is None
    assert trigger["positive_direction"] is None


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


# ---------------------------------------------------------------------
# The directional holds
# ---------------------------------------------------------------------

PAIR = ((3, 0), (3, 1))
REST_CENTRED = {(3, 0): {"mean": 128.0, "spread": 3},
                (3, 1): {"mean": 128.0, "spread": 2}}
DRIVERS_STICK = {(3, 0): STICK, (3, 1): STICK}


def held(x_min, x_max, y_min, y_max):
    """A window in which the stick was pushed and held somewhere."""
    return gamepad.rows_to_codes([
        row(3, 0, x_max, x_min, x_max, count=5),
        row(3, 1, y_max, y_min, y_max, count=5),
    ])


def test_deviation_is_the_furthest_excursion_with_its_sign():
    entry = gamepad.rows_to_codes([row(3, 0, 250, 126, 250)])[(3, 0)]
    assert gamepad.deviation(entry, 128.0) == 122.0
    entry = gamepad.rows_to_codes([row(3, 0, 4, 4, 130)])[(3, 0)]
    assert gamepad.deviation(entry, 128.0) == -124.0


def test_a_clean_push_right_names_the_horizontal_axis():
    codes = held(128, 250, 126, 130)
    outcome = gamepad.resolve_hold(PAIR, codes, REST_CENTRED,
                                   DRIVERS_STICK)
    assert outcome["verdict"] == "ok"
    assert outcome["chosen"] == (3, 0)
    key, orientation, direction = gamepad.hold_outcome(
        gamepad.HOLD_RIGHT, outcome)
    assert (key, orientation, direction) == ((3, 0), "horizontal", "right")


def test_polarity_follows_the_sign_and_is_not_assumed():
    """A controller whose X counts up to the left is recorded that way."""
    codes = held(6, 128, 126, 130)
    outcome = gamepad.resolve_hold(PAIR, codes, REST_CENTRED,
                                   DRIVERS_STICK)
    assert outcome["verdict"] == "ok"
    key, orientation, direction = gamepad.hold_outcome(
        gamepad.HOLD_RIGHT, outcome)
    assert (orientation, direction) == ("horizontal", "left")


def test_up_names_the_vertical_axis_and_the_direction_measured():
    """Pushing up drove Y down, so positive Y means down on this pad.

    That is the usual evdev convention, and the wizard arrives at it by
    watching rather than by knowing it. A controller wired the other way
    would come out of the same code as "up" and be recorded as such.
    """
    codes = held(126, 130, 4, 128)
    outcome = gamepad.resolve_hold(PAIR, codes, REST_CENTRED,
                                   DRIVERS_STICK)
    key, orientation, direction = gamepad.hold_outcome(
        gamepad.HOLD_UP, outcome)
    assert (key, orientation, direction) == ((3, 1), "vertical", "down")
    assert gamepad.matches_evdev_convention(orientation, direction) is True


def test_a_pad_that_counts_the_other_way_is_recorded_that_way():
    codes = held(126, 130, 128, 252)
    outcome = gamepad.resolve_hold(PAIR, codes, REST_CENTRED,
                                   DRIVERS_STICK)
    _, orientation, direction = gamepad.hold_outcome(
        gamepad.HOLD_UP, outcome)
    assert direction == "up"
    assert gamepad.matches_evdev_convention(orientation, direction) is False


def test_a_diagonal_push_is_rejected():
    """Both axes moved a similar amount, so neither can be named.

    Accepting this would put a coin flip in the file and present it as a
    measurement, which is the failure the whole wizard exists to avoid.
    """
    codes = held(128, 250, 128, 240)
    outcome = gamepad.resolve_hold(PAIR, codes, REST_CENTRED,
                                   DRIVERS_STICK)
    assert outcome["verdict"] == "diagonal"
    assert outcome["ratio"] < gamepad.HOLD_RATIO


def test_exactly_three_times_is_accepted_and_just_under_is_not():
    rest = {(3, 0): {"mean": 0.0}, (3, 1): {"mean": 0.0}}
    drivers = {(3, 0): TRIGGER, (3, 1): TRIGGER}
    at = gamepad.resolve_hold(
        PAIR, held(0, 150, 0, 50), rest, drivers)
    assert at["ratio"] == pytest.approx(3.0)
    assert at["verdict"] == "ok"
    under = gamepad.resolve_hold(
        PAIR, held(0, 150, 0, 51), rest, drivers)
    assert under["verdict"] == "diagonal"


def test_an_untouched_other_axis_is_the_cleanest_result_not_an_error():
    """Ratio is undefined, not zero, and must not divide by zero."""
    codes = held(128, 250, 128, 128)
    outcome = gamepad.resolve_hold(PAIR, codes, REST_CENTRED,
                                   DRIVERS_STICK)
    assert outcome["ratio"] is None
    assert outcome["verdict"] == "ok"


def test_a_stick_barely_moved_is_soft_not_a_clean_lead():
    """Near-zero is still three times nearer-zero.

    Without a floor on how far the intended axis travelled, a hold in
    which the operator touched nothing would pass on noise alone.
    """
    codes = held(128, 134, 128, 129)
    outcome = gamepad.resolve_hold(PAIR, codes, REST_CENTRED,
                                   DRIVERS_STICK)
    assert outcome["verdict"] == "soft"


def test_no_rest_measurement_means_no_verdict():
    outcome = gamepad.resolve_hold(
        PAIR, held(128, 250, 126, 130), {}, DRIVERS_STICK)
    assert outcome["verdict"] == "unknown"


def test_reach_is_measured_against_the_driver_limit():
    assert gamepad.reach(127.0, 128.0, 0, 255) == pytest.approx(1.0)
    assert gamepad.reach(-64.0, 128.0, 0, 255) == pytest.approx(0.5)
    assert gamepad.reach(None, 128.0, 0, 255) == 0.0


def test_the_evdev_convention_is_recorded_not_consulted():
    """X up to the right, Y up downward. A data point, nothing more."""
    assert gamepad.matches_evdev_convention("horizontal", "right") is True
    assert gamepad.matches_evdev_convention("horizontal", "left") is False
    assert gamepad.matches_evdev_convention("vertical", "down") is True
    assert gamepad.matches_evdev_convention("vertical", "up") is False
    assert gamepad.matches_evdev_convention(None, None) is None


def test_the_mapping_records_agreement_with_the_convention():
    document = build()
    x = [a for a in document["axes"] if a["code"] == 0][0]
    y = [a for a in document["axes"] if a["code"] == 1][0]
    assert x["matches_evdev_convention"] is True
    assert y["matches_evdev_convention"] is True
    assert "data point and nothing more" in \
        document["evdev_convention_note"]


def test_hold_deviations_are_kept_so_the_decision_can_be_rechecked():
    document = build()
    x = [a for a in document["axes"] if a["code"] == 0][0]
    assert x["hold_deviations"] == {"right_hold": 122.0, "up_hold": 1.0}


# ---------------------------------------------------------------------
# The holds inside the step machine
# ---------------------------------------------------------------------

DEVICE = {
    "name": "Wireless Controller", "transport": "bluetooth",
    "transport_agreement": "agree", "bus": 0x05,
    "phys": "00:17:ec:ed:46:29", "uniq": "00:22:68:f2:5c:b6",
    "identity_source": "uniq", "identity_value": "00:22:68:f2:5c:b6",
    "event": "event4", "path": "/dev/input/event4", "abs_mask": "3003f",
    "absinfo": {"0": STICK, "1": STICK},
    "columns": list(gamepad.STATE_COLUMNS),
}


def at_left_sweep(tmp_path):
    """A wizard sitting at step 2, rest measured, ready to sweep."""
    from ev3ctl.cli.gamepad import Wizard
    session = FakeSession()
    wiz = Wizard(session, str(tmp_path / "m.json"))
    wiz.session = session
    wiz.tick(1000.0)
    wiz.handle_response({"id": session.sent[-1][0], "ok": True,
                         "result": DEVICE})
    _ack_reset(wiz)
    _deliver(wiz, session, [row(3, 0, 128, 126, 131, 4, 514),
                            row(3, 1, 128, 126, 130, 4, 512)])
    wiz.tick(1000.0)
    wiz.tick(1004.0)
    _ack_reset(wiz)
    return session, wiz


def _ack_reset(wiz):
    wiz.handle_response({"id": wiz.reset_id, "ok": True,
                         "result": {"reset": True}})


def _deliver(wiz, session, rows):
    request_id = wiz.track(session.send_gamepad_state(), "state")
    wiz.handle_response({"id": request_id, "ok": True, "result": {
        "present": True, "device_gone": False, "total_events": 50,
        "rows": rows}})


def test_a_sweep_no_longer_advances_the_step(tmp_path):
    """It moves to the hold that answers what the sweep cannot."""
    session, wiz = at_left_sweep(tmp_path)
    _deliver(wiz, session, [row(3, 0, 128, 2, 253),
                            row(3, 1, 128, 3, 250)])
    wiz.tick(1004.0)
    assert wiz.step == gamepad.LEFT
    assert wiz.phase == gamepad.HOLD_RIGHT
    assert set(wiz.step_axes[gamepad.LEFT]) == {(3, 0), (3, 1)}
    assert "RIGHT" in wiz.instruction()


def test_a_clean_hold_names_the_axis_and_moves_to_the_second_hold(tmp_path):
    session, wiz = at_left_sweep(tmp_path)
    _deliver(wiz, session, [row(3, 0, 128, 2, 253),
                            row(3, 1, 128, 3, 250)])
    wiz.tick(1004.0)
    _ack_reset(wiz)
    _deliver(wiz, session, [row(3, 0, 250, 128, 250),
                            row(3, 1, 129, 127, 130)])
    wiz.tick(1004.0)
    wiz.tick(1005.2)
    assert wiz.phase == gamepad.HOLD_UP
    assert wiz.orientation[(3, 0)] == "horizontal"
    assert wiz.polarity[(3, 0)] == "right"


def test_a_diagonal_is_retried_without_losing_the_sweep(tmp_path):
    """The pair was measured properly; only the push needs redoing."""
    session, wiz = at_left_sweep(tmp_path)
    _deliver(wiz, session, [row(3, 0, 128, 2, 253),
                            row(3, 1, 128, 3, 250)])
    wiz.tick(1004.0)
    _ack_reset(wiz)
    _deliver(wiz, session, [row(3, 0, 250, 128, 250),
                            row(3, 1, 240, 128, 240)])
    wiz.tick(1004.0)
    wiz.tick(1005.2)
    assert wiz.phase == gamepad.HOLD_RIGHT
    assert wiz.step_axes[gamepad.LEFT], "the sweep result survives"
    assert wiz.orientation == {}
    assert "diagonal" in wiz.last_error


def test_pushing_sideways_again_during_the_up_hold_is_refused(tmp_path):
    """Otherwise one axis would be named horizontal and vertical both.

    Naming it twice would also leave the other axis of the pair with no
    orientation at all, so the file would contradict itself and have a
    hole in it.
    """
    session, wiz = at_left_sweep(tmp_path)
    _deliver(wiz, session, [row(3, 0, 128, 2, 253),
                            row(3, 1, 128, 3, 250)])
    wiz.tick(1004.0)
    _ack_reset(wiz)
    _deliver(wiz, session, [row(3, 0, 250, 128, 250),
                            row(3, 1, 129, 127, 130)])
    wiz.tick(1004.0)
    wiz.tick(1005.2)
    assert wiz.phase == gamepad.HOLD_UP

    _ack_reset(wiz)
    _deliver(wiz, session, [row(3, 0, 4, 4, 128),
                            row(3, 1, 129, 127, 130)])
    wiz.tick(1005.2)
    wiz.tick(1006.4)
    assert wiz.phase == gamepad.HOLD_UP
    assert (3, 1) not in wiz.orientation
    assert wiz.orientation[(3, 0)] == "horizontal"


def test_redo_inside_a_hold_keeps_the_sweep(tmp_path):
    session, wiz = at_left_sweep(tmp_path)
    _deliver(wiz, session, [row(3, 0, 128, 2, 253),
                            row(3, 1, 128, 3, 250)])
    wiz.tick(1004.0)
    pair = dict(wiz.step_axes)
    wiz.handle_key("r")
    assert wiz.step_axes == pair
    assert wiz.phase == gamepad.HOLD_RIGHT
    assert wiz.armed is False, "a redo re-opens the window"


# ---------------------------------------------------------------------
# The D-pad is a hat, not four buttons
# ---------------------------------------------------------------------

def test_the_dpad_axes_are_declared_by_this_hardware():
    """ABS=3003f carries bits 16 and 17, so the D-pad is a hat."""
    codes = gamepad.axis_codes_from_mask("3003f")
    assert 16 in codes and 17 in codes
    assert evdev_codes.code_name(evdev_codes.EV_ABS, 16) == "ABS_HAT0X"
    assert evdev_codes.code_name(evdev_codes.EV_ABS, 17) == "ABS_HAT0Y"
    assert evdev_codes.is_hat(evdev_codes.EV_ABS, 17)


def test_a_hat_press_is_read_from_the_window_not_the_latest_value():
    """A press and release inside one 200 ms poll is back at zero.

    Reading `latest` would record nothing for a normal D-pad tap. The
    window's extremes still carry it.
    """
    tapped = gamepad.rows_to_codes(
        [row(3, 0x11, 0, -1, 0, count=2)])[(3, 0x11)]
    assert tapped["latest"] == 0
    assert gamepad.press_value((3, 0x11), tapped) == -1


def test_up_and_down_are_the_same_hat_code_with_opposite_signs():
    up = gamepad.rows_to_codes([row(3, 0x11, -1, -1, 0, count=2)])[(3, 0x11)]
    down = gamepad.rows_to_codes([row(3, 0x11, 1, 0, 1, count=2)])[(3, 0x11)]
    assert gamepad.press_value((3, 0x11), up) == -1
    assert gamepad.press_value((3, 0x11), down) == 1


def test_a_button_press_is_also_read_from_the_window():
    tapped = gamepad.rows_to_codes(
        [row(1, 0x130, 0, 0, 1, count=2)])[(1, 0x130)]
    assert gamepad.press_value((1, 0x130), tapped) == 1


def test_an_untouched_code_is_not_a_press():
    idle = gamepad.rows_to_codes(
        [row(3, 0x11, 0, 0, 0, count=0)])[(3, 0x11)]
    assert gamepad.press_value((3, 0x11), idle) is None
    quiet = gamepad.rows_to_codes(
        [row(1, 0x130, 0, 0, 0, count=2)])[(1, 0x130)]
    assert gamepad.press_value((1, 0x130), quiet) is None


def test_step_six_records_all_four_dpad_directions(tmp_path):
    """Four prompts, two hat axes, and the window reset that allows it.

    Without a fresh window per prompt the hat accumulates: up leaves -1
    in the minimum and down leaves +1 in the maximum, so the third and
    fourth prompts would find nothing new on either axis.
    """
    from ev3ctl.cli.gamepad import Wizard
    session = FakeSession()
    wiz = Wizard(session, str(tmp_path / "m.json"))
    wiz.session = session
    wiz.step = gamepad.BUTTONS
    wiz.armed = True
    wiz.button_index = gamepad.BUTTON_PROMPTS.index("D-pad up")

    presses = [(0x11, -1), (0x11, 1), (0x10, -1), (0x10, 1)]
    for code, value in presses:
        wiz.codes = gamepad.rows_to_codes([
            row(3, code, 0, min(0, value), max(0, value), count=2)])
        wiz.armed = True
        wiz._tick_buttons()

    recorded = [(b[1], b[3], b[2]) for b in wiz.buttons]
    assert recorded == [
        (0x11, -1, "D-pad up"), (0x11, 1, "D-pad down"),
        (0x10, -1, "D-pad left"), (0x10, 1, "D-pad right"),
    ]
    assert all(b[0] == evdev_codes.EV_ABS for b in wiz.buttons)


def test_a_claimed_stick_axis_is_never_recorded_as_a_button(tmp_path):
    from ev3ctl.cli.gamepad import Wizard
    session = FakeSession()
    wiz = Wizard(session, str(tmp_path / "m.json"))
    wiz.session = session
    wiz.step = gamepad.BUTTONS
    wiz.armed = True
    wiz.step_axes[gamepad.LEFT] = ((3, 0), (3, 1))
    wiz.codes = gamepad.rows_to_codes([row(3, 0, 200, 128, 200, count=5)])
    wiz._tick_buttons()
    assert wiz.buttons == []


# ---------------------------------------------------------------------
# The two directions are not the same size
# ---------------------------------------------------------------------

def test_rest_to_min_and_max_are_recorded_separately():
    """Measured here: ABS_Y rests at 115 of 0-255, so 115 against 140.

    A consumer dividing both directions by one symmetric figure makes
    one about 22 percent stronger than the other.
    """
    codes = gamepad.rows_to_codes(
        [row(3, 1, 115, 3, 250, count=40, total=4600)])
    rest = {(3, 1): {"mean": 115.0, "spread": 1}}
    document = gamepad.build_mapping(
        device={}, captured_at="x", assignments={(3, 1): "left_stick"},
        rest=rest, codes=codes, drivers={(3, 1): STICK}, buttons=[])
    axis = document["axes"][0]
    assert axis["rest_to_min"] == 115.0
    assert axis["rest_to_max"] == 140.0
    assert axis["rest_to_max"] > axis["rest_to_min"]
    ratio = axis["rest_to_max"] / axis["rest_to_min"]
    assert round((ratio - 1) * 100) == 22


def test_the_file_says_why_the_two_directions_matter():
    document = build()
    assert "normalise each direction against its own measured extent" \
        in document["asymmetry_note"].lower()


def test_a_centred_axis_has_equal_travel_both_ways():
    codes = gamepad.rows_to_codes([row(3, 0, 128, 3, 250, count=4, total=512)])
    document = gamepad.build_mapping(
        device={}, captured_at="x", assignments={(3, 0): "left_stick"},
        rest={(3, 0): {"mean": 128.0, "spread": 2}}, codes=codes,
        drivers={(3, 0): STICK}, buttons=[])
    axis = document["axes"][0]
    assert axis["rest_to_min"] == 128.0
    assert axis["rest_to_max"] == 127.0


def test_a_lost_window_reset_is_retried_rather_than_stalling(tmp_path):
    """Disarmed is silent, which is the dangerous part.

    State replies keep arriving and keep being ignored, so a step whose
    reset reply went missing simply never advances and says nothing
    about why. Step 6 resets once per button, so a lost reply has
    fifteen more chances to strand the wizard than it used to.
    """
    from ev3ctl.cli import gamepad as command
    session, wiz = wizard(tmp_path)
    wiz.session = session
    wiz.device = DEVICE
    wiz.enter(gamepad.LEFT)
    assert wiz.armed is False
    first = wiz.reset_id

    # Nothing acknowledges it. Before the hatch opens, nothing happens.
    wiz.tick(wiz.reset_sent_at + 0.5)
    assert wiz.reset_id == first

    wiz.tick(wiz.reset_sent_at + command.RESET_RETRY_AFTER_S + 0.1)
    assert wiz.reset_id != first, "a fresh reset should have gone out"
    assert "reset timed out" in wiz.last_error
