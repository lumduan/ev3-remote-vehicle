"""HOST CODE. evdev type and code names, copied from the kernel header.

A small transcription of `include/uapi/linux/input-event-codes.h` at tag
**v4.14**, which is the series the brick runs
(`4.14.117-ev3dev-2.3.5-ev3`). Only the ranges a gamepad can produce are
here; this is not a complete copy of the header and does not try to be.

Three things this deliberately is not:

- **Not a dependency.** `python-evdev` would do this and more, but the
  host is allowed exactly one third-party package and it is rich.
- **Not read from the running kernel.** The Mac has no such header, and
  reading the brick's would make the wizard's vocabulary depend on
  whichever machine it was run from.
- **Not a controller layout.** Knowing that code 0 is called `ABS_X`
  says nothing about which stick moves it. That is what the wizard is
  for, and every mapping it writes comes from an observed step.

These numbers are kernel ABI: codes are added over time and never
renumbered, so a transcription cannot rot. The names can, in one
direction only - a code added after v4.14 would come back here as None
and render as its number, which is the honest answer rather than a
wrong one.
"""

EV_SYN = 0x00
EV_KEY = 0x01
EV_REL = 0x02
EV_ABS = 0x03
EV_MSC = 0x04

TYPE_NAMES = {
    EV_SYN: "EV_SYN",
    EV_KEY: "EV_KEY",
    EV_REL: "EV_REL",
    EV_ABS: "EV_ABS",
    EV_MSC: "EV_MSC",
}

# Absolute axes. 0x00-0x05 are the six a two-stick pad with analog
# triggers reports; 0x10-0x11 are the first hat, which is where a D-pad
# usually arrives.
ABS_X = 0x00
ABS_Y = 0x01
ABS_Z = 0x02
ABS_RX = 0x03
ABS_RY = 0x04
ABS_RZ = 0x05
ABS_HAT0X = 0x10
ABS_HAT0Y = 0x11

ABS_NAMES = {
    ABS_X: "ABS_X",
    ABS_Y: "ABS_Y",
    ABS_Z: "ABS_Z",
    ABS_RX: "ABS_RX",
    ABS_RY: "ABS_RY",
    ABS_RZ: "ABS_RZ",
    0x06: "ABS_THROTTLE",
    0x07: "ABS_RUDDER",
    0x08: "ABS_WHEEL",
    0x09: "ABS_GAS",
    0x0A: "ABS_BRAKE",
    ABS_HAT0X: "ABS_HAT0X",
    ABS_HAT0Y: "ABS_HAT0Y",
    0x12: "ABS_HAT1X",
    0x13: "ABS_HAT1Y",
    0x14: "ABS_HAT2X",
    0x15: "ABS_HAT2Y",
    0x16: "ABS_HAT3X",
    0x17: "ABS_HAT3Y",
    0x18: "ABS_PRESSURE",
    0x19: "ABS_DISTANCE",
    0x1A: "ABS_TILT_X",
    0x1B: "ABS_TILT_Y",
    0x1C: "ABS_TOOL_WIDTH",
}

HAT_CODES = (ABS_HAT0X, ABS_HAT0Y, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17)

# The EV3's own buttons, decoded from its `B: KEY=1680 0 0 10004000` on
# 2026-09-02. Not gamepad codes: these are ordinary keyboard codes, and
# the brick's button device is bound to the `kbd` handler. Here so that
# host tooling can name what a brick button press was.
KEY_BACKSPACE = 14
KEY_ENTER = 28
KEY_UP = 103
KEY_LEFT = 105
KEY_RIGHT = 106
KEY_DOWN = 108

BRICK_BUTTONS = {
    KEY_BACKSPACE: "Back",
    KEY_ENTER: "Centre",
    KEY_UP: "Up",
    KEY_LEFT: "Left",
    KEY_RIGHT: "Right",
    KEY_DOWN: "Down",
}

