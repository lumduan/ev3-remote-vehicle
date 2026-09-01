"""HOST CODE. Everything the operator looks at.

The tables are drawn on a fixed grid: output ports A to D and input
ports 1 to 4, always all eight rows, whether or not anything is plugged
in. That is the point of the tool. A port that is empty has to look
different from a port this program could not read, and a row that
disappears when a motor is unplugged tells the operator nothing except
that something happened.

Nothing here reaches the network or the hardware. It is handed an
inventory and a snapshot and turns them into a frame.
"""

import time

from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import evdev_codes, gamepad
from .model import (
    DASH,
    INPUT_PORTS,
    OUTPUT_PORTS,
    PORT_LABELS,
    battery_is_plausible,
    battery_milliamps,
    battery_volts,
    degrees,
    format_scaled,
    number,
    text,
)

KEY_HELP = (
    "[head]a b c d[/head] select port   "
    "[head]left/right[/head] duty -/+10   "
    "[head]space[/head] duty 0   "
    "[head]0[/head] stop all   "
    "[head]r[/head] reset   "
    "[head]s[/head] sensor mode   "
    "[head]q[/head] quit"
)


def _merge(inventory_entry, snapshot_entry, field):
    """Prefer the live value, fall back to the one from the last scan."""
    if snapshot_entry and field in snapshot_entry:
        value = snapshot_entry.get(field)
        if value is not None:
            return value
    if inventory_entry:
        return inventory_entry.get(field)
    return None


def header(session, link_status="connected"):
    facts = Table.grid(padding=(0, 2))
    facts.add_column(style="dim", justify="right")
    facts.add_column()
    facts.add_row("host", text(session.hostname) + "  [dim]via[/dim] " +
                  text(session.host))
    facts.add_row("kernel", text(session.kernel))
    facts.add_row("python", text(session.python))
    release = session.ev3dev_release
    if release:
        facts.add_row("ev3dev", release.splitlines()[0])
    facts.add_row("link", link_status)
    return Panel(facts, title="ev3ctl", title_align="left", padding=(0, 1))


def motors_table(inventory, snapshot, selected=None):
    table = Table(
        title="Output ports", title_justify="left", header_style="head",
        expand=True, padding=(0, 1),
    )
    # Driver gets a minimum because "lego-ev3-l-motor" truncated to
    # "lego-ev..." defeats the point of the tool: telling a Large Motor
    # from a Medium one. The numeric columns give up width first.
    table.add_column("Port", width=4)
    table.add_column("Driver", min_width=16)
    table.add_column("Counts", justify="right", width=7)
    table.add_column("Deg", justify="right", width=7)
    table.add_column("Speed", justify="right", width=6)
    table.add_column("Cmd", justify="right", width=4)
    table.add_column("Duty", justify="right", width=4)
    table.add_column("State")

    for address in OUTPUT_PORTS:
        device = inventory.motor(address)
        live = snapshot.motor(address)
        label = PORT_LABELS[address]
        if selected == address:
            label = "[sel] " + label + " [/sel]"
        if device is None and live is None:
            table.add_row(label, "[empty]empty[/empty]", DASH, DASH, DASH,
                          DASH, DASH, "")
            continue
        position = _merge(device, live, "position")
        count_per_rot = (device or {}).get("count_per_rot")
        state = _merge(device, live, "state") or []
        table.add_row(
            label,
            text((device or {}).get("driver_name")),
            number(position),
            number(degrees(position, count_per_rot), 1),
            number(_merge(device, live, "speed")),
            number(_merge(device, live, "duty_cycle_sp")),
            number(_merge(device, live, "duty_cycle")),
            "[dim]" + " ".join(state) + "[/dim]" if state else "",
        )
    return table


def sensors_table(inventory, snapshot, selected=None):
    table = Table(
        title="Input ports", title_justify="left", header_style="head",
        expand=True, padding=(0, 1),
    )
    table.add_column("Port", width=4)
    table.add_column("Driver", min_width=16)
    table.add_column("Mode", min_width=12)
    table.add_column("Values")

    for address in INPUT_PORTS:
        device = inventory.sensor(address)
        live = snapshot.sensor(address)
        label = PORT_LABELS[address]
        if selected == address:
            label = "[sel] " + label + " [/sel]"
        if device is None and live is None:
            table.add_row(label, "[empty]empty[/empty]", DASH, DASH)
            continue
        table.add_row(
            label,
            text((device or {}).get("driver_name")),
            text(_merge(device, live, "mode")),
            sensor_values(device, live),
        )
    return table


