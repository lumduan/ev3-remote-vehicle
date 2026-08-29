"""HOST CODE. ev3ctl - host tooling for the EV3 remote vehicle.

Runs on macOS under CPython 3.12 or later. Nothing in this package is
ever executed on the EV3 brick; the brick's half of the tool lives in
``agent/`` at the repository root, is copied there at startup, and is
never imported from here. See ``agent/README.md``.
"""

__version__ = "0.1.0"

__all__ = ["__version__", "main"]


def main(argv=None):
    """Console-script entry point for ``ev3ctl``.

    The real parser lives in ``ev3ctl.cli`` and is imported here rather
    than at module scope, so that importing ``ev3ctl`` costs nothing and
    does not pull in rich.
    """
    from .cli import main as cli_main

    return cli_main(argv)
