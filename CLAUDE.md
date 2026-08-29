# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working
with code in this repository.

## Project

A LEGO MINDSTORMS EV3 vehicle driven by a Sony DualShock 4 gamepad. The
brick boots ev3dev from a microSD card, so motors and sensors are Linux
devices under `/sys/class/`, and the gamepad is a Linux input device.

Read [ROADMAP.md](ROADMAP.md) before changing anything. It carries the
phase order and, more usefully, the list of claims this project has not
yet checked on hardware.

## Two runtimes

Every later decision depends on this. Get it wrong and either the brick
refuses to run the program, or host-only code lands on a machine with
64 MB of RAM and no package manager.

**HOST code — everything under `src/`.**
CPython **3.12 or later on macOS**. `rich` is available and expected.
`typing`, `dataclasses`, f-strings, `pathlib`, `match` — all fine. None
of it is ever copied to the brick or executed there.

**BRICK code — everything under `agent/`.**
CPython **3.5 on ev3dev (Debian 9)**, **standard library only**. The
brick has no internet access and nothing installed beyond the stock
image, so there is no `pip install` escape hatch. No f-strings, no
`dataclasses`, no walrus operator, no `typing` at runtime. Type hints go
in comments. See [`agent/README.md`](agent/README.md) for the full table
of what 3.5 does not have.

The boundary is one-way and absolute:

- **No module under `src/` may import anything from `agent/`.**
- **No file under `agent/` may import anything from `src/`.**
- Files under `agent/` are **copied** to the brick and run there by the
  brick's own interpreter. They are never imported by the host.
- `pyproject.toml` names `src/ev3ctl` as the only package, so `agent/`
  cannot be swept into a wheel by accident.

The two sides share exactly one thing: a wire protocol. That is the only
coupling, and it is the only thing that has to stay in sync. When you
change one side of it, change the other in the same commit.

## The protocol is the only coupling

The two sides share one thing: newline-delimited JSON over the stdin and
stdout of a single SSH process. Change one side of it and change the
other in the same commit.

```
Mac   -> {"id": 7, "cmd": "motor_run", "address": "outA", "duty": 30}
brick -> {"id": 7, "ok": true,  "result": {...}}
brick -> {"id": 7, "ok": false, "kind": "no_device", "error": "..."}
```

Rules that hold in both directions:

- **One response per command, carrying the id it was given.** Every path
  through the agent's dispatch returns a response, including the ones
  that failed. A command that produces no reply leaves the host waiting
  on a link that, from where it sits, is merely slow.
- **Strictly synchronous, never pipelined.** The host sends one command
  and waits for that id.
- **stdout is the protocol and nothing else.** Everything human-readable
  from the brick goes to stderr, on its own pipe. A shell profile on the
  brick that prints a banner would otherwise corrupt the stream, which is
  why an unparseable line is reported rather than skipped.
- **Neither end trusts the other.** Duty is clamped to -100..100 on the
  host and clamped again on the brick. The agent in `/tmp` may be an
  older copy; the process on the other end may not be this tool at all.
- **The host owns rendering, the brick owns sysfs.** The agent returns
  raw driver integers with their `decimals`, `units` and `count_per_rot`;
  the host does the arithmetic. Scaling on the brick would spend a
  300 MHz CPU on work the Mac is idle for.

`drive` applies both sides of a tank drive in one message, because two
`motor_run` commands per loop iteration would double the round trips and
the round trip is the whole budget a control loop has. If either side
fails, **both are stopped** before the error is raised: a vehicle with
one wheel driving and one refusing is worse than one that has stopped.

Its reply carries only `duty_cycle` and `speed` per side. That is not
minimalism for its own sake — a sysfs attribute read costs about 9 ms on
this brick, and a reply carrying six values per side measured 144 ms
against 96 ms for two. Anything not in a control loop should ask `poll`
for the rest.

`poll` carries two things beyond the values that change: the list of
device nodes, and each sensor's mode. The node list is what lets the host
re-`scan` only when something is plugged or unplugged, instead of paying
for a full inventory at 5 Hz. Both are cheap, and both exist to keep work
off the brick.

## Motors latch

**This is the rule that outranks every other rule in this repository.**

An ev3dev tacho-motor commanded through `run-direct` with a non-zero
`duty_cycle_sp` keeps turning. It does not stop when the program exits.
It does not stop when the SSH session dies. It does not stop when the
process is killed. It does not stop when the USB cable is pulled. The
kernel driver holds the last thing it was told, and the motor obeys it
until something writes `stop`.

So:

**Every code path that commands a motor ends by stopping it.**

Not most paths. Not the happy path. Every path — normal return, early
return, exception, `KeyboardInterrupt`, EOF on a pipe, an exception
raised inside the cleanup of another exception. The shape is always the
same, on both sides of the link:

```python
try:
    ...            # anything that might command a motor
finally:
    stop_all()     # unconditional, must not raise
```

Three rules follow from it, and they are not optional:

1. **`stop_all` must never raise.** It is called from `finally` blocks
   during teardown, often when something has already gone wrong. It
   loops over every motor it can find and swallows individual failures,
   because one motor that cannot be stopped must not prevent the others
   from being stopped.
