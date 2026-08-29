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
| **`command` is write-only** (`--w--w----`). It can be written and never read back | `ls -l /sys/class/tacho-motor/*/command`. The agent only ever writes it, so the code is unaffected, but a test cannot assert on it - observe `state`, `speed` and `duty_cycle` instead |
| **The watchdog stops a real motor.** Commanded to duty 40 over the real link, then the host went silent: drive was cut **0.94 s** after the last command, against a 1.0 s timeout | Sampled at ~16 Hz by a Python observer on the brick, on a second SSH connection so that watching could not itself count as a command |
| **The EOF path stops a real motor.** Same setup, link then torn down: drive was cut about **0.12 s** after the teardown | The agent's `finally` running `stop_all`, faster than the watchdog because EOF arrives at once |
| A Large Motor at duty 40 turns at roughly **319 deg/s** | Same traces |
| `stop_action` is **`coast`**, and `stop_actions` offers `coast brake hold` | `cat` on the brick |
| **"Stopped" means drive removed, not motion ended.** With `stop_action` at its default `coast`, after the drive was cut the motor freewheeled for a further **0.66 s** before reaching zero speed | The same traces. See the note under Phase 5 |
| **`brake` largely removes that freewheel.** With `stop_action` set to `brake`, speed fell from 229 to 32 deg/s in **0.11 s** after the cut, against roughly 0.48 s for the same fall on `coast` | Measured 2026-08-29 by the same method, after `ev3ctl drive` set the attribute |
| `stop_action` persists on the brick until reboot or a motor `reset` | A second `drive` run reports it as already `brake` rather than changing it |
| **A sysfs attribute read costs about 9 ms on this brick** | Derived from the round-trip table below. It is why the `drive` readback returns two values and not six |
| Round trip over USB, by command: `hello` **19 ms** (no sysfs at all), `motor_run` 38 ms, `drive` **96 ms**, `poll` 131 ms | Measured 2026-08-29 over 15 calls each on an idle brick. The SSH link is only 19% of a drive; the rest is the brick reading and writing sysfs |
| Under contention - a second process polling sysfs at 10 Hz - `drive` rose to **168 ms typical, 372 ms maximum** | Read off the `ev3ctl drive` footer during the hardware run. Still comfortably inside the watchdog's 1000 ms, and the trip counter stayed at 0 |
| **Trimming the `drive` readback from six values per side to two cut the round trip from 144 ms to 96 ms** | Measured before and after. It is the single largest thing this project has done for control latency, and it was done by returning less |
| `ev3ctl drive` works end to end on hardware: `stop_action` reported `coast -> brake`, `w` drove both motors at duty 40 and 325/335 deg/s, `a` counter-rotated at -40/+40, `w`+`a` pivoted at 0/40, `space` and `q` both stopped everything, exit 0, terminal restored | Driven through a pty against the real brick, 2026-08-29. Only a human pressing real keys is untested |
| A Large Motor at duty 40 turns at roughly **325 to 340 deg/s** | Read back from both motors during `drive` |
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

### Bluetooth PAN on macOS

**Read on 2026-08-29, on macOS 26.5.2.** The Mac and the brick pair
successfully: the brick appears as `ev3dev`, address `00:17:EC:ED:46:29`,
and shows as Connected. What it does not do is carry a network.

| Observation | Command |
| --- | --- |
| No Bluetooth network hardware port exists at all | `networksetup -listallhardwareports` |
| No "Bluetooth PAN" network service exists or can be ordered | `networksetup -listallnetworkservices` |
| The Mac's controller advertises `HFP AVRCP A2DP HID Braille LEA AACP GATT SerialPort` - no PAN, NAP or BNEP | `system_profiler SPBluetoothDataType` |
| The connected brick's services are `GATT ACL` - a base link with no network profile | same |

The pairing is not the problem and no amount of Brickman configuration
will fix it: there is no client on this side to connect a PAN to. Apple
removed Bluetooth PAN from macOS some releases ago, which is consistent
with everything above, though that is inference rather than something
read off this machine.

