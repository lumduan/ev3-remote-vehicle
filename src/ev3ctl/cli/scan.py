"""HOST CODE. `ev3ctl scan` - one inventory, printed once, then exit.

The non-interactive half of the tool. It exists so that the first thing
anyone runs against a new brick is something that cannot leave a motor
turning and cannot leave a terminal in cbreak mode: it connects, reads,
prints and goes away.
"""

from .. import ui
from ..console import console
from ..session import connect


def run(args):
    session = connect(
        host=args.host,
        agent_source=args.agent,
        timeout=args.timeout,
        multiplex=not args.no_multiplex,
    )
    try:
        inventory = session.scan()
        console.print(ui.header(session, link_status="one-shot scan"))
        console.print()
        console.print(ui.motors_table(inventory, session.snapshot))
        console.print()
        console.print(ui.sensors_table(inventory, session.snapshot))
        console.print()
        console.print(ui.ports_table(inventory))
        console.print()
        console.print(ui.battery_line(session.snapshot, inventory))
        console.print()
    finally:
        # Nothing here commands a motor, so this is belt and braces. It
        # also leaves the brick in a known state for whatever runs next,
        # which matters when the previous run was `live` and ended badly.
        session.shutdown()
    return 0
