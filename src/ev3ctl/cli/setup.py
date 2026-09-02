"""HOST CODE. `ev3ctl setup` - the menu for somebody's first robot.

Written for a child sitting alone at the keyboard. It never shows a
command or a file path unless it is offering to run it, and when
something is wrong it names a thing to go and touch: a cable, a button,
a switch. The technical version of the same failure is one keypress away
behind "Something is wrong", so an adult is not locked out of it.

**This is the one subcommand that must not connect at startup.** Every
other one opens with `connect(...)` and raises `LinkError` if the brick
does not answer - which is right for them and wrong here, because
diagnosing a brick that will not answer is most of this wizard's job.
The menu appears first; each item reaches for the brick only when it
needs to.
"""

import os
import select
import subprocess
import sys

from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .. import setup_checks as checks
from ..console import console
from ..keys import Keyboard, NotATerminal
from ..messages import (
    LANGUAGES,
    load_language,
    save_language,
    t,
)

# The menu, in the order a first-time setup actually happens.
ITEMS = ("check", "install", "drive", "buttons", "battery", "help",
         "language", "quit")


class Wizard(object):
    """The menu, what it knows so far, and which language it speaks."""

    def __init__(self, host):
        self.host = host
        self.language = load_language()
        self.selected = 0
        self.quit = False
        # Nothing is known until item 1 is run. "not checked" is an
        # honest thing to show; a green tick before anything was looked
        # at would not be.
        self.link = checks.UNKNOWN
        self.motors = checks.UNKNOWN
        self.gamepad = checks.UNKNOWN
        self.programs = checks.UNKNOWN
        self.programs_state = None
        self.buttons_on = False
        self.last_detail = {}
        self.message = None

    # -- words --------------------------------------------------------

    def say(self, key, *args):
        return t(key, self.language, *args)

    def status_of(self, item):
        """The right-hand column for one menu row, or None."""
        if item == "check":
            worst = [self.link, self.motors, self.gamepad]
            if checks.UNKNOWN in worst and checks.BAD not in worst:
                return self.say("status.unknown"), "dim"
            if checks.BAD in worst:
                return self.say("status.bad"), "fail"
            return self.say("status.ok"), "ok"
        if item == "install":
            if self.programs == checks.UNKNOWN:
                return self.say("status.unknown"), "dim"
            if self.programs_state == "ready":
                return self.say("status.ready"), "ok"
            if self.programs_state == "old":
                return self.say("status.old"), "warn"
            return self.say("status.missing"), "fail"
        if item == "buttons":
            if self.buttons_on:
                return self.say("status.on"), "ok"
            return self.say("status.off"), "dim"
        if item == "language":
            return ("English" if self.language == "en" else "ไทย"), "dim"
        return None

    # -- the frame ----------------------------------------------------

    def render(self):
        grid = Table.grid(padding=(0, 2))
        grid.add_column(width=3)
        grid.add_column(min_width=34)
        grid.add_column(justify="right", min_width=12)
        for index, item in enumerate(ITEMS):
            label = self.say("menu." + item)
            status = self.status_of(item)
            shown = ""
            if status is not None:
                shown = "[{1}]{0}[/{1}]".format(status[0], status[1])
            if index == self.selected:
                grid.add_row("[sel] > [/sel]",
                             "[sel] " + label + " [/sel]", shown)
            else:
                grid.add_row("", label, shown)

        body = Table.grid(padding=(0, 1))
        body.add_column()
        body.add_row(Text.from_markup("[dim]" + self.say("app.subtitle")
                                      + "[/dim]"))
        body.add_row(Text(""))
        body.add_row(grid)
        body.add_row(Text(""))
        if self.message:
            body.add_row(Text.from_markup(self.message))
            body.add_row(Text(""))
        body.add_row(Text.from_markup(
            "[dim]" + self.say("menu.hint") + "[/dim]"))
        return Panel(body, title=self.say("app.title"),
                     title_align="left", padding=(1, 2))

    # -- keys ---------------------------------------------------------

    def handle(self, key):
        """One keypress. Returns the chosen item name, or None."""
        if key == "UP":
            self.selected = (self.selected - 1) % len(ITEMS)
        elif key == "DOWN":
            self.selected = (self.selected + 1) % len(ITEMS)
        elif key == "ENTER":
            return ITEMS[self.selected]
        elif key == "q":
            self.quit = True
        elif key.isdigit() and key != "0":
            index = int(key) - 1
            if index < len(ITEMS):
                self.selected = index
                return ITEMS[index]
        return None