**What this costs the project.** Acceptance item 8 exists to prove that
`drive` does not care which transport it runs over. It cannot be run
here. The claim is still true by construction - there is no transport
code in this repository, only an `ssh` invocation and a `--host` string -
but "true by construction" is exactly the kind of claim this document
exists to distrust.

**PPP over a Bluetooth serial link was tried next, and does not work
either.** Tested 2026-08-29. It looked promising from the Mac alone -
the controller advertises `SerialPort`, `/usr/sbin/pppd` is installed,
and macOS had already created `/dev/tty.EV3` and `/dev/cu.EV3`. Every
one of those is on the wrong side of the link.

| Blocker | Evidence |
| --- | --- |
| **`pppd` is not installed on the brick.** The kernel has `CONFIG_PPP=m`, so the capability exists, but the daemon does not | `/usr/sbin/pppd: No such file or directory` on the brick |
| **The brick advertises no Serial Port profile.** There is nothing for PPP to run over | `sdptool browse local` returns zero service records |
| **Root is required and unavailable.** `rfcomm` binding and `pppd` both need it | `sudo -n true` fails; the account needs a password |

Installing `pppd` would fix the first and is not an option: the brick
has no route to the internet, and this project does not install anything
on it.

**And `/dev/tty.EV3` is not this brick.** That was an inference from the
USB gadget MAC `12:16:53:43:46:af` appearing to derive from
`00:16:53:43:46:af`, and it was wrong. `hciconfig` on the brick reports
its adapter as **`00:17:EC:ED:46:29`**, which is the device macOS shows
as `ev3dev`, and macOS has created no serial port for it. The `EV3`
pairing at `00:16:53:43:46:af` is something else - most plausibly this
brick under the stock LEGO firmware, which does advertise SPP where
ev3dev does not.

**DHCP would not have helped at any point**, and it is worth saying
plainly because it is the natural thing to reach for. DHCP hands out
addresses over a link that already exists; every failure above is the
absence of the link itself. PPP would not have needed DHCP either - it
negotiates addresses itself.

### Bluetooth without IP at all, which very nearly worked

Tried 2026-08-29, and the interesting result of the three.

Everything above assumed the brick needs an IP address, because `ev3ctl`
reaches it over SSH. **The protocol does not need one.**
`agent/ev3_agent.py` reads newline-delimited JSON on stdin and writes it
on stdout; it has no idea SSH exists. Any byte stream will do, and
Bluetooth RFCOMM is a byte stream.

With the operator's explicit permission to use `sudo` on the brick - an
exception to the rule in CLAUDE.md, taken deliberately and reverted
afterwards - the brick was made to offer one:

```
bluetoothd --compat            # so sdptool may register records at all
sdptool add --channel=1 SP     # "Serial Port service registered"
rfcomm watch /dev/rfcomm0 1 <wrapper that execs the agent on that tty>
```

All runtime only; nothing was installed and nothing survives a reboot.

**What was demonstrated.** macOS noticed the new profile and created
`/dev/tty.ev3dev` and `/dev/cu.ev3dev`, which had not existed before. On
the first connection, bytes crossed: a JSON command written on the Mac
arrived at the brick, the brick's tty echoed it back, and the wrapper
ran. **No IP, no SSH, no PPP, no DHCP anywhere in that path.**

**What was not.** No protocol round trip ever completed. The agent was
missing from `/tmp` at that moment - the brick had rebooted, and `/tmp`
does not survive that - so it exited before replying. By the time the
agent was back in place, macOS would no longer open the channel at all:
`open("/dev/cu.ev3dev")` succeeds and writes succeed, while a listener
on the brick sees nothing for 25 s. It connected exactly once, minutes
after the SDP record was registered, and never again. That is consistent
with macOS caching an RFCOMM session and satisfying later opens against
the cache without re-establishing anything; clearing it would mean
restarting the Mac's Bluetooth daemon or re-pairing, neither of which
was worth doing to the operator's other devices.