def sensor_values(device, live):
    """Scaled values with their unit, not the raw driver integers."""
    source = live if live and live.get("values") is not None else device
    if not source:
        return DASH
    values = source.get("values") or []
    if not values:
        return DASH
    decimals = source.get("decimals")
    if decimals is None and device:
        decimals = device.get("decimals")
    units = source.get("units") or (device or {}).get("units") or ""
    rendered = [format_scaled(value, decimals) for value in values]
    joined = "  ".join(rendered)
    if units:
        return joined + " [unit]" + units + "[/unit]"
    return joined


def battery_line(snapshot, inventory):
    battery = snapshot.battery or inventory.battery or {}
    volts = battery_volts(battery.get("voltage_now"))
    milliamps = battery_milliamps(battery.get("current_now"))
    node = battery.get("node")
    if volts is None:
        return Text.from_markup(
            "[warn]battery:[/warn] no reading"
            + (" from " + node if node else " (no power_supply node found)")
        )
    reading = "{0:.2f} V".format(volts)
    if milliamps is not None:
        reading += "  {0:.0f} mA".format(milliamps)
    if not battery_is_plausible(volts):
        return Text.from_markup(
            "[warn]battery: {0} - outside the plausible 6.0-8.5 V range, "
            "so the microvolt scaling is probably wrong[/warn]".format(
                reading)
        )
    return Text.from_markup(
        "[dim]battery[/dim] " + reading
        + ("  [dim]" + node + "[/dim]" if node else "")
    )


def footer(inventory, snapshot, selected_out, selected_in, intended,
           last_error=None):
    grid = Table.grid(padding=(0, 1))
    grid.add_column()
    grid.add_row(battery_line(snapshot, inventory))
    target = "[dim]selected[/dim] out{0}  intent {1}   in{2}".format(
        PORT_LABELS[selected_out],
        intended.get(selected_out, 0),
        PORT_LABELS[selected_in],
    )
    grid.add_row(Text.from_markup(target))
    grid.add_row(Text.from_markup(KEY_HELP))
    if last_error:
        grid.add_row(Text.from_markup("[fail]" + _one_line(last_error)
                                      + "[/fail]"))
    else:
        grid.add_row(Text(""))
    return Panel(grid, padding=(0, 1))


def _one_line(message):
    flattened = " ".join(str(message).split())
    if len(flattened) > 160:
        return flattened[:157] + "..."
    return flattened


def dashboard(session, inventory, snapshot, selected_out, selected_in,
              intended, last_error=None, link_status="connected"):
    layout = Layout()
    layout.split_column(
        Layout(header(session, link_status), name="header", size=9),
        Layout(motors_table(inventory, snapshot, selected_out),
               name="motors", size=8),
        Layout(sensors_table(inventory, snapshot, selected_in),
               name="sensors", size=8),
        Layout(footer(inventory, snapshot, selected_out, selected_in,
                      intended, last_error), name="footer", size=6),
    )
    return layout


def ports_table(inventory):
    """The lego-port class, which is where an empty port is still a port."""
    table = Table(
        title="lego-port", title_justify="left", header_style="head",
        expand=True, padding=(0, 1),
    )
    # Both the bare port and the driver's own address string, because
    # they are not the same thing on this hardware and finding that out
    # cost an afternoon: the driver says "ev3-ports:outA".
    table.add_column("Port", width=6)
    table.add_column("Address", min_width=15)
    table.add_column("Driver", min_width=16)
    table.add_column("Status")
    rows = sorted(inventory.ports.items())
    if not rows:
        table.add_row("[empty]none[/empty]",
                      "[empty]/sys/class/lego-port is absent or empty"
                      "[/empty]", "", "")
        return table
    for key, port in rows:
        table.add_row(
            text(key),
            text(port.get("address")),
            text(port.get("driver_name")),
            text(port.get("status")),
        )
    return table


# ---------------------------------------------------------------------
# drive
# ---------------------------------------------------------------------

