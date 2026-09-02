# LEGO MINDSTORMS EV3 remote-controlled vehicle

A vehicle built from LEGO MINDSTORMS EV3 parts, driven by a Sony
DualShock 4 gamepad. The EV3 brick boots
[ev3dev](https://www.ev3dev.org/) from a microSD card and runs ordinary
Debian Linux, so the motors and sensors are Linux devices and the
gamepad is a Linux input device. Nothing about this project is
proprietary firmware.

## Status

**Phase 3 of 5.** The vehicle does not exist. There is no control loop
and no robot. The gamepad is paired and trusted, and `hid-sony` binds
it, but not one event has been read from it yet.

What does exist is `ev3ctl`, the diagnostic tool: it connects to the
brick over SSH, shows every motor and sensor live at 5 Hz, and drives
motors from the keyboard so you can find out what is plugged into which
port before building anything.

`ev3ctl` runs against the real brick. On 2026-08-29 it read back the
kernel, Python version, release string, battery and both attached
motors, and a Large Motor was commanded to duty 40 over the real link
and turned.

**The latch test passes on hardware.** A motor turning under command
stopped promptly when the USB cable was physically pulled, on
2026-08-29. The two mechanisms behind it were measured separately
first: the watchdog cuts the drive 0.94 s after the host goes silent,
and the agent's `finally` cuts it about 0.12 s after the link is torn
down. `ev3ctl live` has still not been exercised on hardware. See "Verified" and "Verified in simulation only" in
[ROADMAP.md](ROADMAP.md); the difference between those two tables is the
point of them.

**"Stopped" means the drive was removed, not that motion ended.**
`stop_action` is `coast`, so a motor freewheels for about another 0.66 s
after the watchdog cuts it. That is invisible on a bench and matters a
great deal on something with wheels.

Nothing in this repository claims to have been tested on a brick unless
it appears in the Verified table in [ROADMAP.md](ROADMAP.md).

## The two links, and why they are different

This is the single most important design decision in the project, and
it is a hardware constraint, not a preference.

**The EV3 has one Bluetooth radio.** It is Bluetooth 2.1+EDR Classic,
and there is exactly one of it. The whole point of this project is to
put a DualShock 4 on that radio over Bluetooth Classic HID and keep it
there.

So the radio is spoken for:

| Link | Carries | Used for |
| --- | --- | --- |
| **USB** (mini-USB cable, gadget networking, then SSH) | development | editing, copying code, diagnostics, this repository's tooling |
| **Bluetooth Classic** (the brick's only radio) | the gamepad, and nothing else | driving the vehicle |

**Bluetooth is never used as a development link. Not once, not for
convenience, not "just to test something".** If development shared the
radio with the gamepad, then every gamepad problem — a dropped
connection, a stutter, a pairing that will not hold — would be
indistinguishable from a development-link problem, on a radio that
already has to be contended for. The one radio is the thing under test.
Putting a second job on it destroys the test.

USB also has the property that matters when a motor is spinning: it is
a cable. Pulling it is a deliberate, physical, instantaneous act, which
makes it a usable emergency stop and a usable fault-injection tool. See
"Motors latch" in [CLAUDE.md](CLAUDE.md).

## Hardware

The vehicle is not designed yet, so this is what the project needs, not
a parts list for a specific build.

| Item | Count | Notes |
| --- | --- | --- |
| LEGO MINDSTORMS EV3 Brick | 1 | the computer; four input ports 1–4, four output ports A–D |
| microSD card with ev3dev-stretch | 1 | the brick boots from this; the stock LEGO firmware is untouched on internal flash |
| mini-USB cable | 1 | the development link, into the brick's PC port |
| EV3 Large Motor | as available | drive |
| EV3 Medium Motor | as available | steering or auxiliary |
| EV3 sensors | as available | whatever is on hand; the point of `ev3ctl` is to find out |
| Sony DualShock 4 gamepad | 1 | Bluetooth Classic HID |

Motor and sensor counts are deliberately vague. Nothing is plugged in
yet, ports are not assigned, and no port mapping in this repository is
a guess — Phase 2 exists precisely so that the mapping is *read off the
hardware* instead of assumed.

## Booting the brick

1. Insert the microSD card carrying ev3dev into the slot on the side of
   the brick.
2. Press the centre button. The brick boots ev3dev instead of the LEGO
   firmware. Booting takes noticeably longer than stock firmware — give
   it a couple of minutes on first boot.
3. Wait for the Brickman menu. When Brickman is on screen, the brick is
   up.

The microSD is the whole operating system. Removing it and pressing the
centre button boots the original LEGO firmware from internal flash,
untouched. Nothing this project does is permanent.

## Reaching the brick over SSH

Connect the mini-USB cable to the brick's **PC port** — the small port
next to the SD card slot, not the USB-A host port on the other side.
The brick presents itself to macOS as a USB network device.

On the brick, bring the wired connection up from Brickman, then from
the Mac:

```bash
ping6 ev3dev.local              # ping6, not ping - see below
ssh robot@ev3dev.local          # default password: maker
```

**Use `ping6`.** On this setup mDNS answers `ev3dev.local` with both a
working IPv6 address and an IPv4 address that nothing answers on, so
plain `ping ev3dev.local` reports 100% packet loss for a brick that SSH
reaches in a millisecond. `ping` failing here proves nothing at all.

The reason, for anyone tempted to fix it: the brick holds
`192.168.137.3` on its `usb0` with a default route via `192.168.137.1`,
while the Mac's sharing bridge is `192.168.2.1/24`. Those are different
networks, so the brick's IPv4 address is unreachable and the brick has
no route to the internet. **None of that matters here.** SSH works over
IPv6, and nothing in this project needs the brick to reach the internet
— it must never have anything installed on it anyway.

Do this once, so that no later command ever has to stop and ask for a
password:

```bash
ssh-copy-id robot@ev3dev.local
```

The exact interface name macOS assigns, whether `ev3dev.local` resolves
on this machine, and the Brickman menu path for the wired connection
are all recorded in [ROADMAP.md](ROADMAP.md) once they have been read
off the real hardware — and listed as unverified until then.

If SSH does not connect, check in this order: the cable is in the PC
port and not the host port; the brick is booted far enough to show
Brickman; `ping ev3dev.local` answers.

## The ev3ctl command

`ev3ctl` finds out what is plugged into which port. Nothing in this
project assumes a port mapping; this is how the mapping gets established.

```bash
uv run ev3ctl scan     # one inventory of every port, printed once
uv run ev3ctl live     # 5 Hz dashboard with interactive motor control
uv run ev3ctl          # same as live
```

Every subcommand takes `--host` (default `robot@ev3dev.local`),
`--agent` to override the agent source, `--timeout`, and
`--no-multiplex`.

The dashboard's keys:

| Key | What it does |
| --- | --- |
| `a` `b` `c` `d` | select an output port |
| `1` `2` `3` `4` | select an input port |
| left / right | change the selected motor's duty by 10 |
| `space` | set the selected motor's duty to 0 |
| `0` | stop every motor |
| `r` | reset the selected motor, zeroing its position |
| `s` | cycle the selected sensor's mode |
| `q` | quit |

Both output ports A to D and input ports 1 to 4 are always shown, whether
or not anything is plugged in, so that an empty port is visibly empty
rather than simply missing.

### How it works, in one paragraph

Two programs. `src/ev3ctl/` runs on the Mac and does all the drawing.
`agent/ev3_agent.py` is copied to `/tmp/ev3_agent.py` on the brick and
does all the hardware access, under the brick's own Python 3.5 with no
third-party packages, because nothing can be installed there. They talk
newline-delimited JSON over one SSH process and share nothing else.

**A motor commanded through `run-direct` keeps turning until something
stops it** — losing the link does not, and neither does killing the
program. So the agent runs a watchdog: no command for one second with a
motor commanded non-zero, and it stops every motor by itself. The Mac's
200 ms polling is what keeps that watchdog quiet, and the watchdog is
what makes it safe to pull the cable. See "Motors latch" in
[CLAUDE.md](CLAUDE.md).

### Debugging the brick side by hand

The agent is a normal program and stays runnable without the Mac:

```bash
ssh robot@ev3dev.local
python3 -u /tmp/ev3_agent.py
{"id": 1, "cmd": "hello"}
{"id": 2, "cmd": "scan"}
```

It prints its own usage to stderr when it detects a terminal. Note that
a motor commanded this way is stopped again a second later by the
watchdog, because a person thinking about what to type next is
indistinguishable from a link that has died.

## Driving it

`ev3ctl drive` tank-steers two motors from the keyboard. It is the
shortest path to something that moves, and it needs no gamepad and no
finished vehicle.

```bash
uv run ev3ctl drive                       # first two motors found
uv run ev3ctl drive --left outA --right outD
uv run ev3ctl drive --speed 25            # gentler, for a beginner
uv run ev3ctl drive --invert-right        # a motor mounted mirrored
```

| Key | Action |
| --- | --- |
| `w` `s` | forward, backward |
| `a` `d` | turn left, turn right |
| `space` | stop both, immediately |
| `q` | quit |

Opposing keys cancel. `a` or `d` alone counter-rotates the motors and
spins the vehicle in place. `w` and `a` together pivot about the stopped
wheel rather than curving — see Phase 2a in [ROADMAP.md](ROADMAP.md) for
why that falls out of the mixing.

## Mapping the gamepad

`ev3ctl gamepad` is a guided wizard that works out which evdev code each
control on the controller produces, and writes the answer to
`docs/gamepad-mapping.json`.

```bash
uv run ev3ctl gamepad                     # the whole wizard
uv run ev3ctl gamepad --output /tmp/m.json
```

It has eight steps and advances on its own as each is satisfied. Every
step shows what to do, every code it has seen so far with its current
value and range, and how close the advance condition is to being met -
so a step that will not advance says which axis has not moved far
enough, rather than only that it is waiting.

| Key | Action |
| --- | --- |
| `s` | skip this step, leaving its axes unassigned |
| `r` | redo this step, forgetting what it found |
| `q` | abort; the same teardown as finishing |

Press PS to wake the controller when step 0 asks for it. Steps 2 and 3
want a full circular sweep of one stick, then two holds: push that stick
fully right and hold, then fully up and hold. Steps 4 and 5 want a
trigger squeezed all the way in and released, three times. Step 6 names each
button in turn and ends when you press `s`.

**Nothing in the output file is taken from a published controller
layout.** Every assignment comes from an observation made during a named
step. An axis no step ever claimed is written with a null control rather
than a plausible guess, and the file says in as many words that the
suggested deadzone is three times the measured jitter from one sitting
and not a tuned value.

Two things worth knowing before the first run:

- **If the pad is on USB and Bluetooth at once, the wizard refuses to
  start.** The two transports use different HID report layouts, so a
  mapping captured from the wrong one would be wrong without ever
  looking wrong. Unplug the cable and press PS. The pad is identified by
  its `Uniq` field - its own Bluetooth address, which hid-sony sets the
  same on both transports - so the guard still fires when the two
  transports report different Names. The Name is only the fallback, for
  a device that reports no `Uniq` at all, and the report says which of
  the two was used.
- **Which axis is horizontal is measured, not assumed.** The circular
  sweep names the pair; it cannot tell them apart, because it drives
  both through their whole range. So each stick step then asks for two
  directional holds - push right, then up - and names the axis that
  deflects further each time. Push diagonally and the hold is refused
  and retried rather than accepted, because an orientation taken from a
  diagonal is a coin flip the file would present as a measurement.

### Two things that surprise people

**A tapped key runs the motors for about 600 ms.** Terminals send key
*presses* and never key *releases*: there is no event that says you let
go. So a key counts as held until it times out, refreshed by the
operating system's own auto-repeat. `--initial-hold-ms` is that timeout
and `--repeat-hold-ms` is the shorter one used once auto-repeat is
running, which is also the worst-case delay before a release is noticed.
Lowering `--initial-hold-ms` below the OS auto-repeat delay makes a held
key stutter.

**`drive` sets `stop_action` to `brake` on both motors at startup**, and
says so in the header. The driver default is `coast`, which leaves a
motor freewheeling for about two thirds of a second after the drive is
cut. That is invisible on a bench and is the difference between stopping
and rolling away on something with wheels.

## The two transports

The same command over either link. Only `--host` changes, because both
are the same SSH invocation — there is no transport layer in this
project and deliberately is not one.

```bash
uv run ev3ctl drive --host robot@ev3dev.local     # USB, the default
uv run ev3ctl drive --host robot@10.0.0.5         # Bluetooth PAN
```

What differs is latency. Over USB a `drive` round trip measures about
96 ms, of which only 19 ms is the link itself; the rest is the brick
reading and writing sysfs. Bluetooth PAN is slower and more variable, so
the control loop never queues: one command is in flight at a time and
newer state replaces older state rather than joining a queue behind it.
The footer shows the current round trip and a rolling maximum, and that
maximum is the number that says whether PAN is usable for driving.

### Bluetooth PAN does not work from this Mac

Tried on 2026-08-29, macOS 26.5.2. The Mac and the brick pair without
trouble — the brick shows as `ev3dev` and reports Connected — but macOS
provides **no Bluetooth PAN client**: there is no Bluetooth network
hardware port, no "Bluetooth PAN" service, and the controller advertises
no PAN, NAP or BNEP profile. The pairing carries `GATT ACL` and nothing
that moves IP.

No amount of configuration on the brick fixes that; there is nothing on
this side to connect to. Details and the options are under "Bluetooth
PAN on macOS" in [ROADMAP.md](ROADMAP.md).

A DHCP server on the Mac does not help, and it is worth being clear
why: DHCP hands out addresses over a link that already exists, and PAN
is what would have created that link. There is no link for it to run
over.

PPP over a Bluetooth serial link does not work either: the brick has no
`pppd` and cannot be given one. But there is a third route that does not
need IP at all — the agent reads JSON on stdin and writes it on stdout,
so any byte stream will do, and Bluetooth RFCOMM is a byte stream. Made
to offer a Serial Port profile, the brick did carry a command over
Bluetooth with no IP, SSH, PPP or DHCP involved. macOS would then not
reopen the channel, so no round trip was ever completed. The full
account is in [ROADMAP.md](ROADMAP.md); the short version is that the
transport independence is real and building on it would still be a
mistake, because the radio belongs to the gamepad.

If you want a second transport to prove `drive` is transport
independent, a supported USB WiFi dongle in the brick's host port is the
usual ev3dev answer and gives the brick a real address:

```bash
uv run ev3ctl drive --host robot@<the brick's wifi address>
```

Either way, remember the constraint this project is built around: **the
brick has one Bluetooth radio, and the gamepad is going to want it.**
Bluetooth was never the development link here.

## Repository layout

| Path | Runtime | What it is |
| --- | --- | --- |
| `src/ev3ctl/` | CPython 3.12, macOS | Host tooling. The `ev3ctl` command. May use `rich`. |
| `src/ev3ctl/cli/` | CPython 3.12, macOS | One module per subcommand. |
| `src/ev3ctl/mixer.py` | CPython 3.12, macOS | Held keys to two motor duties. Pure functions, no I/O. |
| `src/ev3ctl/link.py` | CPython 3.12, macOS | The SSH transport and the only module that knows the wire. |
| `src/ev3ctl/gamepad.py` | CPython 3.12, macOS | The gamepad wizard's arithmetic: advance gates, rest statistics, the mapping document. Pure functions, no I/O. |
| `src/ev3ctl/evdev_codes.py` | CPython 3.12, macOS | evdev type and code names, transcribed from the v4.14 kernel header. Names only, never a controller layout. |
| `agent/` | CPython 3.5, ev3dev | Code that runs **on the brick**. Standard library only. Copied there, never imported by `src/`. |
| `agent/ev3_agent.py` | CPython 3.5, ev3dev | All hardware access, and the watchdog. One file, no dependencies. |
| `agent/battery_report.py` | CPython 3.5, ev3dev | Standalone battery diagnostic. Copied by hand, not part of the protocol. |
| `agent/tank_drive.py` | CPython 3.5, ev3dev | Standalone tank drive from the gamepad's left stick. Copied by hand to `/home/robot/tanks_1/`, launched from Brickman. |
| `agent/pad_buttons.py` | CPython 3.5, ev3dev | Standalone. Maps the gamepad's D-pad, Cross and Share onto the brick's own buttons by injecting input events. Detaches on start; start again to stop. |
| `tests/` | CPython 3.12, macOS | Tests for the pure host logic, and for the brick's parser read as text. |
| `docs/gamepad-mapping.json` | — | Written by `ev3ctl gamepad`. Absent until the wizard has been run. |
| `pyproject.toml` | — | `uv` project definition. Host dependencies only. |
| `README.md` | — | This file. |
| `CLAUDE.md` | — | Working rules for this repository. |
| `ROADMAP.md` | — | Phases, acceptance tests, and the Verified / Unverified tables. |
| `LICENSE` | — | MIT. |

The split between `src/` and `agent/` is the structural fact of this
repository. The two directories do not share a Python version, a
machine, or an import. [CLAUDE.md](CLAUDE.md) states the rule;
[`agent/README.md`](agent/README.md) states what it costs.

## Related work

[`lumduan/lego-spike-prime-remote-car`](https://github.com/lumduan/lego-spike-prime-remote-car)
is a separate project by the same author: a LEGO SPIKE Prime car on
Pybricks firmware, driven by an Xbox controller over Bluetooth Low
Energy. Different hardware, different runtime, different constraints.

**This project does not use Pybricks.** No Pybricks package is a
dependency here, and `pybricks-micropython` is not the runtime. ev3dev
is a Debian system and this project talks to the Linux kernel's LEGO
drivers through sysfs.

## Licence

MIT. See [LICENSE](LICENSE).

## Trademarks

LEGO® and MINDSTORMS® are trademarks of the LEGO Group.

PlayStation® and DualShock® are trademarks of Sony Interactive
Entertainment Inc.

Neither the LEGO Group nor Sony Interactive Entertainment sponsors,
authorises, endorses, or is affiliated with this project in any way.
This is an independent, unofficial project.
