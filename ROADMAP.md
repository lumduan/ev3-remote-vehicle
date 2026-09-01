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
| **The latch test passes. A motor turning under command stops promptly when the USB cable is physically pulled.** | Done on 2026-08-29. outA was commanded to duty 30 and turning at about 198 deg/s when the operator pulled the cable, and reported that it stopped fast. On reconnection - with the brick's uptime continuous, so no reboot intervened - the motor read `duty_cycle` 0, `speed` 0, empty `state`, and `duty_cycle_sp` still 30. That combination is the signature of the brick having written `stop` to itself: the drive removed while the setpoint it was given stays untouched. **This is the test the project exists to pass** |
| **The motor on outD is mechanically stalled.** At duty 30, 50, 60 and 70 it reports the full commanded `duty_cycle`, `speed` 0, an unchanging `position`, and `state` of `running stalled` | Measured 2026-08-29. It degraded across the session: 335 deg/s, then 158, then 0. It enumerates and accepts commands, so this is not wiring or software - something is jamming the shaft or the motor has failed. **A stalled motor draws heavy current; do not keep driving it** |
| A stall is detectable from `duty_cycle` and `speed` alone, with no extra attribute read | Non-zero applied duty with zero speed, held for four frames. `state` would say `stalled` outright but costs about 9 ms a side |
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

### Verified: the gamepad

Read off the hardware on **2026-09-01**, over the USB link, by SSH. These
hold for **BlueZ 5.43-2+deb9u2** on kernel **4.14.117-ev3dev-2.3.5-ev3**.
A different kernel could bind a different driver, so the versions are
part of the fact, not context for it.

| Fact | Where it came from |
| --- | --- |
| The gamepad is `00:22:68:F2:5C:B6`, `Wireless Controller`, class `0x002508`, icon `input-gaming` | `bluetoothctl info` on the brick |
| Its `Modalias` is `usb:v054Cp09CCd0100`. Vendor `0x054C` is Sony, so it is genuine; product `0x09CC` makes it a **CUH-ZCT2**, the 2016 revision | The same `info` output. Read from the PnP Information record, not from the packaging |
| It advertises exactly two UUIDs: **Human Interface Device `0x1124`** and PnP Information `0x1200` | The same `info` output |
| **`hid-sony` binds it.** `sony 0005:054C:09CC.0004: input,hidraw0: BLUETOOTH HID v81.00 Gamepad [Wireless Controller] on 00:17:ec:ed:46:29` | `dmesg` on the brick. No `hid-generic` line appears anywhere in a buffer that ran unbroken from boot. **This resolves the open question this phase was blocked on** |
| hid-sony creates **three** input devices for one controller: `Wireless Controller`, `Wireless Controller Touchpad` and `Wireless Controller Motion Sensors` | `dmesg` and `/proc/bus/input/devices`. Decoding the bitmask of the wrong one would give a confidently wrong answer, so the pad must be matched on its exact name |
| The pad's evdev node is `/dev/input/event4`, `Uniq=00:22:68:f2:5c:b6`, `Bus=0005 Vendor=054c Product=09cc Version=8100` | `/proc/bus/input/devices` |
| **`B: ABS=3003f`**, so `ABS_X`, `ABS_Y`, `ABS_Z`, `ABS_RX`, `ABS_RY`, `ABS_RZ`, `ABS_HAT0X` and `ABS_HAT0Y` all exist. **`ABS_Z` and `ABS_RZ` are present**, which is the evidence that L2 and R2 report as analog axes, and therefore that the controller sends the full 78-byte report `0x11` rather than the 10-byte `0x01` | `/proc/bus/input/devices`. Bits 0-5 and 16-17 of the mask |
| `/dev/input/by-id/` **does not exist on this brick**, so there is no `-event-joystick` symlink to open. `/dev/input/by-path/` holds only the two built-in devices. Code must find the pad by name in `/proc/bus/input/devices`, not by a stable path | `ls` on the brick |
| **The `trust` flag works.** One press of PS reconnected the gamepad with no host-side action at all. That is a hard requirement for standalone operation, where there is no computer present to initiate anything | Observed 2026-09-01 by the operator |
| The Bluetooth radio is **rfkill soft-blocked at boot**, because ConnMan's bluetooth technology reads `Powered = False`. `bluetoothctl power on` fails with `org.bluez.Error.Blocked` until `connmanctl enable bluetooth` clears it, and neither step needs root | `/sys/class/rfkill/rfkill0/soft` went 1 to 0 and `hciconfig hci0` went `DOWN` to `UP RUNNING PSCAN` |
| A power supply's `scope` is what separates the brick's pack from a peripheral's: the brick reads `System` and offers `voltage_now` but no `capacity`, while a battery inside an attached device reads `Device` | `/sys/class/power_supply/lego-ev3-battery/`, read 2026-09-01 at 7.90 V |