DRIVE_KEYS = ("w", "a", "s", "d")

DRIVE_HELP = (
    "[head]w a s d[/head] drive   "
    "[head]space[/head] stop now   "
    "[head]q[/head] quit"
)


def _held_keys(drive):
    """The key set, drawn so a held key is unmistakable at a glance."""
    parts = []
    for key in DRIVE_KEYS:
        if key in drive.held:
            parts.append("[sel] " + key.upper() + " [/sel]")
        else:
            parts.append("[dim] " + key + " [/dim]")
    return "  ".join(parts)


def _millis(seconds):
    if seconds is None:
        return DASH
    return "{0:.0f} ms".format(seconds * 1000.0)


def drive_header(drive):
    session = drive.session
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", justify="right")
    grid.add_column()
    grid.add_row("host", text(session.hostname) + "  [dim]via[/dim] "
                 + text(session.host))
    grid.add_row("kernel", text(session.kernel))
    for address, change in sorted(drive.stop_action.items()):
        previous, current = change
        if current is None:
            grid.add_row("stop", "[fail]" + address
                         + ": stop_action unchanged[/fail]")
        elif previous == current:
            grid.add_row("stop", address + ": stop_action already ["
                         + "ok]" + text(current) + "[/ok]")
        else:
            grid.add_row("stop", address + ": stop_action [dim]"
                         + text(previous) + "[/dim] -> [ok]"
                         + text(current) + "[/ok]")
    return Panel(grid, title="ev3ctl drive", title_align="left",
                 padding=(0, 1))


def drive_motors_table(drive):
    table = Table(
        title="Drive", title_justify="left", header_style="head",
        expand=True, padding=(0, 1),
    )
    table.add_column("Side", width=6)
    table.add_column("Port", min_width=10)
    table.add_column("Cmd", justify="right", width=6)
    table.add_column("Duty", justify="right", width=6)
    table.add_column("Speed", justify="right", width=8)

    # No state column. Every extra attribute in the drive readback costs
    # about 9 ms of round trip on this brick, and state is not one of
    # the values this display is required to show. `ev3ctl live` has it.
    sides = (
        ("left", drive.left, drive.duty_left, drive.invert_left),
        ("right", drive.right, drive.duty_right, drive.invert_right),
    )
    for name, address, commanded, inverted in sides:
        live = (drive.readback or {}).get(name) or {}
        speed = number(live.get("speed"))
        if name in getattr(drive, "stalled", ()):
            speed = "[fail]STALL[/fail]"
        table.add_row(
            name,
            text(address) + (" [warn]inv[/warn]" if inverted else ""),
            number(commanded),
            number(live.get("duty_cycle")),
            speed,
        )
    return table


def drive_footer(drive):
    grid = Table.grid(padding=(0, 1))
    grid.add_column()
    grid.add_row(Text.from_markup(
        "[dim]keys[/dim]  " + _held_keys(drive)
        + "    [dim]throttle[/dim] {0:+.2f}   [dim]turn[/dim] {1:+.2f}".format(
            drive.throttle, drive.turn)))

    rtt = _millis(drive.rtt)
    rtt_max = _millis(drive.rtt_max())
    trips = drive.watchdog_trips
    grid.add_row(Text.from_markup(
        "[dim]round trip[/dim] {0}   [dim]max/10s[/dim] {1}   "
        "[dim]watchdog trips[/dim] {2}".format(
            rtt, rtt_max,
            "[warn]" + str(trips) + "[/warn]" if trips else "0")))

    grid.add_row(Text.from_markup(
        "[dim]speed[/dim] {0}%   [dim]hold[/dim] {1:.0f}/{2:.0f} ms"
        "   [dim]slew[/dim] {3}%/loop".format(
            drive.speed, drive.initial_hold * 1000,
            drive.repeat_hold * 1000, drive.slew_limit)))

    grid.add_row(Text.from_markup(DRIVE_HELP))
    stalled = sorted(getattr(drive, "stalled", ()))
    if stalled:
        grid.add_row(Text.from_markup(
            "[fail]STALLED: " + ", ".join(stalled)
            + " - driven but not turning. Something is jamming it, or the "
            "motor has failed. A stalled motor draws heavy current.[/fail]"))
    if drive.last_error:
        grid.add_row(Text.from_markup(
            "[fail]" + _one_line(drive.last_error) + "[/fail]"))
    else:
        grid.add_row(Text(""))
    return Panel(grid, padding=(0, 1))


