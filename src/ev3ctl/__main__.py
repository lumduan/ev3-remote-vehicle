"""HOST CODE. ``python -m ev3ctl``, equivalent to the ev3ctl command.

Useful when the console script is not on PATH, which is every checkout
that has not been installed yet.
"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
