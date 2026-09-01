"""HOST CODE. The arithmetic between raw evdev counters and a mapping.

Pure functions, no I/O and no state. The wizard's rendering is in ui.py,
its loop is in cli/gamepad.py, and the reading of the device is on the
brick. What is left here is the part that decides whether a step is
satisfied, which is the part that is easiest to get quietly wrong and
the only part that can be checked without a controller in your hands.

Two ideas run through all of it.

**Nothing is assigned that was not observed.** A step names an axis only
because that axis was seen to move during that step. There is no table
of DualShock axis numbers in this file. The place a published layout
would be most convenient - deciding which of a stick's two axes is
horizontal - is instead settled by asking the operator to push the stick
one way and watching which axis moves, because a circular sweep drives
both through their whole range and genuinely cannot say. The usual evdev
polarity convention is recorded as agreeing or disagreeing with what was
measured, and is never consulted to decide anything.

**Every threshold is measured against something the driver said.** The
80 percent test compares against the axis's own reported minimum and
maximum, read with EVIOCGABS, not against a range this code invented.
When the driver will not say, the test falls back to the range observed
so far and the mapping records which of the two it used.
"""

from . import evdev_codes

# The column order of one `gamepad_state` row. The agent returns this
# same tuple from `gamepad_open` so the two sides cannot drift apart
# silently; `columns_match` is what checks it.
STATE_COLUMNS = (
    "type", "code", "latest", "min", "max", "count", "sum",
    "distinct", "interior", "overflow",
)

# How far from rest an axis has to travel, as a fraction of the distance
# from rest to the driver's own limit, before the sweep counts.
SWEEP_FRACTION = 0.8

# A trigger rests at one end of its range rather than in the middle, so
# it is tested against both ends of the span instead of either side of a
# centre.
TRIGGER_FRACTION = 0.8
TRIGGER_RETURN_FRACTION = 0.2

# An axis whose whole declared range is narrower than this is too coarse
# to be a stick. A hat - which is what a D-pad usually is - declares a
# range of 2, so without this a brushed D-pad would sail through an 80
# percent test and be named as a stick. The number comes from the
# driver's declared range, which is measured; it is not a claim about
# any particular controller's layout.
MIN_STICK_RANGE = 8

# Distinct values strictly inside the observed span. Zero of them means
# the axis only ever reported its two extremes, which is an analog
# trigger being delivered as a digital button.
CONTINUOUS_MIN_INTERIOR = 8

# The suggested deadzone is this many times the measured rest spread.
# It is a starting point derived from one sitting's jitter, not a tuned
# value, and the mapping file says so in as many words.
DEADZONE_MULTIPLE = 3

REST_SECONDS = 3.0

STICK_AXES_PER_STEP = 2

# The steps, defined here rather than in the wizard because both the
# wizard and the renderer need to reason about which step is running,
# and a second copy of these numbers is a second thing to get wrong.
CONNECT = 0
REST = 1
LEFT = 2
RIGHT = 3
TRIGGER_L = 4
TRIGGER_R = 5
BUTTONS = 6
SUMMARY = 7

STEPS = (
    (CONNECT, "Connect",
     "Press the PS button on the controller."),
    (REST, "Rest",
     "Take both hands off the controller and leave it alone."),
    (LEFT, "Left stick",
     "Sweep the LEFT stick in a full circle, out to the rim."),
    (RIGHT, "Right stick",
     "Sweep the RIGHT stick in a full circle, out to the rim."),
    (TRIGGER_L, "L2 trigger",
     "Squeeze L2 all the way in and let it out again, three times."),
    (TRIGGER_R, "R2 trigger",
     "Squeeze R2 all the way in and let it out again, three times."),
    (BUTTONS, "Buttons",
     "Press each button as it is named. Press s when you are done."),
    (SUMMARY, "Summary", "Done."),
)

# Steps that clear the accumulated window as they are entered, so that
# each attributes only what happened during it. Rest is included even
# though the requirement lists only the five after it: a rest spread
# measured over events left from the connect step would be the pad
# settling down, not the pad at rest.
RESETTING_STEPS = (REST, LEFT, RIGHT, TRIGGER_L, TRIGGER_R, BUTTONS)

# Sub-phases within a stick step. The circular sweep identifies which
# pair of axes belongs to the stick, and that is all it can do: it drives
# both axes through their whole range, so it cannot say which of the two
# is horizontal. Two directional holds after it answer that, and they are
# inside the step rather than steps of their own because they reuse the
# pair the sweep just named.
SWEEP = 0
HOLD_RIGHT = 1
HOLD_UP = 2