def drive_dashboard(drive):
    layout = Layout()
    layout.split_column(
        Layout(drive_header(drive), name="header", size=7),
        Layout(drive_motors_table(drive), name="motors", size=6),
        Layout(drive_footer(drive), name="footer", size=8),
    )
    return layout


# ---------------------------------------------------------------------
# gamepad
#
# The wizard renders a question rather than a dashboard, so every frame
# has to answer three things at once: what to do now, what the device is
# doing about it, and how far that is from being enough. The third is the
# one the approach this replaces could not show.
# ---------------------------------------------------------------------

GAMEPAD_HELP = (
    "[head]s[/head] skip step   "
    "[head]r[/head] redo step   "
    "[head]q[/head] abort"
)

BAR_WIDTH = 12


def _bar(fraction):
    """A progress bar that reads as done or not done at a glance."""
    filled = int(round(_clamp01(fraction) * BAR_WIDTH))
    body = "#" * filled + "-" * (BAR_WIDTH - filled)
    if fraction >= 1.0:
        return "[ok]" + body + "[/ok]"
    return "[warn]" + body + "[/warn]"


def _clamp01(value):
    if value is None or value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _axis_label(key):
    name = evdev_codes.code_name(key[0], key[1])
    if name is None:
        return "{0} {1}".format(evdev_codes.type_name(key[0]) or key[0],
                                key[1])
    return name


def gamepad_header(wizard):
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", justify="right")
    grid.add_column()
    session = wizard.session
    grid.add_row("host", text(session.hostname) + "  [dim]via[/dim] "
                 + text(session.host))
    device = wizard.device or {}
    if device:
        grid.add_row("device", text(device.get("name")) + "  [dim]"
                     + text(device.get("event")) + "[/dim]")
        grid.add_row("transport", _transport_markup(device))
        grid.add_row("ids", "[dim]phys[/dim] " + text(device.get("phys"))
                     + "   [dim]uniq[/dim] " + text(device.get("uniq"))
                     + "   [dim]bus[/dim] " + _bus_markup(device))
    else:
        grid.add_row("device", "[warn]waiting[/warn]")
    grid.add_row("steps", _step_ladder(wizard))
    grid.add_row("link", wizard.link_status() + "   [dim]events[/dim] "
                 + str(wizard.total_events))
    return Panel(grid, title="ev3ctl gamepad", title_align="left",
                 padding=(0, 1))


def _bus_markup(device):
    bus = device.get("bus")
    if bus is None:
        return DASH
    name = evdev_codes.bus_name(bus)
    shown = "0x{0:02x}".format(bus)
    if name:
        shown += " " + name
    return shown


def _transport_markup(device):
    """The transport, and a loud warning when it is the wrong one.

    A mapping captured over USB does not describe the Bluetooth link the
    vehicle will actually be driven over: the two use different HID
    report layouts, so the axis numbers can differ. Saying so here is
    cheaper than discovering it in a control loop.
    """
    transport = device.get("transport")
    agreement = device.get("transport_agreement")
    if transport == "bluetooth":
        shown = "[ok]Bluetooth[/ok]"
    elif transport == "usb":
        shown = ("[fail]USB - this mapping will apply to USB ONLY and "
                 "must be recaptured over Bluetooth before use[/fail]")
    else:
        shown = "[warn]unknown[/warn]"
    if agreement == "disagree":
        shown += ("  [warn]Bus and Phys disagree about this; trusting "
                  "Bus[/warn]")
    elif agreement in ("bus-only", "phys-only"):
        shown += "  [dim]({0})[/dim]".format(agreement)
    return shown


def _step_ladder(wizard):
    parts = []
    for index, title, _ in gamepad.STEPS:
        if index == wizard.step:
            parts.append("[sel] " + title + " [/sel]")
        elif index < wizard.step:
            parts.append("[ok]" + title + "[/ok]")
        else:
            parts.append("[dim]" + title + "[/dim]")
    return " [dim]>[/dim] ".join(parts)