| **The controller connects and produces events.** `sony 0005:054C:09CC.0001: input,hidraw0: BLUETOOTH HID v81.00 Gamepad [Wireless Controller] on 00:17:ec:ed:46:29`. **92 events were read in 12 s** shortly after connecting - the first ever read from this pad | `dmesg` and a `python3` reader on `/dev/input/event4`, 2026-09-01. This retires the "not one event has ever been read" claim. Those 92 events were the pad settling, not steady drift: a later 15 s reading produced none |
| **`/dev/input/event4` opens and reads as `robot`, without root or sudo** | `os.open(path, O_RDONLY \| O_NONBLOCK)` succeeded from an ordinary ssh session, 2026-09-01. Nothing in this project needs to escalate to read the pad |
| **`struct input_event` is 16 bytes on this brick**, confirmed by running `struct.calcsize("=llHHi")` there | `python3` on the brick, 2026-09-01. Previously reasoned from the ARM word size; now measured. The native `"@llHHi"` is 24 on any 64-bit development host, and parsing 16-byte records as 24-byte ones yields nonsense without raising |
| **One controller produces three input devices, and all three carry the same `Uniq`** `00:22:68:f2:5c:b6`: `Wireless Controller` on `event4`, `Wireless Controller Touchpad` on `event2`, `Wireless Controller Motion Sensors` on `event3` | `/proc/bus/input/devices`, 2026-09-01. This is why identity is the **pair** (`Uniq`, `Name`): `Uniq` alone returns three devices and would call every run ambiguous |
| **Only the gamepad declares `BTN_SOUTH`.** Its `B: KEY=7fdb0000 0 0 0 0 0 0 0 0 0` has bit 304 set in word 9; the Touchpad's `KEY=2420 0 10000 0 ...` does not, and the Motion Sensors device has no `KEY` line at all | `/proc/bus/input/devices`, 2026-09-01. This is the test that separates the three, and it needs no module to be loaded |
| **joydev is not loaded on this brick.** `/dev/input/` holds `event0` to `event4` and `by-path`, and nothing else; `lsmod` has no `joydev` | `ls` and `lsmod`, 2026-09-01. An earlier version of the device discovery used a `js` handler to identify the pad. There is no js node here, so that test could never have fired |
| **`B: ABS=3003f` decodes to six axes plus one hat**: `ABS_X` 0, `ABS_Y` 1, `ABS_Z` 2, `ABS_RX` 3, `ABS_RY` 4, `ABS_RZ` 5, `ABS_HAT0X` 16, `ABS_HAT0Y` 17 | `/proc/bus/input/devices`, 2026-09-01, decoded by `axis_codes_from_mask` |
| **`ABS_Z` and `ABS_RZ` are present, so the controller sends the full 78-byte Bluetooth report `0x11` and not the 10-byte `0x01`.** The minimal report carries no analog trigger axes at all, so their presence is what proves which report is in use - and therefore that proportional throttle is possible at all | The same mask. Confirmed with the pad connected and reporting, 2026-09-01 |
| **The D-pad is a hat, not four buttons.** Bits 16 and 17 are `ABS_HAT0X` and `ABS_HAT0Y`, with a declared range of 2. A step listening only for `EV_KEY` records nothing when the D-pad is pressed | The same mask, 2026-09-01 |
| **A stick does not rest at the midpoint of its range.** `EVIOCGABS` reports `ABS_X` resting at **136** of 0-255, whose midpoint is 127.5. Its travel is therefore **136 counts one way and 119 the other**, a 14 percent asymmetry; `ABS_RY` rests at 124 | `EVIOCGABS` through the agent, 2026-09-01. A consumer dividing both directions by one symmetric figure makes one that much stronger than the other |
| **Resting values are not stable between connections and must be measured per run.** `ABS_Y` was seen settling through 115-116 shortly after one connection and read 128 on the next. A rest value in a mapping file describes that session, not the controller | Two readings on 2026-09-01, one from the event stream and one from `EVIOCGABS`. This is why step 1 of the wizard measures rest every run rather than trusting a stored figure |
| **A resting DualShock 4 can emit no events whatsoever.** 15 s untouched produced **zero** events on every axis, so a measured jitter spread of 0 is a real outcome, and the 3x-jitter deadzone suggestion is then 0 | A reader on `event4`, 2026-09-01. `driver_flat` is the useful number in that case, and the mapping file carries both |
| **The driver's own deadzone hint is `flat=15`** on all six stick and trigger axes, with `fuzz=0`; the two hat axes report `flat=0` | `EVIOCGABS`, 2026-09-01. Worth comparing against any deadzone derived from measured jitter, which on a still controller can be much smaller |
| **`EVIOCGABS` works through the agent on this hardware.** Request `0x80184540 + code` returns a 24-byte `input_absinfo`; all six stick and trigger axes report `min=0 max=255`, and the two hats report `min=-1 max=1` | Run through `gamepad_open` against the real brick, 2026-09-01. The hats' declared range of 2 is what excludes a brushed D-pad from the stick steps |
| **The first `hello` costs 17.1 s on this brick, against a 96 ms steady-state round trip.** `ssh true` alone is 3.9 s and `python3 -c pass` is 9.6 s, at load average 3.08 with the gamepad connected. The 8 s `DEFAULT_TIMEOUT_S` is generous for a command and far too short for the handshake, which also pays for ssh connecting, python3 starting and the agent compiling | Timed from the host, 2026-09-01. `HANDSHAKE_TIMEOUT_S` in `link.py` is now separate, and `ev3ctl gamepad` would not connect at all before it was |
| **The pad's battery reads 80 percent, `Discharging`**, at `/sys/class/power_supply/sony_controller_battery_00:22:68:f2:5c:b6/` | `cat`, 2026-09-01. The flat battery that blocked Phase 3 in the previous session is no longer the blocker |
| **The host cannot open the connection; the pad must.** `bluetoothctl connect` fails with `org.bluez.Error.Failed` even when paired and trusted, and one press of PS then connects it | Observed 2026-09-01 while re-pairing. The trust flag is what lets the brick accept the pad's incoming connection with no computer present |
| **A stale bond presents as a slow white blink.** The pad advertised at RSSI -36 and would not connect until the old bond was removed and it was paired afresh; `trust` then had to be set again, because `remove` discards it | `bluetoothctl` on the brick, 2026-09-01 |