STICK_PHASES = (SWEEP, HOLD_RIGHT, HOLD_UP)

HOLD_SECONDS = 1.0

# The axis being asked for must out-deflect the other by this much. A
# diagonal push moves both, and an orientation taken from a diagonal is
# a coin flip that the file would then present as a measurement.
HOLD_RATIO = 3.0

# ...and it must actually be pushed, not merely leaned on: at least this
# far from rest toward the driver's limit. Without it, a hold where the
# operator touched nothing would pass on noise, because near-zero is
# still three times nearer-zero.
HOLD_MIN_FRACTION = 0.5

# Which way each hold pushes, and what a positive deviation therefore
# means. The second and third entries are the answer for a deviation
# that came out positive and negative respectively.
HOLD_DIRECTIONS = {
    HOLD_RIGHT: ("RIGHT", "right", "left", "horizontal"),
    HOLD_UP: ("UP", "up", "down", "vertical"),
}

STICK_STEPS = {LEFT: "left_stick", RIGHT: "right_stick"}
TRIGGER_STEPS = {TRIGGER_L: "l2", TRIGGER_R: "r2"}

# What the operator is asked to press in step 6, in order. These are the
# symbols printed on the controller, so they name a physical thing the
# operator can find. They are prompts, not an expected mapping: whatever
# code arrives while a prompt is showing is what gets recorded against
# it, including nothing at all.
BUTTON_PROMPTS = (
    "Cross", "Circle", "Triangle", "Square",
    "L1", "R1", "L3", "R3",
    "Share", "Options", "PS",
    "D-pad up", "D-pad down", "D-pad left", "D-pad right",
)

# ---------------------------------------------------------------------
# Device facts
#
# Discovery itself is on the brick: it has to parse
# /proc/bus/input/devices to know which node to open, and doing it twice
# on two sides of the link would be two chances to disagree. What is
# left here is what the host does with the fields that come back.
# ---------------------------------------------------------------------

def axis_codes_from_mask(mask):
    """Axis code numbers out of a `B: ABS=` bitmask.

    The mask is little-endian groups of hex longs, most significant
    group first, space separated. Used only to say how many axes the
    device claims to have, so that the summary can report six of six
    rather than six of an unknown number.
    """
    if not mask:
        return []
    groups = mask.split()
    codes = []
    for index, group in enumerate(reversed(groups)):
        try:
            value = int(group, 16)
        except ValueError:
            continue
        base = index * 32
        bit = 0
        while value:
            if value & 1:
                codes.append(base + bit)
            value >>= 1
            bit += 1
    return sorted(codes)


# ---------------------------------------------------------------------
# The wire rows
# ---------------------------------------------------------------------

def columns_match(columns):
    """True when the agent's column order is the one this file expects."""
    return tuple(columns or ()) == STATE_COLUMNS


def rows_to_codes(rows):
    """Wire rows to a dict keyed by (type, code).

    A row shorter than the column list is dropped rather than padded. A
    truncated row means the two sides disagree about the protocol, and
    inventing zeros for the missing fields would turn that into a
    plausible reading instead of a visible fault.
    """
    codes = {}
    width = len(STATE_COLUMNS)
    for row in rows or ():
        if len(row) < width:
            continue
        entry = dict(zip(STATE_COLUMNS, row))
        codes[(entry["type"], entry["code"])] = entry
    return codes


def axes(codes):
    """Just the EV_ABS entries, in code order."""
    return sorted(
        ((key, value) for key, value in codes.items()
         if key[0] == evdev_codes.EV_ABS),
        key=lambda item: item[0][1],
    )


def pressed_keys(codes):
    """EV_KEY codes that have been seen at all in this window."""
    return sorted(
        key[1] for key, value in codes.items()
        if key[0] == evdev_codes.EV_KEY and value["count"] > 0
    )


# ---------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------

def mean_value(entry):
    """The average value over the window, or the seeded value.

    An axis that emitted no events during the window has no average to
    take. Its current value is the honest answer, and it is a real one
    rather than a zero: the agent seeds every axis from the driver's own
    reported value when it opens the device, so a stick that was never
    touched still reports where it is resting.
    """
    if entry is None:
        return None
    if entry["count"] > 0:
        return entry["sum"] / float(entry["count"])
    return float(entry["latest"])


def spread(entry):
    """Peak to peak over the window."""
    if entry is None:
        return None
    return entry["max"] - entry["min"]