# ---------------------------------------------------------------------
# What each menu item does
#
# Each one leaves the full-screen menu first, prints in the ordinary
# scrolling terminal, and waits for Enter. That is deliberate: a child
# reading a page of advice should be able to scroll back to it, and a
# full-screen frame that redraws would take it away.
# ---------------------------------------------------------------------

def do_check(wizard):
    console.print()
    console.print("[head]" + wizard.say("check.title") + "[/head]")
    console.print()

    rows = (
        ("check.cable", "link", checks.check_link, "fix.cable"),
        ("check.motors", "motors", checks.check_motors, "fix.motors"),
        ("check.pad", "gamepad", checks.check_gamepad, "fix.pad"),
    )
    for label, attr, probe, fix in rows:
        console.print("  " + wizard.say(label) + " ...")
        verdict, detail = probe(wizard.host)
        setattr(wizard, attr, verdict)
        wizard.last_detail[attr] = detail
        _line(wizard, label, verdict)
        if verdict != checks.OK:
            console.print()
            console.print("      [hint]" + wizard.say(fix) + "[/hint]")
            if attr == "link":
                console.print("      [hint]"
                              + wizard.say("fix.brick") + "[/hint]")
                console.print("      [hint]"
                              + wizard.say("fix.brickman") + "[/hint]")
            console.print()
            _wait(wizard)
            return

    verdict, state, detail = checks.check_programs(wizard.host)
    wizard.programs = verdict
    wizard.programs_state = state
    wizard.last_detail["programs"] = detail
    _line(wizard, "check.programs", verdict)
    if verdict != checks.OK:
        console.print()
        console.print("      [hint]" + wizard.say("fix.programs")
                      + "[/hint]")
    else:
        console.print()
        console.print("  [ok]" + wizard.say("check.allgood") + "[/ok]")
    console.print()
    _wait(wizard)


def _line(wizard, label, verdict):
    mark = "[ok]ok[/ok]" if verdict == checks.OK else "[fail]x[/fail]"
    console.print("  {0}  {1}".format(mark, wizard.say(label)))


def do_install(wizard):
    console.print()
    console.print("[head]" + wizard.say("install.title") + "[/head]")
    console.print()
    verdict, _ = checks.check_link(wizard.host)
    if verdict != checks.OK:
        console.print("  [fail]" + wizard.say("install.nolink")
                      + "[/fail]")
        console.print()
        _wait(wizard)
        return
    for name in checks.PROGRAMS:
        console.print("  " + wizard.say("install.copying", name))
    ok, detail = checks.install(wizard.host)
    console.print()
    if ok:
        wizard.programs = checks.OK
        wizard.programs_state = "ready"
        console.print("  [ok]" + wizard.say("install.done") + "[/ok]")
    else:
        wizard.programs = checks.BAD
        console.print("  [fail]"
                      + wizard.say("install.failed", detail) + "[/fail]")
    console.print()
    _wait(wizard)


def do_drive(wizard):
    console.print()
    console.print("[head]" + wizard.say("drive.title") + "[/head]")
    console.print()
    console.print("  [warn]" + wizard.say("drive.wheels") + "[/warn]")
    console.print("  " + wizard.say("drive.how"))
    console.print()
    console.print("  " + wizard.say("drive.starting"))
    _run_on_brick(wizard, "tank_drive.py")
    console.print("  " + wizard.say("drive.running"))
    console.print()
    _wait(wizard)


def do_buttons(wizard):
    console.print()
    console.print("[head]" + wizard.say("buttons.title") + "[/head]")
    console.print()
    console.print("  " + wizard.say("buttons.what"))
    console.print()
    # pad_buttons is its own toggle: a second launch stops the first.
    _run_on_brick(wizard, "pad_buttons.py")
    wizard.buttons_on = not wizard.buttons_on
    key = "buttons.turnedon" if wizard.buttons_on else "buttons.turnedoff"
    console.print("  [ok]" + wizard.say(key) + "[/ok]")
    console.print()
    _wait(wizard)