# Buttons. The header gives 0x130-0x134 two names each: BTN_SOUTH and
# BTN_A are the same number, as are BTN_EAST/BTN_B, BTN_NORTH/BTN_X and
# BTN_WEST/BTN_Y. The letter aliases follow the *Xbox* face layout, so on
# a DualShock they do not correspond to the printed symbols at all -
# BTN_NORTH is 0x133 and is aliased BTN_X, while the pad's X-like symbol
# is Cross at the south position. The directional names are the
# unambiguous ones, so they are primary here and the alias is noted.
#
# This is precisely why the wizard records the label the operator was
# asked to press as a separate field from the symbolic name.
KEY_NAMES = {
    KEY_BACKSPACE: "KEY_BACKSPACE",
    KEY_ENTER: "KEY_ENTER",
    KEY_UP: "KEY_UP",
    KEY_LEFT: "KEY_LEFT",
    KEY_RIGHT: "KEY_RIGHT",
    KEY_DOWN: "KEY_DOWN",
    0x130: "BTN_SOUTH",
    0x131: "BTN_EAST",
    0x132: "BTN_C",
    0x133: "BTN_NORTH",
    0x134: "BTN_WEST",
    0x135: "BTN_Z",
    0x136: "BTN_TL",
    0x137: "BTN_TR",
    0x138: "BTN_TL2",
    0x139: "BTN_TR2",
    0x13A: "BTN_SELECT",
    0x13B: "BTN_START",
    0x13C: "BTN_MODE",
    0x13D: "BTN_THUMBL",
    0x13E: "BTN_THUMBR",
    0x220: "BTN_DPAD_UP",
    0x221: "BTN_DPAD_DOWN",
    0x222: "BTN_DPAD_LEFT",
    0x223: "BTN_DPAD_RIGHT",
    0x120: "BTN_TRIGGER",
    0x121: "BTN_THUMB",
    0x122: "BTN_THUMB2",
    0x123: "BTN_TOP",
    0x124: "BTN_TOP2",
    0x125: "BTN_PINKIE",
    0x126: "BTN_BASE",
    0x127: "BTN_BASE2",
    0x128: "BTN_BASE3",
    0x129: "BTN_BASE4",
    0x12A: "BTN_BASE5",
    0x12B: "BTN_BASE6",
    0x12F: "BTN_DEAD",
}

ALIASES = {
    0x130: "BTN_A",
    0x131: "BTN_B",
    0x133: "BTN_X",
    0x134: "BTN_Y",
}

# Bus types, from uapi/linux/input.h. The `I: Bus=` field of
# /proc/bus/input/devices is one of these, in hex.
BUS_USB = 0x03
BUS_BLUETOOTH = 0x05

BUS_NAMES = {
    0x01: "PCI",
    0x03: "USB",
    0x04: "HIL",
    0x05: "Bluetooth",
    0x06: "virtual",
    0x10: "ISA",
    0x11: "i8042",
    0x18: "I2C",
    0x19: "host",
}


def type_name(event_type):
    """The `EV_*` name for an event type, or None if it is not known."""
    return TYPE_NAMES.get(event_type)


def code_name(event_type, code):
    """The symbolic name for one (type, code), or None if not known.

    None is returned rather than a made-up name so that the caller can
    render the bare number. A code this table has never heard of is a
    fact worth showing, not one worth papering over.
    """
    if event_type == EV_ABS:
        return ABS_NAMES.get(code)
    if event_type == EV_KEY:
        return KEY_NAMES.get(code)
    return None


def alias_name(event_type, code):
    """The header's second name for a code, when it has one."""
    if event_type != EV_KEY:
        return None
    return ALIASES.get(code)


def label(event_type, code):
    """A name that always renders: the symbolic one, or the numbers."""
    name = code_name(event_type, code)
    if name is not None:
        return name
    return "{0}:{1}".format(type_name(event_type) or event_type, code)


def is_hat(event_type, code):
    """True for a hat axis, which is a D-pad and never a stick.

    Used only to explain a rendering, never to decide a mapping. The
    wizard excludes hats from the stick steps on their measured range,
    not on this.
    """
    return event_type == EV_ABS and code in HAT_CODES


def bus_name(bus):
    """A readable name for a bustype, or None."""
    return BUS_NAMES.get(bus)
