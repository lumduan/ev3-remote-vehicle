"""HOST CODE. Non-blocking key reading, and putting the terminal back.

cbreak rather than raw, deliberately. Raw mode clears ISIG, which turns
Ctrl-C into a 0x03 byte this module would have to recognise and turn
back into an interrupt by hand. cbreak clears canonical mode and echo -
the two things that stop keys arriving one at a time - and leaves ISIG
alone, so Ctrl-C keeps travelling the ordinary signal path and reaches
the loop as KeyboardInterrupt. One less mechanism, and the interrupt
still works if this module has a bug in it.

Escape sequences are parsed out of whatever os.read returned, never by
blocking for the rest of a sequence. A partial sequence stays in the
buffer until the next read. Waiting on the tty for the tail of an arrow
key would hand the terminal a way to stall the render loop.
"""

import os
import select
import sys
import termios
import tty

ARROWS = {
    b"A": "UP",
    b"B": "DOWN",
    b"C": "RIGHT",
    b"D": "LEFT",
}

NAMED = {
    b" ": "SPACE",
    b"\x03": "CTRL_C",
    b"\r": "ENTER",
    b"\n": "ENTER",
    b"\x7f": "BACKSPACE",
}

READ_CHUNK = 1024


class NotATerminal(Exception):
    """stdin is a pipe or a file, so there are no keys to read."""


class Keyboard(object):
    """The tty in cbreak mode, restored whatever happens."""

    def __init__(self, stream=None):
        self.stream = stream or sys.stdin
        try:
            self.fd = self.stream.fileno()
        except (AttributeError, ValueError):
            self.fd = -1
        self._saved = None
        self._buffer = b""

    @property
    def is_tty(self):
        return self.fd >= 0 and os.isatty(self.fd)

    def fileno(self):
        return self.fd

    def open(self):
        if not self.is_tty:
            raise NotATerminal(
                "stdin is not a terminal, so the interactive dashboard "
                "has nothing to read keys from"
            )
        self._saved = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)

    def restore(self):
        """Put the terminal back. Safe to call twice, never raises.

        This runs first in every teardown path, before anything that
        talks to the brick, because it is local and instant while the
        link may already be dead. A shell left in cbreak is a worse
        outcome than a motor left running for the one second it takes
        the agent's watchdog to notice.
        """
        if self._saved is None:
            return
        try:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self._saved)
        except Exception:
            pass
        self._saved = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc_info):
        self.restore()
        return False

    def read(self):
        """Every key pressed since the last call. Never blocks."""
        if self.fd < 0:
            return []
        ready, _, _ = select.select([self.fd], [], [], 0)
        if not ready:
            return []
        try:
            chunk = os.read(self.fd, READ_CHUNK)
        except OSError:
            return []
        if not chunk:
            return []
        self._buffer += chunk
        return self._drain()

    def _drain(self):
        buffer = self._buffer
        keys = []
        index = 0
        while index < len(buffer):
            byte = buffer[index:index + 1]
            if byte != b"\x1b":
                keys.append(_plain(byte))
                index += 1
                continue
            consumed, key = _escape(buffer, index)
            if consumed == 0:
                # Incomplete sequence. Leave it for the next read rather
                # than reporting a bare ESC that the operator did press
                # but did not mean.
                break
            if key is not None:
                keys.append(key)
            index += consumed
        self._buffer = buffer[index:]
        return keys


def _plain(byte):
    if byte in NAMED:
        return NAMED[byte]
    return byte.decode("utf-8", "replace").lower()


def _escape(buffer, index):
    """Parse one escape sequence. Returns (bytes consumed, key or None).

    Zero consumed means the sequence is not all here yet.
    """
    if len(buffer) - index < 3:
        return 0, None
    introducer = buffer[index + 1:index + 2]
    if introducer not in (b"[", b"O"):
        # An escape followed by something else. Drop the escape and let
        # the next byte be read as an ordinary key.
        return 1, None
    final = buffer[index + 2:index + 3]
    if final in ARROWS:
        return 3, ARROWS[final]
    # Some other CSI sequence - a function key, a mouse report. Skip to
    # its terminating byte so its payload is not read as keystrokes.
    position = index + 2
    while position < len(buffer):
        if 0x40 <= buffer[position] <= 0x7E:
            return position - index + 1, None
        position += 1
    return 0, None
