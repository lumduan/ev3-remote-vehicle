# ROADMAP

A LEGO MINDSTORMS EV3 vehicle driven by a Sony DualShock 4 gamepad over
Bluetooth Classic HID, with the brick running ev3dev from a microSD
card.

Every phase below is one commit. Every phase ends with an acceptance
test carried out on the physical brick, by hand. A phase is not finished
because the code is written; it is finished when the test has been run
and what happened has been written down.

Development happens over USB. The brick's single Bluetooth radio is
reserved for the gamepad and is never used as a development link. The
reason is in [README.md](README.md).

## What is verified and what is not

Nothing in this document is written from memory. A fact belongs in the
Verified table only if it was read off this machine or this brick, with
the date it was read. Everything else is Unverified, and each Unverified
row names the phase that resolves it.

This section is the most useful part of the document. A project that
cannot say what it does not know will eventually build on a guess.

### Verified

| Fact | Where it came from |
| --- | --- |
| Development machine is macOS 26.5.2, build 25F84, on `arm64` | `sw_vers` and `uname -m`, 2026-08-29 |
| Host Python is 3.14.2 | `python3 --version`, 2026-08-29 |
| Host `uv` is 0.11.7 | `uv --version`, 2026-08-29 |
| Host git is 2.50.1 (Apple Git-155) | `git --version`, 2026-08-29 |
| Host ssh is OpenSSH_10.2p1 | `ssh -V`, 2026-08-29 |
| The brick boots ev3dev from microSD and reaches the Brickman menu | Reported by the operator, 2026-08-29 |

Note what is **not** in that table: anything at all about the brick's
software. Every such fact is below.

### Unverified, and how each one gets resolved

| Claim | Why it is not verified | Resolved by |
| --- | --- | --- |
| The exact ev3dev kernel version on this brick | Never read. `uname -r` has not been run on this brick and its output has not been recorded anywhere in this repository | **Phase 2.** `ev3ctl scan` prints it in the header. Record the exact string in the Verified table above |
| The Python 3 version on this brick | Never read. ev3dev-stretch is Debian 9, whose system Python 3 is *expected* to be 3.5, but expected is not read. Everything under `agent/` is written to 3.5 because that is the safe assumption, not because it has been confirmed | **Phase 2.** `ev3ctl scan` prints `sys.version` in the header. Record it |
| Whether macOS on Apple Silicon enumerates the ev3dev USB gadget network interface at all, and under what name | Never tried on this machine. macOS supports CDC-ECM natively and does not support RNDIS; which of those the brick presents, and whether macOS binds it without help, is unknown here. This is the single point of failure for the entire development link | **Phase 0 acceptance test**, re-run and recorded. If it fails, the project is blocked at Phase 0 and the contingency is a supported USB WiFi dongle in the brick's host port — a hardware purchase, a separate decision, and still not Bluetooth |
| Whether `hid-sony` binds the DualShock 4 on this kernel | No gamepad has been paired. The DualShock 4 is a Bluetooth Classic HID device and ev3dev ships `hid-sony`, but whether the module is present in this kernel build, and whether it binds this controller revision, has not been checked | **Phase 3.** Pair the controller, then look for the device under `/dev/input/` and check the driver bound to it |
| Whether `/etc/ev3dev-release` exists on this image | Not read. The agent protocol returns null for it rather than failing, precisely because its presence is a guess | **Phase 2.** `hello` returns its contents or null |
| Whether `/sys/class/lego-port/` exists on this release and lists ports with **nothing** attached | Not read. The diagnostic CLI is specified on the assumption that an empty port is still visible as a port. If it is not, empty rows have to come from a fixed A–D and 1–4 grid instead | **Phase 2**, on first run against the real brick with nothing plugged in |
| The `power_supply` node name for the battery, and whether `current_now` exists on it | Not read. Code globs `/sys/class/power_supply/*` and takes the first entry rather than hardcoding a name, because the name is unknown | **Phase 2.** `ev3ctl scan` shows the node name and the battery reading |
| That battery `voltage_now` is in microvolts | Not read. It is the Linux power-supply class convention, but this is a driver on an unusual platform. A reading far outside 6.0–8.5 V means the scaling assumption is wrong, so the tool surfaces that as a warning rather than printing a nonsense number | **Phase 2.** A plausible voltage confirms it; an implausible one disproves it, and either outcome is information |
| `count_per_rot` for the EV3 Large Motor and the EV3 Medium Motor on this driver | Not read. 360 is a plausible value and is exactly the kind of plausible value that is wrong. Degrees are computed as `position / count_per_rot * 360` with the value read from the device, never assumed | **Phase 2**, with a motor attached |
| Whether `run-direct` appears in every motor's `commands` list on this driver | Not read. The whole interactive motor control depends on it | **Phase 2**, with a motor attached. `scan` returns each motor's `commands` list |
| Whether `ev3dev.local` resolves from this Mac, or whether an IP address is needed | Not tried. Depends on Avahi running on the brick and on mDNS working across the USB interface | **Phase 0 acceptance test** |
| Whether the stock ev3dev image accepts key authentication after `ssh-copy-id` | Not tried. The default account is `robot` with password `maker`; whether `~/.ssh` on the image is set up to accept an installed key is unconfirmed | **Phase 0 acceptance test** |
| The Brickman menu path that brings the wired USB connection up | Not read off the brick. Expected to be `Wireless and Networks` → `All Network Connections` → the wired entry → `Connect`, but the wording differs between ev3dev releases | **Phase 0 acceptance test.** Record what the brick actually says |
| Which EV3 motors and sensors are actually on hand, and what is plugged where | Nothing is plugged in and no robot is built. No port mapping is written down anywhere in this repository, deliberately | **Phase 2.** That is the entire purpose of the diagnostic CLI |

