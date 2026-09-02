# agent/

**Everything in this directory runs on the EV3 brick, not on the Mac.**

The brick boots ev3dev-stretch, which is Debian 9. Its system Python 3
is old, its CPU is a 300 MHz ARM9, and it has 64 MB of RAM. Nothing is
installed on it beyond the stock image, and it has no internet access,
so nothing can be installed on it either.

That gives this directory two absolute rules.

## Rule 1: Python 3.5, standard library only

Every file here must run under **CPython 3.5 with the standard library
and nothing else**. No `pip install` on the brick, ever.

Forbidden here, even though they are fine under `src/`:

| Not allowed | Why |
| --- | --- |
| any third-party import | nothing is installed on the brick |
| ...except `ev3dev2`, for the LCD only | see below |
| `ev3dev`, `ev3dev2`, `rich` | third-party; talk to sysfs directly |
| f-strings | 3.5 does not have them; use `.format()` |
| the `=` specifier in f-strings | 3.8 |
| `dataclasses` | 3.7 |
| the walrus operator `:=` | 3.8 |
| `typing` at runtime | keep hints in comments |
| `subprocess.run(capture_output=...)` | 3.7 |
| `pathlib` conveniences added after 3.5 | read sysfs with `open()` |

### The one exception: `ev3dev2`, for the LCD

`agent/tank_drive.py` imports `ev3dev2.display` and `ev3dev2.fonts`.
Nothing else may, and nothing may import anything else from it.

The rule above exists because nothing is installed on the brick and
there is no pip to install it with. `ev3dev2` is different: it ships in
the stock ev3dev image, at `/usr/lib/python3/dist-packages/ev3dev2/`, so
the reason does not apply to it.

It is there because every standard-library route to the screen was tried
first and measured on 2026-09-01:

- **`print()` to the console works**, and is what the operator sees when
  a program is launched from Brickman, whose `conrun` console is the
  LCD. But that console is 44x21 characters in a 4x6 pixel font, and
  making it bigger needs `setfont`, which needs root.
- **Writing `/dev/fb0` directly works** while nothing else is drawing,
  and loses otherwise: fbcon owns whichever console is being displayed
  and repaints over anything put underneath it.
- **The documented fix is `chvt`** to an unused console. That is root
  only, `sudo` here wants a password, and unbinding fbcon through
  `/sys/class/vtconsole/vtcon1/bind` is root only as well.

The import is guarded and the code falls back to `print()` when it
fails, so a brick without `ev3dev2` still drives. Driving is the point;
the screen is a convenience.

One trap, since it cost an hour: the module is **`ev3dev2.fonts`**,
plural. `ev3dev2.font` does not exist, and importing it raises an
ImportError that a bare `except` will hide - which is exactly what
happened, leaving `HAVE_DISPLAY` quietly False and the screen blank with
no error anywhere.

Type hints go in comments, not annotations:

```python
def read_attr(path, name):
    # type: (str, str) -> str or None
    ...
```

### `pad_buttons.py` writes to an input device

Every other program here reads hardware. `agent/pad_buttons.py` writes
to one: it injects key events into the brick's own button device, so the
gamepad's D-pad, Cross and Share act as the brick's arrows, centre and
Back.

That is not a trick. `evdev_write` in the kernel calls
`input_inject_event`, so an event written to an event node arrives
exactly as if the button had been pressed. Verified on this brick on
2026-09-02: the events read back out of the device, and Brickman redrew
its menu in response.

Two consequences worth stating plainly:

- **An injected key stays pressed until something releases it.** That is
  the same shape of hazard as a latched motor, and it takes the same
  answer: every path out of the program releases every key it holds. The
  one that matters is Back, which walks the brick into shutdown on its
  own.
- **An injected Back is a SIGTERM to whatever Brickman launched**, since
  that is what Brickman's own Back button does. That is the point of it,
  and it is also why it can stop `tank_drive.py` mid-drive.

It imports nothing third-party, so the `ev3dev2` exception above does
not extend to it and does not need to.

### `pad_buttons.py` has two launchers, and a flag to tell them apart

It lives in its own folder, `/home/robot/pad_buttons/`, not beside
`tank_drive.py` in `tanks_1/`. It is not part of driving, and it is the
one program here meant to be running all the time rather than started
for a session.

It double-forks and detaches, which is right when Brickman's File
Browser launches it - the menu comes straight back - and wrong under
systemd, which would see the foreground process exit at once and call
the service dead. So it takes **`--foreground`**. The unit passes it, a
File Browser launch does not. One flag, two launchers, no heuristics.

With no flag it is a **toggle**: launching it while a copy is running
stops that copy and exits. With `--foreground` it **takes over**
instead. The distinction is not cosmetic. If systemd's own start toggled
the running copy off and exited 0, `Restart=on-failure` would see a
clean exit, leave the service dead, and the buttons would silently stop
working.

`Restart=on-failure`, not `always`, for the same reason from the other
direction: a deliberate stop has to stay stopped, or the toggle reads as
"it will not turn off".

The pidfile and the log are derived from
`os.path.dirname(os.path.abspath(__file__))`, so they follow the program
wherever it is installed and no path constant needs editing.

### Autostart needs one `sudo`, run by a person, once

`agent/pad-buttons.service` is a systemd **user** unit. Installing,
enabling and editing it are all owned by `robot`, who is already in the
`input` group and needs no more privilege than that.

The exception is lingering. A user service starts at login, and nothing
logs in at boot, so it needs:

```bash
sudo loginctl enable-linger robot
```

once, ever. Measured on this brick 2026-09-02: there is no cron at all,
no `/etc/rc.local`, no `/etc/xdg/autostart`, and `/etc/systemd/system`
is root-only - so there is no root-free route to autostart, and this is
the one that needs the least root.

**Nothing in this project runs that command.** `ev3ctl setup` prints it
for the operator and then checks whether it worked. A test walks the
parse tree of the setup modules asserting no `sudo` string reaches a
call, so the rule cannot erode by accident.

`After=bluetooth.target` in the unit is ordering only. It is not a
promise the pad is connected - the program already waits for the pad and
reconnects, which is what makes the feature work whether the gamepad is
awake at boot or woken an hour later.

## Rule 2: the import boundary is one-way and absolute

**No module under `src/` may import anything from this directory, and
no file here may import anything from `src/`.**

The two sides do not share a Python process, a Python version, or a
machine. They share a protocol: newline-delimited JSON over the stdin
and stdout of one SSH process. That protocol is the only coupling, and
it is the only thing that has to stay in sync.

Files here are **copied** to the brick and run there. They are never
imported. `pyproject.toml` packages `src/ev3ctl` explicitly so that
this directory can never be swept into a wheel by accident.

## Debugging on the brick by hand

Every program here must stay runnable on its own, with no Mac attached:

```bash
ssh robot@ev3dev.local
python3 -u /tmp/ev3_agent.py
```

Then type a JSON command and press Enter. If a program here only works
when driven by `ev3ctl`, it is harder to debug than it needs to be, on
the machine where debugging is already hardest.

## Motors latch

A motor commanded through `run-direct` keeps turning until something
stops it. Losing the SSH link does not stop it. Killing the agent does
not stop it. See CLAUDE.md, "Motors latch", for the rule that follows
from this. Every program in this directory owns that problem, because
this is the side that is still running when the cable is pulled.
