# LEGO MINDSTORMS EV3 remote-controlled vehicle

A vehicle built from LEGO MINDSTORMS EV3 parts, driven by a Sony
DualShock 4 gamepad. The EV3 brick boots
[ev3dev](https://www.ev3dev.org/) from a microSD card and runs ordinary
Debian Linux, so the motors and sensors are Linux devices and the
gamepad is a Linux input device. Nothing about this project is
proprietary firmware.

## Status

**Phase 1 of 5.** The repository exists; the vehicle does not. There is
no control loop, no gamepad pairing, and no robot. See
[ROADMAP.md](ROADMAP.md) for the phase order, what each phase has to
prove on the physical brick, and — just as important — the list of
things this project has **not** yet checked on hardware.

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
ping ev3dev.local
ssh robot@ev3dev.local          # default password: maker
```

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

## Repository layout

| Path | Runtime | What it is |
| --- | --- | --- |
| `src/ev3ctl/` | CPython 3.12, macOS | Host tooling. The `ev3ctl` command. May use `rich`. |
| `agent/` | CPython 3.5, ev3dev | Code that runs **on the brick**. Standard library only. Copied there, never imported by `src/`. |
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
