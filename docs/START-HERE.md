# Start here

This page shows you how to make the robot go.

You do not need to know anything about computers. If a word is new, it
is explained the first time it appears.

Thai version: [START-HERE.th.md](START-HERE.th.md)

---

## 1. What the parts are

You need four things.

**The brick.** The big grey box with a small screen and some buttons.
This is the robot's computer. Everything plugs into it.

**The card.** A tiny memory card that goes in the side of the brick. It
holds the software the brick runs.

**The cable.** A USB cable. The small end goes in the brick. The big end
goes in your computer.

**The controller.** A Sony DualShock 4, the kind used with a
PlayStation 4. This is what you hold to drive.

You also need **two motors** plugged into the brick, with their own
cables.

---

## 2. Put the card in and turn the brick on

1. Push the card into the slot on the side of the brick, until it
   clicks.
2. Press the round button in the middle of the brick.
3. Wait. The first time can take **two or three minutes**. That is
   normal.
4. You are ready when a menu appears on the brick's screen.

> **If the screen stays dark**, the batteries may be flat. Try new ones,
> or plug in the charger.

---

## 3. Plug in the cable

The brick has **two** USB sockets. They look similar and they are not
the same.

- The **small one, next to the card slot**. This is the one you want.
- The other one, on the opposite side, is for plugging things *into* the
  brick. Not this one.

Put the small end of the cable in the small socket. Put the big end in
your computer.

Then, **on the brick's own screen**, use its buttons to choose:

```
Wireless and Networks  ->  All Network Connections
```

and switch the wired connection on. This tells the brick to talk to your
computer through the cable.

---

## 4. Get the computer ready

You do this once, ever.

**Install `uv`.** This is a small program that fetches everything else
the robot software needs. Open a terminal — the app on your computer
where you type commands — and paste this:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Get the robot software.** Still in the terminal:

```bash
git clone https://github.com/lumduan/ev3-remote-vehicle.git
cd ev3-remote-vehicle
```

The first time you connect, the computer will ask for a password. It is:

```
maker
```

---

## 5. Open the setup menu

This is the only command you need to remember:

```bash
uv run ev3ctl setup
```

A menu appears:

```
  EV3 SETUP

  Let's get your robot working.

   >  Check my robot                      not checked
      Put the programs on the robot       not checked
      Drive the robot
      Use the gamepad as the robot's buttons       off
      How full are the batteries
      Something is wrong - help me
      Language / ภาษา                         English
      Quit

  up/down move   Enter choose   q quit
```

Use the **up and down arrow keys** to move. Press **Enter** to choose.
Press **q** to leave.

Choose **Language** if you would rather read Thai.

---

## 6. Do these two things, in order

**First, "Check my robot".**

It looks at each part in turn and tells you if something is wrong. If
one thing is wrong, it stops there and tells you what to go and touch.
Fix it, then choose Check again.

When everything is good it says so.

**Then, "Put the programs on the robot".**

This copies the driving program onto the brick so it can run on its own,
with no computer attached. You only do this once, and again whenever the
software changes.

---

## 7. Wake the controller up

Press the round **PS** button in the middle of the controller.

The light on the front should come on and stay on. That means it is
talking to the brick.

> **If the light blinks white and never settles**, the controller's
> battery is flat. Plug it into a charger. You can keep using it while
> it charges.

---

## 8. Drive it

**Lift the wheels off the table first.** Hold the robot in your hand or
rest it on a book, so it cannot drive off while you are learning.

Choose **Drive the robot** from the menu.

Wait a few seconds. The brick is slow to start a program.

| What you do | What happens |
| --- | --- |
| Push the **left stick** forward | Both wheels go forward |
| Pull it back | Both wheels go backward |
| Push it left or right | The robot spins on the spot |
| Push it forward and to one side | The robot turns in a curve |
| Let go | It stops |

**Hold L1 for one and a half seconds** to change speed. The light on the
brick changes colour to tell you which speed you are on:

| Light | Speed |
| --- | --- |
| Green | Slow — start here |
| Orange | Medium |
| Red | Fast |

**Press R1** to see how full the batteries are.

**Press Share** to stop the program.

---

## 9. Use the controller as the robot's buttons

Choose **Use the gamepad as the robot's buttons**.

Now the controller works the brick's own menus, so you never have to put
the robot down:

| Controller | Does |
| --- | --- |
| Arrow buttons | Move up and down the brick's menu |
| **X** | Choose the thing you have picked |
| **Share** | Go back. Also stops a running program |

You will hear a small click each time, so you know it worked.

Choose the same menu item again to turn it off.

---

## 10. When something goes wrong

These five things cause almost every problem. Try them in order.

**1. The cable is loose.** Push it in firmly at both ends. This is the
most common one by far.

**2. The brick is off.** Press its middle button. Wait for the menu.

**3. The controller is asleep.** Press the round PS button.

**4. The controller is flat.** Plug in a charger. It works while
charging.

**5. A motor cable is loose.** The robot needs two motors to drive.

Still stuck? Choose **Something is wrong - help me** in the menu. It
shows this list, and pressing **d** shows the technical details for a
grown-up to read.

---

## Words you might not know

**Terminal** — the app where you type commands to your computer.

**Command** — a line you type into the terminal and press Enter.

**Brick** — the grey box. The robot's computer.

**Brickman** — the menu on the brick's own screen.

**Gamepad / controller** — the thing you hold to drive.

**Program** — a set of instructions the brick follows.

---

## For grown-ups

This guide covers the happy path only. [README.md](../README.md) has the
engineering detail: how the two links differ, what has been measured on
real hardware and what has not, and why certain choices were made.
[ROADMAP.md](../ROADMAP.md) separates what is verified from what is
merely believed, which is the most useful page in the repository.
