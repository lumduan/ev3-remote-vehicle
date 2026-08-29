"""HOST CODE. Argument parsing, dispatch, and the last line of defence.

Every exception that reaches this module is turned into something the
operator can act on. Nothing escapes as a traceback: a stack trace names
the line that gave up, and the operator's next move is about a cable.
"""

import argparse

from .. import __version__
from ..console import fail, hint
from ..console import show_diagnosis
from ..errors import AgentError, LinkError
from ..keys import NotATerminal
from ..link import DEFAULT_HOST

EXIT_OK = 0
EXIT_LINK = 2
EXIT_AGENT = 3
EXIT_USAGE = 4
EXIT_INTERRUPTED = 130

DESCRIPTION = """\
Live view of every motor and sensor attached to an ev3dev EV3 brick,
over SSH on the USB link, with interactive motor control for checking
what is plugged into which port.
"""

EPILOG = """\
The brick is reached over USB, never Bluetooth: the brick's single
Bluetooth radio is reserved for the gamepad. Run ssh-copy-id once so
that no command has to stop and ask for a password.
"""


def common_options():
    """Options every subcommand takes, before or after the subcommand."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--host", default=DEFAULT_HOST, metavar="USER@HOST",
        help="brick to connect to (default: %(default)s)",
    )
    parser.add_argument(
        "--agent", metavar="PATH",
        help="agent source to copy to the brick "
             "(default: agent/ev3_agent.py in the repository)",
    )
    parser.add_argument(
        "--timeout", type=float, metavar="SECONDS",
        help="how long to wait for one response from the brick",
    )
    parser.add_argument(
        "--no-multiplex", action="store_true",
        help="do not share one SSH connection between the file copy and "
             "the agent session",
    )
    return parser


def build_parser():
    common = common_options()
    parser = argparse.ArgumentParser(
        prog="ev3ctl", parents=[common], description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version",
        version="ev3ctl {0}".format(__version__),
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    subparsers.add_parser(
        "scan", parents=[common],
        help="print one inventory of every port and exit",
        description="Print one static inventory of every output port, "
                    "every input port and the battery, then exit.",
    )
    subparsers.add_parser(
        "live", parents=[common],
        help="interactive dashboard (the default)",
        description="Live dashboard at 5 Hz with interactive motor "
                    "control. This is what runs when no command is given.",
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "live"

    # Imported here rather than at module scope so that --help and
    # --version stay instant and do not pay for rich's import.
    from . import live as live_command
    from . import scan as scan_command

    runner = scan_command.run if command == "scan" else live_command.run

    try:
        return runner(args)
    except LinkError as exc:
        show_diagnosis(exc.diagnosis)
        return EXIT_LINK
    except AgentError as exc:
        fail(str(exc))
        hint("The link is fine; the brick refused that command.")
        return EXIT_AGENT
    except NotATerminal as exc:
        fail(str(exc))
        hint("Use `ev3ctl scan` when there is no terminal, for example "
             "in a pipeline or a script.")
        return EXIT_USAGE
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