<!-- Still held, for the first successful run of `ev3ctl gamepad`. These
     rows are deliberately commented out: the wizard has not been run,
     and a fact in the Verified table with no reading behind it is
     exactly what this document exists to prevent.

| The axis mapping was captured over ____ and lives in `docs/gamepad-mapping.json` | `uv run ev3ctl gamepad` on ____-__-__ |
| Which evdev axis each stick and trigger moves: ____ | The same run. Every assignment came from an observation made during a named step, never from a published layout |
| Which axis of each stick is horizontal, and which polarity means right and up: ____ | The same run, the two directional holds inside steps 2 and 3 |
| This pad ____ the usual evdev polarity convention (X up to the right, Y up downward) | The same run. Recorded as a data point; nothing in the capture consulted it |
| The pad's Name over USB is ____, against `Wireless Controller` over Bluetooth | `/proc/bus/input/devices` with the pad connected each way. See "Names by transport" |
| L2 and R2 report ____ intermediate values, so proportional throttle is ____ | The same run, steps 4 and 5 |
| The driver's own `flat` deadzone hint, per axis: ____ | `EVIOCGABS` through the agent, reported in the same file |
-->

**The input probe aborted, and the cause is a flat battery.** With the
gamepad paired and trusted, the operator pressed PS once. It connected on
its own, the light bar lit solid white and then turned blue, and a few
seconds later the controller powered itself off. No stick was moved, so
the probe read no events and was terminated. `dmesg` records **eleven**
bind-and-vanish cycles, `0005:054C:09CC.0004` through `.000B`, each a
clean `sony` bind followed by the device disappearing. That is a power
fault and not a Bluetooth fault: the pack had charge enough to bring up
the radio and complete a connection, but not to hold the radio and the
light bar up together. Nothing so far suggests the controller is faulty.

**The light bar changing colour is evidence in its own right.**
`hid-generic` has no LED driver and never sets a light bar colour.
Something assigned this controller a per-device colour, and only a
Sony-specific driver does that. Alone it would be suggestive rather than
conclusive; alongside the `sony` line in `dmesg` it is not needed.