def suggested_deadzone(rest_spread):
    """Three times the measured jitter. A starting point, not a tuning."""
    if rest_spread is None:
        return None
    return int(round(rest_spread * DEADZONE_MULTIPLE))


def rest_report(codes):
    """Per axis, the mean and the peak-to-peak spread at rest."""
    report = {}
    for key, entry in axes(codes):
        report[key] = {
            "mean": mean_value(entry),
            "spread": spread(entry),
        }
    return report


# ---------------------------------------------------------------------
# The advance gates
# ---------------------------------------------------------------------

def axis_range(driver, entry):
    """The (low, high, source) that an 80 percent test measures against.

    The driver's own limits when EVIOCGABS answered, and the range
    observed so far when it did not. The source travels with the
    measurement into the mapping file, so a reader can tell which of the
    two a given axis was judged by.
    """
    if driver:
        low = driver.get("minimum")
        high = driver.get("maximum")
        if low is not None and high is not None and high > low:
            return low, high, "driver"
    if entry is None:
        return None, None, "unknown"
    if entry["max"] > entry["min"]:
        return entry["min"], entry["max"], "observed"
    return None, None, "unknown"


def too_coarse(low, high):
    """True for a range too narrow to belong to a stick or a trigger."""
    if low is None or high is None:
        return True
    return (high - low) < MIN_STICK_RANGE


def sweep_progress(entry, rest, low, high):
    """How far each direction got, each 0.0 to 1.0, for the display.

    Returned even when the axis cannot qualify, so the operator can see
    a stick that is moving but not reaching, which is the difference
    between a step that is going wrong and a step that is going slowly.
    """
    if entry is None or rest is None or low is None or high is None:
        return 0.0, 0.0
    up_span = (high - rest) * SWEEP_FRACTION
    down_span = (rest - low) * SWEEP_FRACTION
    up = (entry["max"] - rest) / up_span if up_span > 0 else 0.0
    down = (rest - entry["min"]) / down_span if down_span > 0 else 0.0
    return _clamp01(up), _clamp01(down)


def swept(entry, rest, low, high):
    """True when the axis was carried near both ends of its range."""
    if too_coarse(low, high):
        return False
    up, down = sweep_progress(entry, rest, low, high)
    return up >= 1.0 and down >= 1.0


def trigger_progress(entry, low, high):
    """How far the trigger got toward pressed, and back toward released."""
    if entry is None or low is None or high is None or high <= low:
        return 0.0, 0.0
    span = float(high - low)
    press = (entry["max"] - low) / (span * TRIGGER_FRACTION)
    release_target = span * TRIGGER_RETURN_FRACTION
    if release_target <= 0:
        release = 1.0 if entry["min"] <= low else 0.0
    else:
        release = (low + release_target - entry["min"]) / release_target
    return _clamp01(press), _clamp01(release)


def trigger_spanned(entry, low, high):
    """True when the trigger reached both ends of its declared range."""
    if too_coarse(low, high):
        return False
    press, release = trigger_progress(entry, low, high)
    return press >= 1.0 and release >= 1.0


def continuity(entry):
    """Whether an axis moved through its range or jumped between ends.

    "extremes-only" is the finding that matters: an analog trigger being
    reported as a digital button rules out proportional throttle, and it
    is far better to discover that here than in a control loop.
    """
    if entry is None or entry["count"] == 0:
        return "unknown"
    if entry["max"] <= entry["min"]:
        return "unknown"
    if entry["interior"] == 0:
        return "extremes-only"
    if entry["interior"] >= CONTINUOUS_MIN_INTERIOR:
        return "continuous"
    return "few"


def qualifying_axes(codes, drivers, rest, exclude=()):
    """Every axis that passed the sweep gate, excluding claimed ones."""
    found = []
    for key, entry in axes(codes):
        if key in exclude:
            continue
        low, high, _ = axis_range(drivers.get(key), entry)
        mean = rest.get(key, {}).get("mean")
        if swept(entry, mean, low, high):
            found.append(key)
    return found


def qualifying_triggers(codes, drivers, exclude=()):
    """Every axis that spanned its range end to end, excluding claimed."""
    found = []
    for key, entry in axes(codes):
        if key in exclude:
            continue
        low, high, _ = axis_range(drivers.get(key), entry)
        if trigger_spanned(entry, low, high):
            found.append(key)
    return found


def _clamp01(value):
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


# ---------------------------------------------------------------------
# The directional holds
# ---------------------------------------------------------------------