**Why it never completed, established over about seven attempts.**
macOS opens the RFCOMM channel **exactly once per `bluetoothd`
lifetime**. The first connection after the daemon starts reaches the
brick; every one after that has `open()` and `write()` succeed locally
against a cached session while the brick's listener sees nothing at all.
Two for two on the working case - the first attempt ever, and the first
after a `sudo pkill bluetoothd` - and five for five on the failing one.

Both times the channel did open, the brick side had a different fault,
and they were found in the wrong order:

1. First working channel: `/tmp/ev3_agent.py` was missing, because the
   brick had rebooted and `/tmp` does not survive that. The agent exited
   before replying.
2. Second working channel: the agent was present, but `rfcomm watch`
   starts its command at the instant the device node is created, before
   the channel has settled, so the agent read EOF and exited silently -
   an empty `agent_bt.log` next to a live listener.

The second was fixed by decoupling: `rfcomm` maintains the device, and a
separate supervisor waits for it and then runs the agent. That fix has
never yet met a fresh channel, so **no protocol round trip has completed
over Bluetooth, and no round-trip time exists to compare with USB's
96 ms.**

**This is what rules it out as a transport, independently of whether the
next attempt would answer.** A link that can be established once per
Bluetooth-daemon lifetime would need the operator to restart their Mac's
Bluetooth - dropping every other device they own - before each run. That
is not a development link, and it is not a driving link either.

**What it means for this project.** The transport independence that
acceptance item 8 exists to prove is real, and stronger than the item
assumed: the agent is a byte-stream program, so the question is not
"which network" but "which stream". A serial transport beside the SSH
one in `link.py` would be perhaps forty lines.

It is still the wrong thing to build. The brick has one radio, Phase 3
gives it to the DualShock 4, and a development link that competes with
the gamepad is the exact mistake this project's design was built to
avoid. Recorded because it is true, not because it should be pursued.

The honest options, none of them urgent:

- **A USB WiFi dongle in the brick's host port.** The standard ev3dev
  answer, gives the brick a real IP, and proves the same thing item 8
  was written to prove. A hardware purchase.
- **A Linux host**, which does still have PAN, if one is to hand.
- **PPP over a Bluetooth serial link.** Ruled out above: the brick has
  neither the daemon nor the profile, and cannot be given either.
- **Accept USB only** and rewrite item 8 to say so. The gamepad is
  getting the radio in Phase 3 regardless, and this project's own rule
  has always been that Bluetooth is not a development link.

Note the last one is not a defeat. Item 8 was a nice-to-have; the rule
that the radio belongs to the gamepad is the actual design.

### Unverified, and how each one gets resolved

| Claim | Why it is not verified | Resolved by |
| --- | --- | --- |
| **Round-trip time over Bluetooth PAN**, typical and maximum | **Blocked, not merely unmeasured.** This Mac appears to provide no Bluetooth PAN client at all - see "Bluetooth PAN on macOS" below. Until a second transport exists, `drive` is transport-independent by construction and not by demonstration | **Phase 2a, acceptance item 8**, which needs either a different host or a different second transport |
| Whether the brick itself can offer Bluetooth PAN (NAP) | Not tested. The brick was off the USB bus when the question came up, and with no client on the Mac there was nothing to test against | Only worth answering if a PAN client is found |
| Whether `hid-sony` binds the DualShock 4 on this kernel | No gamepad has been paired. `hid-sony` is not confirmed present in this kernel build either | **Phase 3.** Pair the controller, then look for the device under `/dev/input/` and check the driver bound to it |
| That the dashboard's keys drive a motor, and that a device unplugged mid-session empties its row | A motor has now been commanded over the real link and turned, and `ev3ctl scan` shows both motors in the right rows. What has not been exercised on hardware is `ev3ctl live` itself: its key handling, its rescan-on-replug, and its teardown | **Phase 2**, acceptance items 4, 5 and 8 |
| That a motor stops when the **USB cable is physically pulled** | Both mechanisms that would stop it are now proven on a real turning motor: the watchdog at 0.94 s with the link up and the host silent, and the EOF path at 0.12 s when the link is torn down. What has not been done is the physical act, which is the one case where the link neither closes cleanly nor stays up | **Phase 2**, acceptance item 7. It needs a hand on the cable and cannot be done remotely |
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

