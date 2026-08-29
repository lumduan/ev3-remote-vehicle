"""ev3ctl - host-side tooling for the EV3 remote vehicle.

Runs on macOS under CPython 3.12 or later. Nothing in this package is
ever executed on the EV3 brick; see ``agent/README.md`` for the other
half of the split.

This module is the scaffold only. No hardware access, no transport and
no rendering exist yet. See ROADMAP.md, Phase 2.
"""

__version__ = "0.1.0"

__all__ = ["__version__", "main"]


def main() -> int:
    """Console-script entry point.

    Deliberately does nothing yet. It exists so that the packaging is
    real and testable from the first commit: ``uv run ev3ctl`` must
    exit cleanly rather than raise. Phase 2 replaces this body with the
    argument parser and the ``scan`` and ``live`` subcommands.
    """
    print("ev3ctl {}: scaffold only, no commands yet.".format(__version__))
    print("See ROADMAP.md, Phase 2, for what lands here next.")
    return 0
