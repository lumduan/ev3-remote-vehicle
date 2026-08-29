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
| `ev3dev`, `ev3dev2`, `rich` | third-party; talk to sysfs directly |
| f-strings | 3.5 does not have them; use `.format()` |
| the `=` specifier in f-strings | 3.8 |
| `dataclasses` | 3.7 |
| the walrus operator `:=` | 3.8 |
| `typing` at runtime | keep hints in comments |
| `subprocess.run(capture_output=...)` | 3.7 |
| `pathlib` conveniences added after 3.5 | read sysfs with `open()` |

Type hints go in comments, not annotations:

```python
def read_attr(path, name):
    # type: (str, str) -> str or None
    ...
```

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