def _run_on_brick(wizard, program):
    """Start one brick program and leave it running."""
    target = os.path.join(checks.BRICK_DIR, program)
    argv = ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
            wizard.host, "nohup {0} >/dev/null 2>&1 &".format(target)]
    try:
        subprocess.run(argv, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=30)
    except Exception as exc:
        console.print("  [fail]" + str(exc) + "[/fail]")


def do_battery(wizard):
    console.print()
    console.print("[head]" + wizard.say("battery.title") + "[/head]")
    console.print()
    ok, out = checks._ssh(
        wizard.host,
        "cat /sys/class/power_supply/lego-ev3-battery/voltage_now; "
        "for d in /sys/class/power_supply/*/; do "
        "[ \"$(cat $d/scope 2>/dev/null)\" = Device ] && "
        "cat $d/capacity; done")
    lines = out.splitlines() if ok else []
    if lines:
        try:
            volts = int(lines[0]) / 1000000.0
            console.print("  {0}  {1:.2f} V".format(
                wizard.say("battery.robot"), volts))
        except ValueError:
            pass
    if len(lines) > 1:
        console.print("  {0}  {1}%".format(
            wizard.say("battery.pad"), lines[1].strip()))
    else:
        console.print("  [dim]" + wizard.say("battery.nopad") + "[/dim]")
    console.print()
    _wait(wizard)


def do_help(wizard):
    console.print()
    console.print("[head]" + wizard.say("help.title") + "[/head]")
    console.print()
    console.print("  " + wizard.say("help.intro"))
    console.print()
    for number in range(1, 6):
        console.print("  {0}. {1}".format(
            number, wizard.say("help." + str(number))))
    console.print()
    if wizard.last_detail:
        console.print("  [dim]" + wizard.say("help.details") + "[/dim]")
    console.print()
    keys = _wait(wizard, extra=("d",))
    if "d" in keys:
        console.print()
        for name, detail in sorted(wizard.last_detail.items()):
            console.print("  [dim]{0}:[/dim] {1}".format(name, detail))
        console.print()
        _wait(wizard)


def do_language(wizard):
    index = LANGUAGES.index(wizard.language)
    wizard.language = LANGUAGES[(index + 1) % len(LANGUAGES)]
    save_language(wizard.language)


ACTIONS = {
    "check": do_check,
    "install": do_install,
    "drive": do_drive,
    "buttons": do_buttons,
    "battery": do_battery,
    "help": do_help,
    "language": do_language,
}


# ---------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------

def _wait(wizard, extra=()):
    """Block until Enter, or one of `extra`. Returns the keys seen.

    A plain blocking read: select with no timeout, then the non-blocking
    `Keyboard.read`. That is the idiom the three existing loops use with
    a timeout; without a link to watch there is nothing to time out for,
    and it costs no CPU.
    """
    console.print("  [dim]" + wizard.say("common.back") + "[/dim]")
    keyboard = Keyboard()
    if not keyboard.is_tty:
        return ()
    keyboard.open()
    seen = []
    try:
        while True:
            select.select([keyboard.fileno()], [], [])
            for key in keyboard.read():
                seen.append(key)
                if key == "ENTER" or key in extra:
                    return tuple(seen)
    finally:
        keyboard.restore()


def run(args):
    keyboard = Keyboard()
    if not keyboard.is_tty:
        raise NotATerminal(
            "stdin is not a terminal, so the setup menu has nothing to "
            "read keys from"
        )
    wizard = Wizard(args.host)

    while not wizard.quit:
        chosen = _menu(wizard, keyboard)
        if chosen is None or chosen == "quit":
            break
        action = ACTIONS.get(chosen)
        if action is not None:
            action(wizard)
    console.print()
    return 0


def _menu(wizard, keyboard):
    """Show the menu until something is chosen. Returns its name."""
    keyboard.open()
    try:
        with Live(wizard.render(), console=console, screen=True,
                  refresh_per_second=8, transient=False) as live:
            while True:
                select.select([keyboard.fileno()], [], [])
                for key in keyboard.read():
                    chosen = wizard.handle(key)
                    if wizard.quit:
                        return None
                    if chosen is not None:
                        if chosen == "language":
                            # Redrawn in place: switching language is
                            # not worth leaving the menu for.
                            do_language(wizard)
                            live.update(wizard.render())
                            continue
                        return chosen
                live.update(wizard.render())
    finally:
        # Local, instant, and before anything that talks to the brick.
        keyboard.restore()


if __name__ == "__main__":
    sys.exit(0)