def deviation(entry, rest_mean):
    """How far this axis got from rest during the window, with its sign.

    The furthest excursion rather than the average, because the window
    opens while the stick is still travelling and an average over the
    journey understates where it arrived.
    """
    if entry is None or rest_mean is None:
        return None
    above = entry["max"] - rest_mean
    below = entry["min"] - rest_mean
    return above if abs(above) >= abs(below) else below


def reach(value, rest_mean, low, high):
    """A deviation as a fraction of the distance from rest to the limit.

    Signed input, unsigned output: how much of the available travel in
    whichever direction it went was actually used.
    """
    if value is None or rest_mean is None or low is None or high is None:
        return 0.0
    span = (high - rest_mean) if value >= 0 else (rest_mean - low)
    if span <= 0:
        return 0.0
    return abs(value) / float(span)


def resolve_hold(pair, codes, rest, drivers):
    """Which of a stick's two axes the operator just pushed.

    Returns a dict describing the outcome rather than a bare answer,
    because the display has to show the operator the ratio being
    satisfied while they are still holding, and the same numbers are
    what the mapping file records.

    `verdict` is one of:
      ok        - one axis clearly moved and the other did not
      diagonal  - both moved; the push was not along an axis
      soft      - nothing moved far enough to be a deliberate push
      unknown   - no rest measurement, so no deviation can be taken
    """
    if len(pair) != STICK_AXES_PER_STEP:
        return {"verdict": "unknown", "deviations": {}, "ratio": None,
                "chosen": None, "other": None, "reach": 0.0}

    deviations = {}
    for key in pair:
        mean = rest.get(key, {}).get("mean")
        deviations[key] = deviation(codes.get(key), mean)

    if any(value is None for value in deviations.values()):
        return {"verdict": "unknown", "deviations": deviations,
                "ratio": None, "chosen": None, "other": None,
                "reach": 0.0}

    ordered = sorted(pair, key=lambda k: abs(deviations[k]), reverse=True)
    chosen, other = ordered[0], ordered[1]
    big, small = abs(deviations[chosen]), abs(deviations[other])

    low, high, _ = axis_range(drivers.get(chosen), codes.get(chosen))
    travelled = reach(deviations[chosen], rest.get(chosen, {}).get("mean"),
                      low, high)

    # Infinite rather than a division by zero: an untouched other axis
    # is the cleanest possible result, not an error.
    ratio = (big / small) if small > 0 else None

    result = {"deviations": deviations, "chosen": chosen, "other": other,
              "ratio": ratio, "reach": travelled}
    if travelled < HOLD_MIN_FRACTION:
        result["verdict"] = "soft"
    elif ratio is not None and ratio < HOLD_RATIO:
        result["verdict"] = "diagonal"
    else:
        result["verdict"] = "ok"
    return result


def hold_outcome(phase, outcome):
    """The orientation and polarity one satisfied hold establishes.

    Returns (chosen_key, orientation, positive_direction). The polarity
    is read off the sign of the deviation: if pushing the stick right
    made the axis go up, then up on that axis means right.
    """
    _, positive, negative, orientation = HOLD_DIRECTIONS[phase]
    chosen = outcome["chosen"]
    value = outcome["deviations"][chosen]
    return chosen, orientation, (positive if value > 0 else negative)


def matches_evdev_convention(orientation, positive_direction):
    """Whether a measured polarity agrees with the usual evdev layout.

    The convention is that X counts up to the right and Y counts up
    downward - left is the minimum on X, up is the minimum on Y. It is
    recorded because agreement or disagreement is itself a fact worth
    having, and for no other purpose: nothing in this wizard consults it
    to decide anything, and an axis is oriented by what the operator was
    seen to do with it.
    """
    if orientation is None or positive_direction is None:
        return None
    if orientation == "horizontal":
        return positive_direction == "right"
    return positive_direction == "down"


# ---------------------------------------------------------------------
# The mapping document
# ---------------------------------------------------------------------

DEADZONE_NOTE = (
    "suggested_deadzone is the measured rest peak-to-peak spread times "
    "{0}. It is a starting point derived from this controller's jitter "
    "in one sitting, not a tuned value, and it should be checked against "
    "driver_flat where that is present."
).format(DEADZONE_MULTIPLE)

ORIENTATION_NOTE = (
    "orientation and positive_direction are measured, not assumed. The "
    "circular sweep identifies which pair of axes a stick owns; it "
    "cannot say which of the pair is horizontal, because it drives both "
    "through their whole range. Two directional holds after the sweep "
    "settle it: the operator pushes the stick right, then up, and the "
    "axis that deflects further each time is the one named. "
    "hold_deviations carries what both axes did during both holds, so "
    "the decision can be checked rather than taken on trust."
)

