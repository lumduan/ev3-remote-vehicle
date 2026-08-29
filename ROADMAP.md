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
cannot say what it does not know will eventually build on a guess. It
did, twice, on 2026-08-29; both are recorded below.

### Verified

Read off the hardware on **2026-08-29**, over the USB link, by SSH.

| Fact | Where it came from |
| --- | --- |
| Kernel is `4.14.117-ev3dev-2.3.5-ev3`, `#1 PREEMPT Sat Mar 7 12:54:39 CST 2020`, `armv5tejl` | `uname -a` on the brick |
| Python 3 on the brick is **3.5.3**, at `/usr/bin/python3` | `python3 -VV` on the brick. The 3.5-only rule for `agent/` was the correct assumption |
| `/etc/ev3dev-release` reads `ev3-micropython-v2.0.0-sd-card-image` | `cat` on the brick. This card is the EV3 MicroPython image, not the plain ev3dev-stretch image. It is still Debian 9.12 with the ev3dev kernel and the ev3dev sysfs drivers, which is all this project uses |
| `/etc/debian_version` is `9.12` | `cat` on the brick |
| CPU is `ARM926EJ-S rev 5 (v5l)`, hardware `LEGO MINDSTORMS EV3` | `/proc/cpuinfo` |
| Usable RAM is **56 MB**, with 95 MB of swap | `free -m` |
| **macOS on Apple Silicon does enumerate the gadget**, as interface `en7` | `ifconfig` diff before and after plugging in. It is bridged into `bridge100`, which is macOS Internet Sharing |
| `ev3dev.local` resolves to **two** addresses: an IPv6 one that works, and `192.168.137.3` which does not | `dscacheutil -q host -a name ev3dev.local`. SSH connects over IPv6 |
| **Plain `ping ev3dev.local` fails while the link is perfectly healthy** | 100% packet loss to the IPv4 address, while `ping6 ev3dev.local` answers in 1.3 ms and ssh works. Use `ping6`, never `ping`, to test this link |
| The brick's own interface is `usb0`, holding `192.168.137.3/24` with a default route via `192.168.137.1` | `ip route` on the brick |
| **The brick's IPv4 config is for a network that is not there.** The Mac's sharing bridge is `192.168.2.1/24`, so nothing answers `192.168.137.1` | Comparing `ip route` on the brick with `ifconfig bridge100` on the Mac. This is why the advertised IPv4 address is unreachable and why IPv6 is the only working path |
| The brick has **no internet access** | `ping 8.8.8.8` from the brick: 100% loss, 2 errors. Consistent with the routing mismatch above |
| Key authentication works on the stock image | `ssh -o BatchMode=yes robot@ev3dev.local true` connects with no prompt after `ssh-copy-id` |
| `/sys/class/lego-port/` **exists and lists all eight ports** with nothing attached, as `port0` to `port7` | `ls` on the brick. Empty input ports report status `no-sensor`, empty output ports `no-motor` |
| **Port addresses are `ev3-ports:outA`, not `outA`** | `cat /sys/class/tacho-motor/*/address`. See "the address prefix" below; this cost a wrong assumption |
| The battery node is `lego-ev3-battery`, and it has both `voltage_now` and `current_now` | `ls /sys/class/power_supply/*/` |
| **`voltage_now` is in microvolts**: 8053800 reads as 8.05 V, inside the plausible 6.0-8.5 V band | `cat`, cross-checked against the sanity range the tool applies |
| `current_now` 156000 reads as 156 mA | same |
| Two **Large Motors** (`lego-ev3-l-motor`) are attached, on **outA and outD** | `ev3ctl scan` and `cat /sys/class/tacho-motor/*/address` |
| `count_per_rot` is **360** on the EV3 Large Motor on this driver | `cat /sys/class/tacho-motor/*/count_per_rot`. It is read, not assumed; the code still refuses to default to 360 |
| `run-direct` **is** in every motor's `commands` list | `commands` reads `run-forever run-to-abs-pos run-to-rel-pos run-timed run-direct stop reset` |
| No sensors are attached: `/sys/class/lego-sensor/` is empty | `ls` on the brick |
| `ev3ctl scan` runs against the real brick and prints the real kernel, Python, release and battery | Run on 2026-08-29 |

