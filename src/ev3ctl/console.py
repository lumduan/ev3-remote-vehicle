"""HOST CODE. Every byte this tool prints goes through here.

One Console, one Theme, one place that knows what a warning looks like.
Keeping output in a single module is what lets the tool stay readable
when it is piped to a file: rich drops colour on its own when the stream
is not a terminal, and there is no second code path printing raw escape
sequences behind its back.
"""

from rich.console import Console
from rich.theme import Theme

THEME = Theme(
    {
        "ok": "bold green",
        "fail": "bold red",
        "warn": "bold yellow",
        "hint": "cyan",
        "dim": "dim",
        "unit": "dim",
        "empty": "dim italic",
        "sel": "bold reverse",
        "head": "bold",
    }
)

console = Console(theme=THEME, highlight=False)
error_console = Console(theme=THEME, stderr=True, highlight=False)


def ok(message):
    console.print("[ok]OK[/ok]   " + message)


def warn(message):
    console.print("[warn]WARN[/warn] " + message)


def fail(message):
    error_console.print("[fail]FAIL[/fail] " + message)


def info(message):
    console.print("     " + message)


def hint(message):
    console.print("     [hint]" + message + "[/hint]")


def show_diagnosis(diagnosis):
    """Print a failure as what happened, why, and what to do about it.

    A traceback tells the reader where the program gave up. It does not
    tell them which end of a USB cable to look at, which is the only
    thing they can act on.
    """
    out = error_console
    out.print()
    out.print("[fail]" + diagnosis.summary + "[/fail]")
    out.print()
    if diagnosis.command:
        out.print("  [dim]command:[/dim] " + diagnosis.command)
        out.print()
    out.print("  " + diagnosis.cause)
    if diagnosis.detail:
        out.print()
        for line in diagnosis.detail.strip().splitlines():
            out.print("  [dim]|[/dim] " + line)
    out.print()
    out.print("  [hint]Check, in this order:[/hint]")
    for step in diagnosis.checklist:
        out.print("    - " + step)
    out.print()