---

# Phases

## Phase 0: USB development link verified

**Status: complete, with its evidence gap named.** The brick boots
ev3dev and the operator reaches the Brickman menu. What this repository
has *not* yet recorded is the interface name macOS gives the gadget,
the brick's kernel version, its Python version, and whether
`ev3dev.local` resolves. Those four sit in the Unverified table above
and are resolved by re-running the acceptance test below and writing
down what it prints.

**Goal.** A development link that is a cable, not a radio. Everything
later in this project assumes the host can reach the brick without
touching Bluetooth.

**Work.** Write ev3dev to a microSD card, boot the brick from it,
connect the mini-USB cable to the brick's PC port, bring the wired
connection up in Brickman, and install an SSH key.

**Acceptance test.** On the physical brick, USB cable only, Bluetooth
switched off on the brick if it is switchable:

1. `ifconfig -a` on the Mac lists a network interface that was not there
   before the cable was plugged in. **Record its name.**
2. `ping -c 3 ev3dev.local` answers. If it does not, find the brick's
   address from Brickman and note that mDNS is not usable.
3. `ssh robot@ev3dev.local` connects with password `maker`.
4. `ssh-copy-id robot@ev3dev.local` succeeds, and a following
   `ssh -o BatchMode=yes robot@ev3dev.local true` connects with no
   prompt.
5. `uname -r` and `python3 -VV` on the brick print. **Record both
   verbatim in the Verified table.**
6. Unplug the cable. The SSH session dies. Plug it back in and connect
   again without rebooting the brick.

**Pass:** steps 3, 4 and 5. Step 6 matters because every later phase
assumes the link can be broken and remade without a reboot — and,
worse, that it can break *while a motor is running*.

**Fail:** if step 1 finds no interface, the link does not exist on this
machine and no amount of software fixes it. Record what was tried and
what was seen, then treat Phase 0 as open. Nothing downstream can be
tested until it closes.

## Phase 1: Repository scaffold

**Status: complete.** This commit.

**Goal.** Everything on disk except application logic, so that later
phases change one thing at a time and a hardware failure is always
attributable to the one thing that changed.

**Work.** `pyproject.toml` as a `uv` project with `rich` as the only
runtime dependency and an `ev3ctl` console script. `src/ev3ctl/` holding
an `__init__.py` and nothing else. `agent/` with a README stating the
Python 3.5 rule and the import boundary. `README.md`, `CLAUDE.md`, this
file, `LICENSE`, `.gitignore`. No application logic: nothing in this
commit reads sysfs, opens a socket, or drives a motor. No `tests/`
directory yet, because there is nothing to test.

**Acceptance test.** On the development machine:

1. `uv run ev3ctl` runs and exits without a traceback. It does nothing,
   and says so.
2. `git log` shows exactly one commit.
3. `grep -ri pybricks .` finds Pybricks mentioned only as *not used*.
4. `README.md` explains why Bluetooth is not the development link, in
   terms of the brick's single shared radio.
5. The Unverified table above is not empty.

**Pass:** all five. Item 5 is the one that matters: a scaffold that
claims to know things it has not checked is worse than no scaffold.

## Phase 2: Port and device diagnostic CLI

**Goal.** Find out what is actually plugged into which port, and prove a
motor can be commanded and stopped — before any robot is built. No port
mapping in this project is a guess, and this phase is why.

The tool must show an empty port as empty rather than omitting it, must
survive a device being unplugged while it is running, and must show
values in real units rather than raw driver integers.