**The address prefix.** This project assumed a motor's `address` would
read `outA`, because that is the form the specification and most
documentation use. The driver reports `ev3-ports:outA`. The result was a
tool that connected perfectly, read both motors, and drew four empty
rows, because nothing matched the port grid. Both sides now compare on
the bare name after the last colon and accept either form. It is exactly
the failure the "never invent a hardware fact" rule exists to prevent,
and it survived every test written before the brick was plugged in,
because the fake sysfs tree had been built from the same assumption.

### Verified in simulation only

Exercised on the development machine on 2026-08-29, against a fake sysfs
tree and the real agent running as a local process. These say the code
does what it intends. **They say nothing about a real motor turning**,
and none of them substitutes for the Phase 2 acceptance test. They are
listed separately from Verified for exactly that reason.

The fake tree was corrected to use the real `ev3-ports:` address form
once the hardware disproved the old one, and deliberately leaves one
device on the bare form so both are covered.

| Behaviour | How it was exercised |
| --- | --- |
| The watchdog stops a latched motor 0.99 s after commands stop | Fake sysfs tree, motor commanded to duty 40, then no further commands. `command` became `stop` |
| The watchdog does **not** fire while the host is polling | `ev3ctl live` driven through a pty, motor held at duty 30 for 2.5 s. `command` stayed `run-direct` |
| EOF on the agent's stdin stops every motor | Agent's stdin closed with a motor at duty 40. `command` became `stop`, agent exited 0 |
| `q` stops every motor and restores the terminal | pty session: `command` became `stop`, exit 0, terminal flags identical before and after |
| Ctrl-C does the same, and exits 130 | pty session, `\x03` sent while a motor was at duty 40 |
| A dead link is reported, not raised as a traceback | Agent process killed mid-session. Exit 2, diagnosis printed, terminal restored |
| Port addresses are read, never inferred from node names | Fake tree where `motor0` is `outC` and `motor1` is `outA`. Both landed in the right rows |
| Both address forms are accepted | Fake tree mixes `ev3-ports:in1` and a bare `in4` |
| Degrees are computed from the device's own `count_per_rot` | Fake motor with `count_per_rot` 720: 360 counts rendered as 180.0 deg, not 360 |
| Sensor values are scaled by `decimals` | Fake ultrasonic sensor, raw 2537 with `decimals` 1, rendered 253.7 cm |
| An implausible battery voltage is surfaced as a warning | 8.12 V accepted; the same reading a thousand times larger flagged as bad scaling |
| Unplugging a device empties its row within one refresh | Motor node deleted from the fake tree mid-session; the node list changed, a rescan followed, row A read `empty` |
| Missing sysfs is survivable | The whole agent run on macOS, where none of `/sys/class/*` exists. Every field returned null, nothing raised |

### Unverified, and how each one gets resolved

| Claim | Why it is not verified | Resolved by |
| --- | --- | --- |
| Whether `hid-sony` binds the DualShock 4 on this kernel | No gamepad has been paired. `hid-sony` is not confirmed present in this kernel build either | **Phase 3.** Pair the controller, then look for the device under `/dev/input/` and check the driver bound to it |
| That a motor actually turns when commanded, and that `Cmd` and `Duty` track | `ev3ctl scan` has run against the brick; `ev3ctl live` has not, and no motor has been commanded over the real link. The port grid bug was found and fixed but the fix has not been seen working on hardware | **Phase 2**, acceptance items 3 to 6 |
| **That a motor stops within a second when the USB cable is pulled** | The watchdog and the EOF path both work in simulation. Neither has been tested against a motor that is actually turning. **This is the one that matters** | **Phase 2**, acceptance item 7 |
| Sensor behaviour: `decimals`, `units`, `modes`, and whether mode cycling works | No sensor is attached to this brick | **Phase 2**, acceptance item 6 |
| Why the brick dropped off the USB bus mid-session on 2026-08-29 | The `en7` interface vanished and the interface list returned byte-identical to the pre-plug baseline, while work was in progress. Cause unknown: cable, power, or an idle shutdown | **Phase 0**, next time the brick is connected. If it recurs, it matters a great deal, because a link that dies on its own is the latch test happening at random |
| Why the brick is configured for `192.168.137.0/24` when the Mac shares `192.168.2.0/24` | Observed, not explained. `192.168.137.0/24` is the range Windows Internet Connection Sharing uses by default, so a stale profile from another host is the likely story, but that has not been checked in the brick's connman config | Nobody, for now. **It costs this project nothing**: SSH works over IPv6, and nothing here needs the brick to have internet or a working IPv4 address. Worth knowing before anyone spends an afternoon on the IPv4 address |