def gamepad_codes_table(wizard):
    """Every code seen in this step, with its value and its range.

    Buttons and axes share the table on purpose. An operator who nudges
    a stick during the button step, or brushes the D-pad during a stick
    sweep, can see that it happened rather than wondering why the step
    will not advance.
    """
    table = Table(
        title="Seen in this step", title_justify="left",
        header_style="head", expand=True, padding=(0, 1),
    )
    table.add_column("Code", min_width=14)
    table.add_column("N", justify="right", width=5)
    table.add_column("Value", justify="right", width=8)
    table.add_column("Min", justify="right", width=7)
    table.add_column("Max", justify="right", width=7)
    table.add_column("Rest", justify="right", width=8)
    table.add_column("Toward -", width=BAR_WIDTH + 2)
    table.add_column("Toward +", width=BAR_WIDTH + 2)

    for key, entry in sorted(wizard.codes.items()):
        if entry["count"] == 0 and key[0] == evdev_codes.EV_KEY:
            continue
        rest_entry = wizard.rest.get(key, {})
        rest_mean = rest_entry.get("mean")
        low, high, _ = gamepad.axis_range(wizard.drivers.get(key), entry)
        if key[0] == evdev_codes.EV_ABS and low is not None:
            if wizard.step in gamepad.STICK_STEPS:
                down, up = _sweep_bars(entry, rest_mean, low, high)
            else:
                up, down = gamepad.trigger_progress(entry, low, high)
                down, up = _bar(down), _bar(up)
        else:
            down, up = DASH, DASH
        table.add_row(
            _axis_label(key)
            + (" [dim]hat[/dim]" if evdev_codes.is_hat(*key) else ""),
            str(key[1]),
            number(entry["latest"]),
            number(entry["min"]),
            number(entry["max"]),
            number(rest_mean, 1) if rest_mean is not None else DASH,
            down, up,
        )
    if not wizard.codes:
        table.add_row("[empty]nothing yet[/empty]", DASH, DASH, DASH,
                      DASH, DASH, DASH, DASH)
    return table


def _sweep_bars(entry, rest_mean, low, high):
    up, down = gamepad.sweep_progress(entry, rest_mean, low, high)
    if gamepad.too_coarse(low, high):
        # A hat declares a range of 2 and would otherwise show two full
        # bars the instant it is brushed, which reads as a passing stick.
        return "[dim]too coarse[/dim]", "[dim]too coarse[/dim]"
    return _bar(down), _bar(up)


def gamepad_footer(wizard):
    grid = Table.grid(padding=(0, 1))
    grid.add_column()
    grid.add_row(Text.from_markup(
        "[head]" + wizard.step_title() + "[/head]  "
        + wizard.instruction()))
    grid.add_row(Text.from_markup(_condition(wizard)))
    grid.add_row(Text.from_markup(GAMEPAD_HELP))
    if wizard.blocked:
        grid.add_row(Text.from_markup(
            "[fail]" + _one_line(wizard.blocked) + "[/fail]"))
    elif wizard.last_error:
        grid.add_row(Text.from_markup(
            "[fail]" + _one_line(wizard.last_error) + "[/fail]"))
    else:
        grid.add_row(Text(""))
    return Panel(grid, padding=(0, 1))