**Status: implemented. Acceptance items 1, 2, 3 and the substance of 7
done on hardware; 4, 5, 6, 8 and 9 outstanding.** `ev3ctl scan` has run against the real brick and
printed its real kernel, Python version, release and battery; those
readings are in the Verified table above.

A motor **has** now been commanded over the real link and turned, and
both mechanisms that stop a latched motor have been measured on it: the
watchdog cut the drive 0.94 s after the host went silent, and the EOF
path cut it 0.12 s after the link was torn down. **The physical cable
pull itself has still not been done**, and it cannot be done remotely.
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

## Phase 2a: Keyboard tank drive

**Status: implemented, and items 1 to 7 verified on the real brick by
driving the program through a pty.** What remains is a human pressing
real keys, and everything involving Bluetooth PAN or a chassis: items 8,
9 and 10.

**Goal.** Something that drives, before there is a gamepad or a
vehicle. `ev3ctl drive` tank-steers two motors from WASD, and works
unchanged over USB and over Bluetooth PAN because both are the same SSH
invocation with a different `--host`.

This is the short way to a moving robot, in the same spirit as the
sibling project's skid-steer phase. Nothing in it is wasted: the mixing,
the slew limiting and the safety shape are what Phase 4 reuses when the
gamepad replaces the keyboard.

**Work.** A `drive` agent command applying both sides in one message; a
`set_stop_action` agent command; `src/ev3ctl/mixer.py` holding the
arithmetic as pure functions; `src/ev3ctl/cli/drive.py` holding the
loop. The agent's watchdog, its `finally` and its EOF handling are
untouched.

### Two decisions worth reading before the tests

**`stop_action` is now set to `brake` at startup, and that is the point
of this phase.** The driver default is `coast`. Measured on
2026-08-29: with `coast`, after the drive is cut the motor freewheels
and is still turning at 91 deg/s a third of a second later, reaching
rest at 0.66 s. With `brake` it is under 32 deg/s within 0.11 s. On a
bench that difference is invisible. On a vehicle it is the difference
between stopping and rolling on after the link has died.

`stop_action` persists on the brick until it is rebooted or the motor is
`reset`. So the first run after a boot reports `coast -> brake`, and
every run after that reports it as already `brake`. Both are correct.

**Teardown restores the terminal before it talks to the brick**, which
is the opposite order to the one this phase was specified with. The
reason: restoring the terminal takes microseconds, so doing it first
delays the motor stop by nothing measurable, while doing it last would
leave the operator's shell in cbreak for as long as `stop_all` and `bye`
take to time out on a link that is already dead — up to two seconds. The
brick's watchdog stops the motors at 0.94 s either way, which is what
makes the ordering a usability question rather than a safety one.

**Acceptance test.** Motors on the bench, not on a vehicle, for items 1
to 7. Each says what to observe and what to check when it does not
happen.

1. **It starts.** `uv run ev3ctl drive` over USB.
   **Observe:** both motors named in the Drive table, and a `stop`
   line in the header reading `stop_action coast -> brake`.
   **If not:** "already brake" means the brick has not been rebooted
   since a previous run, which is correct. "stop_action unchanged" in
   red means the write was refused; check `stop_actions` on the motor
   contains `brake`.

2. **Hold `w`.** Both motors ramp up over roughly a third of a second,
   not instantly, then hold a steady duty. Release.
   **Observe:** `Cmd` climbs 6, 12, 18 … to `--speed`, and both sides
   are equal. On release both drop to 0 within about 150 ms.
   **If not:** an instant jump to full means the slew limit is not being
   applied. A stutter while holding means `--initial-hold-ms` is below
   the operating system's auto-repeat delay; raise it.