---

# Phases

## Phase 0: USB development link verified

**Status: complete, with evidence, on 2026-08-29.** macOS on Apple
Silicon enumerates the gadget as `en7` and bridges it into `bridge100`.
`ev3dev.local` resolves over IPv6, key authentication works, and the
brick's kernel, Python version and release string are in the Verified
table above.

One thing about this link is still open, and it is not small: **the
brick dropped off the USB bus by itself, mid-session, with no cable
touched.** `en7` vanished and the interface list went back to exactly
what it was before the brick was plugged in. Cause unknown. It is in the
Unverified table, and it matters because a link that dies on its own is
the latch test firing at a moment nobody chose.

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
2. `ping6 -c 3 ev3dev.local` answers. **Use `ping6`, not `ping`.**
   Verified on 2026-08-29: mDNS hands back both a working IPv6 address
   and an unreachable IPv4 one, so plain `ping` reports 100% loss for a
   brick that SSH reaches without trouble. `ping` failing proves
   nothing here.
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

**Status: implemented. Acceptance items 1 and 2 done on hardware;
3 to 9 outstanding.** `ev3ctl scan` has run against the real brick and
printed its real kernel, Python version, release and battery; those
readings are in the Verified table above.

**No motor has been commanded over the real link, and the latch test has
not been run.** Everything from item 3 down is still simulation only.
Running against the brick found three bugs that no amount of simulation
would have: an SSH control socket path too long for `sockaddr_un`, a
troubleshooting checklist that told the operator to use `ping` when
`ping` cannot work here, and a port grid built on the wrong address
format. All three are fixed; none of the fixes has been watched moving a
motor.

**Goal.** Find out what is actually plugged into which port, and prove a
motor can be commanded and stopped, before any robot is built. No port
mapping in this project is a guess, and this phase is why.

**Work.** Two components and one protocol.

`agent/ev3_agent.py` runs on the brick under Python 3.5 with the
standard library only. It owns every sysfs read and every sysfs write,
and it owns the watchdog. `src/ev3ctl/` runs on the Mac and owns all
rendering. They talk newline-delimited JSON over the stdin and stdout of
one SSH process, and share nothing else. `ev3ctl scan` prints one
inventory and exits; `ev3ctl live` is the 5 Hz dashboard with
interactive motor control.

**Acceptance test.** On the physical brick, connected by USB only. Each
item says what to observe and what to check when it does not happen.

1. **Empty inventory.** With nothing plugged into any port, run
   `uv run ev3ctl scan`.
   **Observe:** four output rows A to D and four input rows 1 to 4, all
   reading `empty`, and a battery voltage between 6.0 and 8.5 V.
   **If not:** a battery line reading "outside the plausible 6.0-8.5 V
   range" means `voltage_now` is not in microvolts on this driver, and
   the scaling in `model.py` needs the real unit. A `lego-port` table
   reading "absent or empty" means `/sys/class/lego-port` does not exist
   on this release; the A-D and 1-4 rows come from a fixed grid and are
   unaffected, but record it in Unverified above. If the whole command
   fails, it prints the failing ssh command and a checklist; work down
   the checklist rather than reading the Python.

