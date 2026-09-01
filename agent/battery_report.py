#!/usr/bin/env python3
"""BRICK CODE. What the brick's batteries say, for an operator to read.

This exists because a flat gamepad battery and a broken Bluetooth
pairing look identical from the host: the controller connects, the
kernel binds a driver, and the link drops a few seconds later. Telling
those two apart needs a number read off the hardware, and the only
place that number exists is the brick's own sysfs.

Run it on the brick, on its own:

    ssh robot@ev3dev.local python3 /tmp/battery_report.py

It is an operator diagnostic and not part of the control path. Nothing
imports it, it imports nothing from this project, and it never writes to
a device - so it is safe to run at any moment, including while the agent
is running and a motor is turning.

Two facts about this sysfs that the code depends on, both read off the
hardware rather than assumed:

- The brick's own pack reports a `scope` of `System`, while a battery
  inside an attached peripheral reports `Device`. That attribute, and
  not the directory name, is what separates them. hid-sony names the
  gamepad's node after the controller's MAC address, so matching on the
  name would be matching on the one thing that differs per controller.
- The brick's pack offers `voltage_now` and no `capacity` at all, while
  hid-sony's node for a DualShock 4 offers `capacity` and `status` and
  no voltage. Every attribute here is therefore optional, and a missing
  one is reported as absent rather than raising.
"""

import os
import sys

SYSFS_ROOT = "/sys/class/power_supply"

# Listed in the order an operator wants to read them, not alphabetically.
ATTRIBUTES = (
    "model_name",
    "scope",
    "status",
    "capacity",
    "capacity_level",
    "voltage_now",
)

# Any microvolt reading this project will meet falls well inside this
# band. Outside it the scale is not what we think it is, and saying so
# is better than printing a confident wrong voltage.
PLAUSIBLE_MICROVOLTS = (1000000, 30000000)


def read_attribute(directory, name):
    # (str, str) -> str or None
    """One sysfs attribute, or None when it is not there.

    Caught per attribute deliberately. Attribute sets differ between
    drivers and between ev3dev releases, and one missing file must not
    cost the reading of every other one.
    """
    path = os.path.join(SYSFS_ROOT, directory, name)
    try:
        with open(path) as handle:
            return handle.read().strip()
    except (IOError, OSError):
        return None


def render_voltage(raw):
    # (str) -> str or None
    """Microvolts as volts, but only when the number looks like one."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    low, high = PLAUSIBLE_MICROVOLTS
    if low <= value <= high:
        return "{0:.2f} V".format(value / 1000000.0)
    return None


def describe(directory):
    # (str) -> list of (str, str)
    """Every attribute of one supply that could actually be read."""
    found = []
    for name in ATTRIBUTES:
        value = read_attribute(directory, name)
        if value is not None:
            found.append((name, value))
    return found


def classify(scope):
    # (str or None) -> str
    """What a supply is, judged by scope rather than by its name."""
    if scope == "Device":
        return "peripheral - a battery inside an attached device"
    if scope == "System":
        return "the brick's own pack"
    return "unknown - this supply reports no scope attribute"


def main():
    print("Power supplies under {0}".format(SYSFS_ROOT))
    try:
        names = sorted(os.listdir(SYSFS_ROOT))
    except OSError as exc:
        print("  cannot list it: {0}".format(exc))
        return 1

    if not names:
        print("  none at all")

    peripherals = []
    for directory in names:
        attributes = describe(directory)
        lookup = dict(attributes)
        scope = lookup.get("scope")
        if scope == "Device":
            peripherals.append(directory)

        print("")
        print("  {0}".format(directory))
        print("    classified as: {0}".format(classify(scope)))
        for name, value in attributes:
            line = "    {0:<16}{1}".format(name, value)
            if name == "voltage_now":
                volts = render_voltage(value)
                if volts is None:
                    line += "  (not a plausible microvolt reading)"
                else:
                    line += "  ({0})".format(volts)
            print(line)
        missing = [n for n in ATTRIBUTES if n not in lookup]
        if missing:
            print("    absent: {0}".format(", ".join(missing)))

    print("")
    if peripherals:
        print("Gamepad battery node: PRESENT - {0}".format(
            ", ".join(peripherals)))
        print("A supply with scope Device means a Sony-specific driver")
        print("parsed the controller's battery field. hid-generic does")
        print("not parse it and creates no such node, so this node")
        print("existing is itself evidence of which driver bound.")
    else:
        print("Gamepad battery node: ABSENT")
        print("No supply reports scope Device, so no peripheral battery")
        print("is attached at this moment. With the gamepad switched")
        print("off or disconnected, that is the expected result.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