EVDEV_CONVENTION_NOTE = (
    "The usual evdev convention is that X counts up toward the right "
    "and Y counts up downward, so left is the minimum on X and up is "
    "the minimum on Y. matches_evdev_convention records whether this "
    "controller agreed. It is a data point and nothing more: no part of "
    "the capture consulted the convention to decide an orientation or a "
    "polarity, and a false here means the controller differs, not that "
    "the measurement is wrong."
)


def build_mapping(device, captured_at, assignments, rest, codes, drivers,
                  buttons, declared_axes=(), orientation=None,
                  polarity=None, hold_deviations=None):
    """The whole mapping document, ready to be written as JSON.

    `assignments` maps (type, code) to the control name a step decided
    on. Every axis the device declares appears, including ones no step
    ever claimed; those carry a null control rather than being left out,
    so that a mapping with a gap in it looks like one.
    """
    orientation = orientation or {}
    polarity = polarity or {}
    hold_deviations = hold_deviations or {}

    axis_records = []
    seen = set()
    for key, entry in axes(codes):
        seen.add(key[1])
        axis_records.append(_axis_record(
            key, entry, assignments, rest, drivers, orientation,
            polarity, hold_deviations))
    for code in declared_axes:
        if code in seen:
            continue
        axis_records.append(_missing_axis_record(code))
    axis_records.sort(key=lambda record: record["code"])

    return {
        "tool": "ev3ctl gamepad",
        "captured_at": captured_at,
        "device": device,
        "deadzone_note": DEADZONE_NOTE,
        "orientation_note": ORIENTATION_NOTE,
        "evdev_convention_note": EVDEV_CONVENTION_NOTE,
        "axes": axis_records,
        "buttons": [_button_record(item) for item in buttons],
    }


def _axis_record(key, entry, assignments, rest, drivers, orientation,
                 polarity, hold_deviations):
    driver = drivers.get(key) or {}
    low, high, source = axis_range(driver, entry)
    rest_entry = rest.get(key, {})
    rest_spread = rest_entry.get("spread")
    record = {
        "code": key[1],
        "name": evdev_codes.code_name(key[0], key[1]),
        "control": assignments.get(key),
        "orientation": orientation.get(key),
        "positive_direction": polarity.get(key),
        "matches_evdev_convention": matches_evdev_convention(
            orientation.get(key), polarity.get(key)),
        "hold_deviations": hold_deviations.get(key),
        "observed_min": entry["min"],
        "observed_max": entry["max"],
        "rest_mean": _round(rest_entry.get("mean")),
        "rest_spread": rest_spread,
        "suggested_deadzone": suggested_deadzone(rest_spread),
        "range_source": source,
        "driver_min": driver.get("minimum"),
        "driver_max": driver.get("maximum"),
        "driver_fuzz": driver.get("fuzz"),
        "driver_flat": driver.get("flat"),
        "driver_resolution": driver.get("resolution"),
        "gate_low": low,
        "gate_high": high,
    }
    control = assignments.get(key)
    if control in ("l2", "r2"):
        record["continuous"] = continuity(entry)
        record["interior_values_seen"] = entry["interior"]
        record["distinct_values_seen"] = entry["distinct"]
        record["distinct_capped"] = bool(entry["overflow"])
    return record


def _missing_axis_record(code):
    """An axis the device declares but no step ever saw move."""
    return {
        "code": code,
        "name": evdev_codes.code_name(evdev_codes.EV_ABS, code),
        "control": None,
        "orientation": None,
        "positive_direction": None,
        "matches_evdev_convention": None,
        "hold_deviations": None,
        "observed_min": None,
        "observed_max": None,
        "rest_mean": None,
        "rest_spread": None,
        "suggested_deadzone": None,
        "range_source": "unseen",
        "driver_min": None,
        "driver_max": None,
        "driver_fuzz": None,
        "driver_flat": None,
        "driver_resolution": None,
        "gate_low": None,
        "gate_high": None,
    }


def _button_record(item):
    """One captured button.

    `value` is carried because a D-pad on this pad is expected to arrive
    as hat *axis* movement rather than as EV_KEY presses, and up and
    down are then the same code with opposite signs. Recording the code
    alone would silently merge the two.
    """
    event_type, code, label, value = item
    return {
        "code": code,
        "name": evdev_codes.code_name(event_type, code),
        "alias": evdev_codes.alias_name(event_type, code),
        "type": evdev_codes.type_name(event_type),
        "value": value,
        "label": label,
    }


def _round(value):
    if value is None:
        return None
    return round(value, 2)
