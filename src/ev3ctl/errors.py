"""HOST CODE. Failures described in terms the operator can act on.

The rule this module exists to enforce: when the tool cannot reach the
brick, it says which cable, which port and which command, and it never
prints a traceback. A traceback names the line that gave up. The
operator needs to know whether the cable is in the PC port.
"""

DEFAULT_CHECKLIST = (
    "the mini-USB cable is in the brick's PC port, not the USB-A host "
    "port, and in the Mac",
    "the brick is powered on and has finished booting to the Brickman "
    "menu",
    "the wired connection is up in Brickman, under "
    "Wireless and Networks / All Network Connections",
    "`ping ev3dev.local` answers",
    "`ssh robot@ev3dev.local` connects (password: maker), and "
    "`ssh-copy-id robot@ev3dev.local` has been run once",
)


class Diagnosis(object):
    """One failure, in four parts: what, why, evidence, what to do."""

    def __init__(self, summary, cause, command=None, detail=None,
                 checklist=None):
        self.summary = summary
        self.cause = cause
        self.command = command
        self.detail = detail
        self.checklist = tuple(checklist or DEFAULT_CHECKLIST)


class LinkError(Exception):
    """The link to the brick could not be established or was lost."""

    def __init__(self, diagnosis):
        Exception.__init__(self, diagnosis.summary)
        self.diagnosis = diagnosis


class AgentError(Exception):
    """The agent ran, understood the command, and refused it.

    Distinct from LinkError on purpose. The link being fine and the
    motor being absent are different problems with different fixes, and
    only one of them is about a cable.
    """

    def __init__(self, message, kind="error", request=None):
        Exception.__init__(self, message)
        self.kind = kind
        self.request = request


def quote_command(argv):
    """Render an argv list the way a person would have typed it."""
    parts = []
    for item in argv:
        if any(character in item for character in " \t\"'$"):
            parts.append("'" + item.replace("'", "'\\''") + "'")
        else:
            parts.append(item)
    return " ".join(parts)