3. **Tap `w` once and let go.** The motors run about 600 ms and stop.
   **Observe:** exactly that. **This is correct behaviour, not a
   fault** — a terminal sends no key-release event, so a key is held
   until it times out. `--initial-hold-ms` is that timeout.

4. **Hold `a` alone.** The two motors counter-rotate, spinning in place.
   **Observe:** `Cmd` equal and opposite, for example -40 and +40.
   **If not:** if both turn the same way, one motor is mounted mirrored;
   use `--invert-left` or `--invert-right`.

5. **Hold `w` and `a` together.** One side stops and the other drives:
   the vehicle pivots about the stopped wheel.
   **Observe:** `Cmd` reading 0 and 40, not two similar numbers.
   **This differs from what this phase was originally specified to do**,
   which described both wheels turning at different speeds. The mixing
   the phase specifies — `left = throttle + turn`, normalised by the
   larger magnitude — cannot produce that at full deflection: full
   forward plus full left is `0` and `2`, which normalises to `0` and
   `1`. The formula was kept and this criterion corrected, rather than
   shipping a test that must fail. Scaling the turn axis below 1.0 is
   what would produce a gentle arc, if that is ever wanted.

6. **Hold `w`, then press `space` while still holding it.**
   **Observe:** both sides drop to 0 at once, faster than the slew limit
   alone would allow, without waiting for `w` to time out.
   **If not:** check `space` is reaching the program at all; the display
   shows the held key set.

7. **Hold `w`, then press `q`.** Then repeat with Ctrl-C.
   **Observe:** motors stop, terminal echo works, exit status 0 for `q`
   and 130 for Ctrl-C.
   **If not:** if the shell is left without echo, `stty sane` recovers
   it; the terminal is restored before anything is sent to the brick,
   precisely so a dead link cannot cost you your shell.

8. **Bluetooth PAN. Blocked on this Mac** - see "Bluetooth PAN on
   macOS" above; macOS 26.5.2 offers no PAN client, so there is nothing
   for the brick to connect to. Run this against any second transport
   that does exist, such as a WiFi dongle in the brick's host port.
   Bring the second transport up, unplug USB entirely, and run
   `ev3ctl drive --host robot@<its address>`.
   **Observe:** everything above behaves the same, and the footer shows
   a round-trip time. **Record the typical and maximum here.** Over USB
   the same command measures **96 ms** typical on an otherwise idle
   brick, rising to **168 ms typical and 372 ms maximum** while another
   process was also reading sysfs. Quote the second pair when comparing:
   a driving robot is never the only thing running.
   **If not:** if the round trip approaches 1000 ms the brick's watchdog
   will cut the motors under a held key, and the footer's watchdog trip
   counter will start climbing. That is the number that decides whether
   PAN is usable for driving at all.

9. **Walk away.** Over PAN, hold `w` and walk away from the brick until
   the link drops.
   **Observe:** the motors stop, and stop rather than freewheel.
   **If not:** if they coast to a halt, `stop_action` did not take;
   check item 1.

10. **On the floor.** Attach both motors to any rolling chassis, however
    crude, and run with `--speed 25`.
    **Observe:** forward, back, spin in place, and a prompt stop when
    the keys are released.
    **If not:** a vehicle that turns the wrong way needs `--invert-*`,
    not a code change.

**Pass:** 7, 9 and 10. Item 9 is the one this phase exists for.

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

**`stop_action` is `coast`, and that is a decision this phase has to
make deliberately.** Measured on 2026-08-29: when the watchdog cuts a
motor at duty 40, the drive stops in under a second but the motor
freewheels for a further 0.66 s before it is actually still. On a bench
that is invisible. On a vehicle it means the car keeps rolling after the
link dies, and rolls further the faster it was going. `stop_actions`
offers `coast`, `brake` and `hold`. Nothing in this project sets it yet,
so the driver default applies. Whether a safety stop should brake rather
than coast is a Phase 5 question, and the answer is probably yes.

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