**Acceptance test.** On the physical brick, connected by USB only:

1. With nothing plugged into any port, the inventory shows four output
   rows and four input rows, all empty, plus a battery voltage between
   6.0 and 8.5 V.
2. The header shows the brick's real kernel version and Python version.
   **Record both in the Verified table above.**
3. A Large Motor plugged into port A appears within one refresh, with
   its driver name. Turning the shaft by hand changes position and
   degrees, and speed becomes non-zero.
4. Unplugging that motor while the tool is running returns row A to
   empty within one refresh, and the tool does not crash.
5. Commanding the selected motor to duty 30 spins it; commanded duty
   reads 30 and actual duty tracks it; commanding 0 stops it.
6. A Color Sensor in input port 1 shows its driver name and a scaled
   value with a unit. Cycling its mode changes the value shape.
7. **Latch test.** With a motor at duty 40, unplug the USB cable. The
   motor must stop within about one second.
8. Quitting returns the terminal to normal, echo works, and every motor
   is stopped.
9. Ctrl-C does the same.

**Pass:** 7, 8 and 9. **Item 7 is the most important test in this
project.** If a motor does not stop when the cable is pulled, the
watchdog is broken, and nothing else in this repository may be trusted
until it is fixed. Items 1 to 6 are what makes the tool useful; item 7
is what makes it safe to use.

## Phase 3: Gamepad pairing and evdev event-code mapping

**Goal.** The DualShock 4 pairs with the brick over Bluetooth Classic
HID, stays paired across a power cycle, and every control on it is
mapped to a known evdev event code — read off the device, not taken from
a table on the internet.

This is the phase that spends the brick's one radio. After it, the radio
is committed.

**Acceptance test.** On the physical brick, with the gamepad:

1. The controller pairs from the brick and appears as an input device.
2. The driver bound to it is recorded. **Add it to the Verified table.**
3. Every stick, trigger, D-pad direction and button produces an event,
   and the event code for each is written down.
4. Stick centre values and resting drift are measured, not assumed. A
   deadzone that is guessed is a deadzone that is wrong.
5. Power the brick off and on. The controller reconnects without being
   re-paired.
6. Switch the controller off mid-session. The brick notices, and does
   not hang waiting for it.
7. The USB development link still works while the controller is
   connected. If it does not, the two-link design is wrong and this
   phase has found it.

**Pass:** 3, 5 and 7. Item 4 is what makes Phase 4 possible; item 6 is
the first half of Phase 5.

## Phase 4: Non-blocking control loop

**Goal.** The vehicle drives from the gamepad. One loop on the brick,
reading input and writing motors, that never blocks waiting for
anything — not for an input event, not for a motor to finish, not for
the host, which is not there.

Nothing in this phase may require the development machine at run time.
The brick is the computer. Once the program is on it, the vehicle is a
self-contained toy.

**Acceptance test.** On the physical robot, on the floor:

1. Sticks or triggers drive the vehicle, and it responds without
   perceptible lag.
2. The loop rate is measured and written down, not estimated.
3. Releasing every control brings the vehicle to a stop.
4. The loop does not stall when the controller is idle, when a sensor is
   unplugged, or when a motor stalls against an obstacle.
5. Ending the program stops every motor.
6. **Latch test.** Kill the program mid-drive. The vehicle stops, it
   does not run away.

**Pass:** 4, 5 and 6.

## Phase 5: Durability and safety

**Goal.** The vehicle behaves correctly when things go wrong, because
they will. A child will switch the controller off mid-drive, drive it
into a wall and hold the throttle there, and run the battery flat.

**Acceptance test.** Each case is provoked deliberately on the physical
robot:

1. Controller switched off mid-drive: the vehicle stops, promptly, on
   its own.
2. Controller switched back on: it reconnects, or fails in a way the
   operator can see. It does not hang silently.
3. Motor stalled against an obstacle at full throttle: the motor is not
   left burning current indefinitely.
4. Battery low: the condition is detectable and visible before the brick
   browns out.
5. Program crash from an unexpected exception: every motor stops.
6. Battery physically removed mid-drive: on the next power-up, nothing
   resumes moving by itself.

**Pass:** 1, 3 and 5. Case 1 is the one that happens weekly. Case 5 is
the one the whole `try` / `finally` discipline exists for.

---

# Future work

Deliberately not scheduled, and listed only so that it is not confused
with the plan.

- Telemetry off the robot while driving. The radio is committed to the
  gamepad, so this needs a second channel that does not exist yet.
- Autonomous behaviour using the sensors, once Phase 2 has established
  what sensors there are.
- A second controller, or controller hot-swap.