2. **Header facts.** Read the header of that same output.
   **Observe:** the brick's real kernel version and Python version.
   **Record both verbatim in the Verified table above.** This is the
   step that resolves two of the four seeded Unverified rows.
   **If not:** a blank kernel or python field means `hello` returned
   null for it, which would mean `os.uname()` or `sys.version` failed on
   the brick - unlikely, and worth investigating before trusting
   anything else the tool says.

3. **A motor appears.** Plug a Large Motor into port A and run
   `uv run ev3ctl live`.
   **Observe:** within one refresh (200 ms), row A shows driver
   `lego-ev3-l-motor`. Turn the shaft by hand: Counts and Deg change,
   and Speed becomes non-zero.
   **If not:** if the row stays empty, the device node exists but its
   `address` attribute did not read as `outA`; run
   `ssh robot@ev3dev.local 'cat /sys/class/tacho-motor/*/address'` to see
   what it actually says. If Deg stays at `-` while Counts moves,
   `count_per_rot` was unreadable; the tool refuses to assume 360, so
   record the real value in Unverified.

4. **A motor disappears.** Unplug that motor while `live` is running.
   **Observe:** row A returns to `empty` within one refresh, and the
   tool does not crash.
   **If not:** the rescan is driven by the node list carried in every
   `poll`. If the row is stale, the node set is not changing when a
   device is removed on this release, and the tool would have to compare
   addresses instead.

5. **A motor turns.** Press `a`, then the right arrow three times.
   **Observe:** the motor spins. `Cmd` reads 30 and `Duty` tracks it.
   Press `space`: the motor stops and `Cmd` reads 0.
   **If not:** `Cmd` is `duty_cycle_sp` read back off the brick, not what
   the tool intended, so `Cmd` staying at 0 means the write was refused.
   The footer shows the refusal. Check that `run-direct` is in the
   motor's `commands` list, which `ev3ctl scan` prints.

6. **A sensor reads.** Plug a Color Sensor into input port 1.
   **Observe:** row 1 shows its driver name and a value scaled by
   `decimals` with its unit, not a raw integer. Press `s`: the mode
   changes and the Values column changes shape.
   **If not:** a value shown with no unit means `units` was unreadable.
   A value that looks a factor of ten wrong means `decimals` was
   unreadable, in which case the tool prints the raw integer rather than
   guessing the scale.

7. **Latch test.** Set a motor to duty 40, then **pull the USB cable out
   while it is running.**
   **Observe:** the motor stops within about one second.
   **If not: stop. The watchdog is broken and nothing else in this tool
   may be trusted.** Two independent mechanisms should stop it, and both
   live on the brick because the Mac is gone: the agent reaching EOF on
   stdin and running `stop_all` in its `finally`, and the watchdog thread
   firing after 1000 ms with no command. Reproduce it by hand with
   `ssh robot@ev3dev.local` then `python3 -u /tmp/ev3_agent.py`, type
   `{"id":1,"cmd":"motor_run","address":"outA","duty":40}`, and wait: the
   watchdog should stop it a second later and say so on stderr.

8. **Clean quit.** Press `q`.
   **Observe:** the terminal returns to normal, typing echoes, and every
   motor is stopped.
   **If not:** if the shell is left without echo, `stty sane` recovers
   it. The terminal is restored before anything is sent to the brick,
   precisely so that a dead link cannot cost you your shell.

9. **Interrupt.** Press Ctrl-C during `live`.
   **Observe:** the same outcome as item 8, and exit status 130.
   **If not:** Ctrl-C works because the terminal is put in cbreak rather
   than raw mode, leaving `ISIG` on. If Ctrl-C does nothing, that is the
   thing to check.

**Pass:** 7, 8 and 9. **Item 7 is the most important test in this
project.** Items 1 to 6 are what makes the tool useful; item 7 is what
makes it safe to use. A tool that cannot be trusted to stop a motor is
worse than no tool, because it will be trusted.

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
