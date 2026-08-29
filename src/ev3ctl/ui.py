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

from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

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
    table.add_column("Address", width=8)
    table.add_column("Driver")
    table.add_column("Mode", width=14)
    table.add_column("Status")
    rows = sorted(inventory.ports.items())
    if not rows:
        table.add_row("[empty]none[/empty]",
                      "[empty]/sys/class/lego-port is absent or empty"
                      "[/empty]", "", "")
        return table
    for address, port in rows:
        table.add_row(
            text(address),
            text(port.get("driver_name")),
            text(port.get("mode")),
            text(port.get("status")),
        )
    return table