**A battery node would be the same kind of evidence.** `hid-generic`
does not parse the DualShock 4's battery field and creates no
`power_supply` node at all, so a node reporting `scope` of `Device`
under `/sys/class/power_supply/` can only have been made by a driver
that understands this controller. `agent/battery_report.py` reports it,
and classifies by `scope` rather than by node name because hid-sony
names the gamepad's node after the controller's MAC address - the one
thing that differs per controller. Run with the gamepad off, it prints
the brick's pack alone and says the gamepad node is absent.

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
| The DualShock 4's full axis and button mapping, and its stick centre and resting drift | **Blocked on nothing but one operator run.** The pad connects, events have been read from it, and its battery is at 80 percent; `ev3ctl gamepad` exists and its arithmetic is unit-tested off the brick. What has not happened is a person holding the controller and working through the eight steps, so which evdev code each physical control produces is still unrecorded - `ABS_Y`'s resting value is the only axis figure measured so far | **Phase 3**, acceptance items 3 and 4. One run of `uv run ev3ctl gamepad`, which writes `docs/gamepad-mapping.json` and fills in the block still held above |
| That the dashboard's keys drive a motor, and that a device unplugged mid-session empties its row | A motor has now been commanded over the real link and turned, and `ev3ctl scan` shows both motors in the right rows. What has not been exercised on hardware is `ev3ctl live` itself: its key handling, its rescan-on-replug, and its teardown | **Phase 2**, acceptance items 4, 5 and 8 |
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

7. **Latch test. PASSED on hardware, 2026-08-29** - see the Verified
   table. Kept here because it must be re-run whenever anything in the
   stopping path changes. Set a motor to duty 40, then **pull the USB
   cable out while it is running.**
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

### A stall the dashboard could not see

Found on 2026-08-29, after this phase was written. The motor on outD
stalled during testing - full duty applied, zero speed, `state` reading
`running stalled` - and `ev3ctl drive` showed nothing but a speed of
zero, which looks identical to a motor that has simply been asked to
stop.

The cause was a decision made earlier in this same phase. The `drive`
readback had been trimmed from six values per side to two to cut the
round trip from 144 ms to 96 ms, and `state` was one of the four
dropped. The trim was right and the round trip matters; what was wrong
was concluding that nothing was lost with it.

The fix costs nothing. A motor commanded non-zero, whose driver reports
it is applying that duty, with a speed of zero, is stalled by
definition - and both of those values were already in the reply. Held
for four frames so that spin-up from rest does not trip it, and shown
as `STALL` in the speed column with a banner in the footer. Verified
against the real stalled motor.

The general lesson is worth more than the fix: **an optimisation that
removes data removes diagnostics with it, and the diagnostics are missed
later than the latency is.**



## Phase 3: Gamepad pairing and evdev event-code mapping

**Goal.** The DualShock 4 pairs with the brick over Bluetooth Classic
HID, stays paired across a power cycle, and every control on it is
mapped to a known evdev event code — read off the device, not taken from
a table on the internet.

This is the phase that spends the brick's one radio. After it, the radio
is committed.

**Acceptance test.** On the physical brick, with the gamepad:

1. The controller pairs from the brick and appears as an input device.
   **Done 2026-09-01.** Paired and trusted at `00:22:68:F2:5C:B6`, and
   it appears as `/dev/input/event4`.
2. The driver bound to it is recorded. **Add it to the Verified table.**
   **Done 2026-09-01.** `hid-sony`, quoted in "Verified: the gamepad".
3. Every stick, trigger, D-pad direction and button produces an event,
   and the event code for each is written down. **The instrument for
   this is written: `ev3ctl gamepad`, a guided wizard. It has not been
   run.** See "How the mapping gets captured" below.
4. Stick centre values and resting drift are measured, not assumed. A
   deadzone that is guessed is a deadzone that is wrong. **Step 1 of the
   wizard measures both; nothing is measured until it is run.**
5. Power the brick off and on. The controller reconnects without being
   re-paired.
6. Switch the controller off mid-session. The brick notices, and does
   not hang waiting for it.
7. The USB development link still works while the controller is
   connected. If it does not, the two-link design is wrong and this
   phase has found it.

**Pass:** 3, 5 and 7. Item 4 is what makes Phase 4 possible; item 6 is
the first half of Phase 5.

**Where this phase stands.** Items 1 and 2 are done, and they were the
two the phase was actually blocked on. Items 3 to 7 all need a
controller that stays powered on, so they wait on a charge and on
nothing else. The mapping in item 3 is deliberately not guessed from a
table on the internet in the meantime: `B: ABS=3003f` says which axes
exist, and that is a different claim from knowing which one a given
stick moves.

What has changed is that the *instrument* now exists, so the phase no
longer waits on writing one. Nothing in the paragraph above has been
weakened: the wizard has not been run, and until it has, the Verified
block held for it stays commented out.

### How the mapping gets captured