def _condition(wizard):
    """The advance condition, and how close this step is to meeting it."""
    step = wizard.step
    if wizard.device is None:
        return ("[dim]advances when[/dim] the controller appears. "
                "[warn]Waiting - press PS.[/warn]")
    if wizard.device_gone:
        # The known failure on this controller: it powers itself off
        # when the pack is low. Saying which it is saves the operator
        # debugging Bluetooth when the answer is a charger.
        return ("[fail]The controller has disconnected. It powers itself "
                "off when its battery is low - put it on a charger, then "
                "press PS and r to redo this step.[/fail]")
    if step == gamepad.REST:
        return ("[dim]advances after[/dim] {0:.0f}s of continuous data "
                "{1}".format(gamepad.REST_SECONDS,
                             _bar(wizard.rest_progress(time.monotonic()))))
    if step in gamepad.STICK_STEPS:
        found = gamepad.qualifying_axes(
            wizard.codes, wizard.drivers, wizard.rest)
        if step == gamepad.RIGHT:
            previous = wizard.step_axes.get(gamepad.LEFT, ())
            if wizard.wrong_stick:
                return ("[fail]Those are the same two axes step 2 found. "
                        "That is most likely the LEFT stick again - try "
                        "the right one, or press r to redo.[/fail]")
            found = [key for key in found if key not in previous]
        names = ", ".join(_axis_label(key) for key in found) or "none yet"
        return ("[dim]advances when[/dim] exactly 2 axes have swept 80% "
                "of their range both ways   [dim]so far[/dim] {0}/2 "
                "[ok]{1}[/ok]".format(len(found), names))
    if step in gamepad.TRIGGER_STEPS:
        found = gamepad.qualifying_triggers(
            wizard.codes, wizard.drivers, exclude=wizard.claimed())
        names = ", ".join(_axis_label(key) for key in found) or "none yet"
        return ("[dim]advances when[/dim] 1 new axis spans its range   "
                "[dim]so far[/dim] {0}/1 [ok]{1}[/ok]".format(
                    len(found), names))
    if step == gamepad.BUTTONS:
        prompt = wizard.button_prompt()
        if prompt is None:
            return ("[ok]Every prompt recorded.[/ok] "
                    "[dim]press s to go on[/dim]")
        return ("[head]Press: {0}[/head]   [dim]{1} of {2} recorded - "
                "this step advances only when you press s[/dim]".format(
                    prompt, len(wizard.buttons),
                    len(gamepad.BUTTON_PROMPTS)))
    if step == gamepad.SUMMARY:
        if wizard.written_to:
            return "[ok]Written to " + wizard.written_to + "[/ok]"
        return "[fail]Nothing was written.[/fail]"
    return ""


def gamepad_dashboard(wizard):
    layout = Layout()
    layout.split_column(
        Layout(gamepad_header(wizard), name="header", size=10),
        Layout(gamepad_codes_table(wizard), name="codes"),
        Layout(gamepad_footer(wizard), name="footer", size=7),
    )
    return layout


# -- the summary, printed after the alternate screen has gone ---------

def gamepad_summary_table(wizard):
    table = Table(
        title="Axes", title_justify="left", header_style="head",
        expand=True, padding=(0, 1),
    )
    table.add_column("Axis", min_width=12)
    table.add_column("Code", justify="right", width=5)
    table.add_column("Control", min_width=12)
    table.add_column("Min", justify="right", width=7)
    table.add_column("Max", justify="right", width=7)
    table.add_column("Rest", justify="right", width=8)
    table.add_column("Spread", justify="right", width=7)
    table.add_column("Deadzone", justify="right", width=9)
    table.add_column("Flat", justify="right", width=6)
    table.add_column("Source", width=9)

    for record in wizard.mapping()["axes"]:
        control = record["control"]
        if control is None:
            control = "[empty]unassigned[/empty]"
        elif record.get("continuous") == "extremes-only":
            control += " [fail]digital[/fail]"
        elif record.get("continuous") == "continuous":
            control += " [ok]analog[/ok]"
        elif record.get("continuous") == "few":
            control += " [warn]few steps[/warn]"
        table.add_row(
            text(record["name"]),
            str(record["code"]),
            control,
            number(record["observed_min"]),
            number(record["observed_max"]),
            number(record["rest_mean"], 1),
            number(record["rest_spread"]),
            number(record["suggested_deadzone"]),
            number(record["driver_flat"]),
            text(record["range_source"]),
        )
    return table


def gamepad_buttons_table(wizard):
    table = Table(
        title="Buttons", title_justify="left", header_style="head",
        expand=True, padding=(0, 1),
    )
    table.add_column("Asked for", min_width=14)
    table.add_column("Type", width=8)
    table.add_column("Code", justify="right", width=6)
    table.add_column("Name", min_width=16)
    table.add_column("Value", justify="right", width=7)

    records = wizard.mapping()["buttons"]
    if not records:
        table.add_row("[empty]none recorded[/empty]", DASH, DASH, DASH,
                      DASH)
        return table
    for record in records:
        name = text(record["name"])
        if record["alias"]:
            name += " [dim]= " + record["alias"] + "[/dim]"
        table.add_row(
            text(record["label"]),
            text(record["type"]),
            str(record["code"]),
            name,
            number(record["value"]),
        )
    return table