2. **The brick must be able to stop itself.** The host cannot be trusted
   to still be there. Code on the brick that accepts motor commands runs
   a watchdog: if commands stop arriving and a motor has been commanded
   non-zero, it stops every motor on its own, roughly one second later.
   The host sending a regular heartbeat is not a safety mechanism; the
   watchdog is. The host is only what keeps the watchdog quiet.
3. **Pulling the USB cable is a test, and it must pass.** With a motor
   running, unplugging the cable must stop it within about a second.
   Anything else means the watchdog is broken, and nothing else in the
   project can be trusted until it is fixed.
4. **Stopping the drive is not the same as stopping the robot.**
   `stop_action` on these motors is `coast`. Measured on 2026-08-29: the
   watchdog cuts the drive inside a second, and the motor then freewheels
   for a further 0.66 s. Every claim in this repository that something
   "stops" means the drive was removed. Once this project has wheels
   under it, decide `brake` or `hold` deliberately rather than inheriting
   `coast` by default. See Phase 5 in [ROADMAP.md](ROADMAP.md).

Two failures that look alike and are not:

- **The link dies, the agent lives.** Pulling the USB cable kills ssh.
  The agent keeps running on the brick, reaches EOF on stdin, and runs
  its `finally`. The watchdog is the backstop if it does not. Motors
  stop. This is the case the latch test exercises.
- **The agent dies.** If the process on the brick is killed outright,
  nothing on the brick is left to stop anything, and the motor runs until
  the battery is pulled. There is no software answer to this from the
  Mac. It is the reason the watchdog lives on the brick and not here.

Restoring the operator's terminal comes *before* attempting a final stop
over the link, because terminal restoration is local and instant while
the link may already be dead. The watchdog is what makes that ordering
safe.

## Never invent a hardware fact

If a value has not been read off this brick, it is not known. Write it
into the **Unverified** table in [ROADMAP.md](ROADMAP.md) with what
would resolve it, and handle its absence in code. Specifically:

- **Never hardcode a sysfs device node name.** `motor0` is not port A
  and `sensor0` is not port 1. The mapping is whatever order the kernel
  bound the devices in, and it changes between runs. Enumerate the class
  directory and read each device's `address` attribute to learn its
  port. This is the most common mistake made with these drivers.
- **Never assume the shape of a value either, only that you have to read
  it.** This brick reports its addresses as `ev3-ports:outA`, not
  `outA`. The project assumed the bare form, because that is what the
  documentation uses, and the result was a tool that connected, read
  both motors correctly, and drew four empty rows. Every test written
  before the brick was plugged in passed, because the fake sysfs tree
  was built from the same assumption. Reading the attribute is not
  enough on its own; the format it comes back in is a fact too.
- **Never assume an attribute exists.** Attribute sets differ between
  ev3dev releases and between drivers. Read defensively, catch per
  attribute, and report the value as unknown rather than aborting.
- **Never assume a scale factor.** Motor `count_per_rot` is not
  necessarily 360. Sensor values are scaled by `decimals`. Battery
  readings are in microvolts and microamps. Read the scale, do not
  assume it, and sanity-check the result before showing it.

A number in a document with no source is worse than no number, because
the next person will build on it.

## Bluetooth is for the gamepad only

The brick has one Bluetooth radio, and the gamepad owns it. The
development link is USB, always. Do not add a Bluetooth transport, a
Bluetooth fallback, or a "just for now" Bluetooth shortcut. The reason
is in [README.md](README.md) and it does not expire.

## The runtime is ev3dev, and only ev3dev

ev3dev is a Debian system. Motors and sensors are reached through sysfs
under `/sys/class/`, using ordinary file reads and writes. Do not add a
dependency on any alternative EV3 firmware or its host tooling, and do
not reach for a third-party hardware wrapper on the brick — there is no
package manager there to install one with.

There is a sibling project by the same author on different hardware with
a different firmware and a different host toolchain. Its structure is
worth reading and its code does not transfer. See "Related work" in
[README.md](README.md) for which project that is and why nothing crosses
between them.

## Conventions

- **English only**, in code, comments, docstrings, documents and commit
  messages.
- **Line length 79**, both runtimes. `uv run ruff check .` is the gate.
- **Module docstrings say why the module exists**, not what its name
  already says. Start each one with `HOST CODE.` or `BRICK CODE.` so the
  runtime is never in doubt.
- **One phase, one commit.** A commit that changes two things makes the
  next hardware failure twice as hard to attribute.
- **Do not claim something works until it has been run on the brick.**
  Say what the operator should observe, and what to check if it does not
  happen.

## Working on the brick

- Do not run `apt`, `pip`, or `sudo` on the brick. Nothing gets
  installed. The brick has no internet access, and an image that has
  been modified by hand is an image whose state nobody knows.
- The development link is USB. `ssh robot@ev3dev.local`, password
  `maker`, or a key installed once with `ssh-copy-id`.
- The brick is a 300 MHz ARM9 with 64 MB of RAM. Poll it at 5 Hz, not
  faster, and do not ask it to do work the host could do instead.