`uv run ev3ctl gamepad` walks the operator through eight steps and
advances on its own as each is satisfied, showing live values throughout
rather than reporting at the end. It writes `docs/gamepad-mapping.json`.

Three decisions in it are worth knowing before reading the code:

- **Identity is the `Uniq` field, not the Name.** `Uniq` is the pad's
  own Bluetooth address, and hid-sony sets it on both transports; the
  Name is a label and may differ between them. The guard exists to catch
  one physical pad arriving over Bluetooth and USB at once - they use
  different HID report layouts, so a mapping taken from the wrong one is
  wrong without looking wrong - and a guard comparing Names would be
  watching the one field that is allowed to differ. The Name still seeds
  the search, matched on equality and never as a substring, because
  hid-sony's three devices for one controller all contain it. All three
  also share the `Uniq`, so the group is narrowed to the gamepad
  function by the one field that separates them: only the gamepad
  declares `BTN_SOUTH` in its `B: KEY=` mask, verified on hardware
  2026-09-01. Name is the fallback when `Uniq` is empty, and the report
  says which was used.
- **The ambiguity guard fires on one condition only:** the same exact
  Name carried by *different* `Uniq` values, which is two separate
  controllers of the same model, where mapping either would be picking
  one at random. The same `Uniq` under different Names is the ordinary
  case - one controller's three functions - and is accepted.
- **"80 percent of the range" is measured against the driver's range**,
  read per axis with the `EVIOCGABS` ioctl, not against the range
  observed so far. Against an observed range the test is self-referential
  early in a step and a twitch satisfies it. The driver's declared range
  also excludes the D-pad from the stick steps on its own evidence: a hat
  declares a range of 2, which is too coarse to be a stick, so a brushed
  D-pad cannot be named as one.
- **Which of a stick's two axes is horizontal is measured by two holds.**
  A full circular sweep moves both axes through their whole range and
  therefore cannot distinguish them, so the sweep is followed - inside
  the same step, reusing the pair it just named - by a prompt to push
  the stick fully right and hold, and then fully up and hold. The axis
  that deflects further from its step 1 rest mean is the one named, and
  the sign of that deflection is the polarity. The intended axis must
  out-deflect the other by three times; below that the push was a
  diagonal, and the hold is refused and retried rather than accepted,
  because an orientation taken from a diagonal is a coin flip that the
  file would go on to present as a measurement. Pushing sideways again
  during the up hold is refused for the same reason: it would name one
  axis both horizontal and vertical and leave the other unnamed.
- **The evdev polarity convention is recorded, never consulted.** X
  counting up to the right and Y counting up downward is the usual
  arrangement, and `matches_evdev_convention` says whether this pad
  agreed. Nothing in the capture reads it. A `false` there means the
  controller differs, not that the measurement is suspect.

### Names by transport

The ambiguity guard has to catch one physical gamepad appearing on
Bluetooth and USB at the same time. It identifies the pad by `Uniq`
rather than by Name precisely because the Name is expected to differ
between the two transports, and `Uniq` is not.

**That expectation is unverified.** The pad has only ever been seen over
Bluetooth, where `/proc/bus/input/devices` reports
`N: Name="Wireless Controller"` (`ROADMAP.md`, "Verified: the gamepad").
What it reports over USB has not been read off this brick, and the USB
string used in `tests/test_gamepad.py` is a stand-in chosen to make the
differing-Name case testable, not an observation.

Fill both rows in once the pad has been connected each way and
`/proc/bus/input/devices` read:

| Transport | `N: Name=` | `U: Uniq=` | `I: Bus=` |
| --- | --- | --- | --- |
| Bluetooth | `Wireless Controller` | `00:22:68:f2:5c:b6` | `0005` |
| USB | ____ | ____ | ____ |

If the two Names turn out to be identical, the Name-based guard would
have sufficed and `Uniq` is merely the better key. If they differ, the
Name-based guard would have missed the case outright, and this row is
the evidence for why identity moved.

The group is narrowed by the device's own `KEY` mask: only the gamepad
function declares `BTN_SOUTH`. That was chosen after an earlier version
leaned on joydev having bound a `js` handler, which turned out to be
unreachable - `lsmod` has no joydev on this brick and `/dev/input/`
holds no js node, so the test could never have fired. The mask is read
from the device itself and depends on no module being loaded.

The arithmetic - the parser, the advance gates, the rest statistics, the
16-byte `input_event` decode and the analog-versus-digital trigger test -
is unit-tested off the brick in `tests/test_gamepad.py`, because a wrong
comparison in a gate does not crash; it produces a step that waits
forever while the operator wonders which of the two of them is broken.

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
