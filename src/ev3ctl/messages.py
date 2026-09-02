"""HOST CODE. Every word the setup wizard says, in both languages.

One table, so a translation is read through in one sitting rather than
hunted for across a wizard, and so that a missing string is a test
failure rather than a blank box on a child's screen.

**The Thai needs a native reader's review.** It was written by someone
who is not one, for children, which is the audience least able to work
around an awkward phrase. Treat every `th` value here as a draft until
somebody who speaks Thai has read it.

Two rules for anything added here:

- **English first.** `en` is the fallback for every key, so a missing or
  empty `th` degrades to English rather than to nothing. `check_tables`
  proves the two sides carry the same keys; the fallback is for safety,
  not for permission to skip translating.
- **Write for a ten-year-old alone at the keyboard.** No jargon, no file
  paths, no commands. When something is wrong, name the thing to go and
  touch: a cable, a button, a switch. "Push the cable in at both ends"
  is useful; "exit 255, no route to host" is not.
"""

import os

LANGUAGES = ("en", "th")
DEFAULT_LANGUAGE = "en"

# Remembered so the choice is made once, not every run. Under the user's
# config directory rather than the repository, because it is a
# preference and not a project fact.
CONFIG_DIR = os.path.join(
    os.path.expanduser("~"), ".config", "ev3ctl")
LANGUAGE_FILE = os.path.join(CONFIG_DIR, "lang")

TEXT = {
    # -- the shell of the wizard --------------------------------------
    "app.title": {
        "en": "EV3 SETUP",
        "th": "ตั้งค่าหุ่นยนต์ EV3",
    },
    "app.subtitle": {
        "en": "Let's get your robot working.",
        "th": "มาทำให้หุ่นยนต์ของเราทำงานกันเถอะ",
    },
    "menu.hint": {
        "en": "up/down move   Enter choose   q quit",
        "th": "ลูกศรขึ้นลง เลือก   Enter ตกลง   q ออก",
    },
    "menu.check": {
        "en": "Check my robot",
        "th": "ตรวจดูหุ่นยนต์",
    },
    "menu.install": {
        "en": "Put the programs on the robot",
        "th": "ติดตั้งโปรแกรมลงในหุ่นยนต์",
    },
    "menu.drive": {
        "en": "Drive the robot",
        "th": "ขับหุ่นยนต์",
    },
    "menu.buttons": {
        "en": "Use the gamepad as the robot's buttons",
        "th": "ใช้จอยเป็นปุ่มของหุ่นยนต์",
    },
    "menu.autostart": {
        "en": "Start the buttons by themselves",
        "th": "ให้ปุ่มทำงานเองอัตโนมัติ",
    },
    "menu.battery": {
        "en": "How full are the batteries",
        "th": "ดูแบตเตอรี่",
    },
    "menu.help": {
        "en": "Something is wrong - help me",
        "th": "มีบางอย่างผิดปกติ - ช่วยด้วย",
    },
    "menu.language": {
        "en": "Language / ภาษา",
        "th": "ภาษา / Language",
    },
    "menu.quit": {
        "en": "Quit",
        "th": "ออกจากโปรแกรม",
    },

    # -- little words that appear in the right-hand column ------------
    "status.ok": {"en": "ok", "th": "ใช้ได้"},
    "status.bad": {"en": "problem", "th": "มีปัญหา"},
    "status.unknown": {"en": "not checked", "th": "ยังไม่ได้ตรวจ"},
    "status.ready": {"en": "ready", "th": "พร้อม"},
    "status.old": {"en": "old", "th": "เก่า"},
    "status.missing": {"en": "not there", "th": "ยังไม่มี"},
    "status.on": {"en": "on", "th": "เปิด"},
    "status.off": {"en": "off", "th": "ปิด"},
    "common.back": {
        "en": "Press Enter to go back",
        "th": "กด Enter เพื่อกลับ",
    },
    "common.working": {"en": "Looking...", "th": "กำลังตรวจ..."},

    # -- checking -----------------------------------------------------
    "check.title": {
        "en": "Checking your robot",
        "th": "กำลังตรวจดูหุ่นยนต์",
    },
    "check.cable": {"en": "The cable", "th": "สายเชื่อมต่อ"},
    "check.brick": {"en": "The robot answers", "th": "หุ่นยนต์ตอบ"},
    "check.motors": {"en": "The motors", "th": "มอเตอร์"},
    "check.pad": {"en": "The controller", "th": "จอยควบคุม"},
    "check.programs": {"en": "The programs", "th": "โปรแกรม"},
    "check.allgood": {
        "en": "Everything is ready. Go and drive it!",
        "th": "ทุกอย่างพร้อมแล้ว ไปขับกันเลย",
    },

    # -- what to do when a check fails --------------------------------
    "fix.cable": {
        "en": "I cannot reach the robot. Push the small end of the "
              "cable into the robot, in the little port next to the "
              "card slot. Push the big end into the computer.",
        "th": "ยังติดต่อหุ่นยนต์ไม่ได้ เสียบปลายเล็กของสายเข้าที่ช่อง"
              "เล็กข้างช่องใส่การ์ด และเสียบปลายใหญ่เข้าคอมพิวเตอร์",
    },
    "fix.brick": {
        "en": "The robot is not answering. Is it turned on? Its little "
              "screen should show a menu.",
        "th": "หุ่นยนต์ไม่ตอบ เปิดเครื่องแล้วหรือยัง "
              "หน้าจอเล็กควรแสดงเมนู",
    },
    "fix.brickman": {
        "en": "On the robot's own screen, choose Wireless and "
              "Networks, then All Network Connections, and switch the "
              "wired one on.",
        "th": "ที่หน้าจอหุ่นยนต์ เลือก Wireless and Networks แล้วเลือก "
              "All Network Connections และเปิดการเชื่อมต่อแบบมีสาย",
    },
    "fix.motors": {
        "en": "I found fewer than two motors. Check the cable from "
              "each motor to the robot is pushed in at both ends.",
        "th": "เจอมอเตอร์น้อยกว่าสองตัว ตรวจสายจากมอเตอร์ถึงหุ่นยนต์ "
              "ว่าเสียบแน่นทั้งสองด้าน",
    },
    "fix.pad": {
        "en": "The controller is asleep. Press the round PS button in "
              "the middle and wait a moment.",
        "th": "จอยกำลังหลับอยู่ กดปุ่ม PS กลมตรงกลาง แล้วรอสักครู่",
    },
    "fix.programs": {
        "en": "The programs are not on the robot yet. Choose "
              "\"Put the programs on the robot\" from the menu.",
        "th": "ยังไม่ได้ติดตั้งโปรแกรมลงหุ่นยนต์ "
              "เลือก \"ติดตั้งโปรแกรมลงในหุ่นยนต์\" จากเมนู",
    },

    # -- installing ---------------------------------------------------
    "install.title": {
        "en": "Putting the programs on the robot",
        "th": "กำลังติดตั้งโปรแกรมลงในหุ่นยนต์",
    },
    "install.copying": {"en": "Copying {0}", "th": "กำลังคัดลอก {0}"},
    "install.done": {
        "en": "Done. Both programs are on the robot now.",
        "th": "เสร็จแล้ว ทั้งสองโปรแกรมอยู่ในหุ่นยนต์แล้ว",
    },
    "install.failed": {
        "en": "That did not work: {0}",
        "th": "ทำไม่สำเร็จ: {0}",
    },
    "install.nolink": {
        "en": "I cannot reach the robot, so I cannot put the programs "
              "on it. Check the cable first.",
        "th": "ติดต่อหุ่นยนต์ไม่ได้ จึงติดตั้งโปรแกรมไม่ได้ "
              "ตรวจสายก่อน",
    },

    # -- driving ------------------------------------------------------
    "drive.title": {"en": "Drive the robot", "th": "ขับหุ่นยนต์"},
    "drive.wheels": {
        "en": "Lift the wheels off the table first, so it cannot drive "
              "away while you are learning.",
        "th": "ยกล้อให้ลอยจากโต๊ะก่อน หุ่นจะได้ไม่วิ่งหนีตอนหัดขับ",
    },
    "drive.how": {
        "en": "Push the left stick to drive. Hold L1 to change speed. "
              "Press Share to stop.",
        "th": "ดันก้านซ้ายเพื่อขับ กด L1 ค้างเพื่อเปลี่ยนความเร็ว "
              "กด Share เพื่อหยุด",
    },
    "drive.starting": {
        "en": "Starting. This takes a few seconds.",
        "th": "กำลังเริ่ม รอสักครู่",
    },
    "drive.running": {
        "en": "It is running on the robot now. Press Enter to come "
              "back here.",
        "th": "กำลังทำงานอยู่ในหุ่นยนต์ กด Enter เพื่อกลับมาที่นี่",
    },

    # -- gamepad-as-brick-buttons -------------------------------------
    "buttons.title": {
        "en": "The gamepad as the robot's buttons",
        "th": "ใช้จอยเป็นปุ่มของหุ่นยนต์",
    },
    "buttons.what": {
        "en": "The arrows move the robot's menu. X chooses. Share goes "
              "back, and stops a program that is running.",
        "th": "ปุ่มลูกศรเลื่อนเมนูของหุ่นยนต์ ปุ่ม X คือเลือก "
              "ปุ่ม Share คือย้อนกลับ และหยุดโปรแกรมที่กำลังทำงาน",
    },
    "buttons.turnedon": {
        "en": "Turned on. Try the arrows on the gamepad now.",
        "th": "เปิดแล้ว ลองกดปุ่มลูกศรบนจอยดู",
    },
    "buttons.turnedoff": {
        "en": "Turned off.",
        "th": "ปิดแล้ว",
    },

    # -- batteries ----------------------------------------------------
    "battery.title": {"en": "Batteries", "th": "แบตเตอรี่"},
    "battery.robot": {"en": "Robot", "th": "หุ่นยนต์"},
    "battery.pad": {"en": "Controller", "th": "จอยควบคุม"},
    "battery.nopad": {
        "en": "The controller is not connected, so I cannot see its "
              "battery.",
        "th": "จอยยังไม่ได้เชื่อมต่อ จึงดูแบตเตอรี่ไม่ได้",
    },

    # -- starting by itself -------------------------------------------
    "auto.title": {
        "en": "Start the buttons by themselves",
        "th": "ให้ปุ่มทำงานเองอัตโนมัติ",
    },
    "auto.what": {
        "en": "The robot can turn the gamepad buttons on by itself "
              "every time it starts. Then you never have to switch "
              "them on again.",
        "th": "หุ่นยนต์เปิดปุ่มจอยให้เองได้ทุกครั้งที่เปิดเครื่อง "
              "จะได้ไม่ต้องมาเปิดเองอีก",
    },
    "auto.on": {
        "en": "It is on. The buttons will work by themselves after the "
              "robot starts - just press PS on the gamepad.",
        "th": "เปิดอยู่แล้ว ปุ่มจะทำงานเองหลังหุ่นยนต์เปิดเครื่อง "
              "แค่กดปุ่ม PS บนจอย",
    },
    "auto.off": {
        "en": "It is not set up yet. Choose \"Put the programs on the "
              "robot\" first.",
        "th": "ยังไม่ได้ตั้งค่า เลือก \"ติดตั้งโปรแกรมลงในหุ่นยนต์\" ก่อน",
    },
    "auto.needsroot": {
        "en": "This last bit needs a grown-up, once. Ask them to type "
              "this on the computer:",
        "th": "ขั้นตอนสุดท้ายนี้ต้องให้ผู้ใหญ่ช่วยครั้งเดียว "
              "ขอให้เขาพิมพ์บรรทัดนี้ในคอมพิวเตอร์",
    },
    "auto.password": {
        "en": "It will ask for a password. The password is: maker",
        "th": "เครื่องจะถามรหัสผ่าน รหัสคือ maker",
    },
    "auto.explain": {
        "en": "That line tells the robot it may start your programs "
              "before anybody logs in. It is only needed once, ever.",
        "th": "บรรทัดนี้บอกหุ่นยนต์ว่าเริ่มโปรแกรมของเราได้ "
              "ก่อนที่จะมีใครล็อกอิน ทำครั้งเดียวพอ",
    },
    "auto.pressdone": {
        "en": "Press Enter when that is done and I will check.",
        "th": "ทำเสร็จแล้วกด Enter แล้วจะตรวจให้",
    },
    "auto.worked": {
        "en": "It worked. The buttons will start by themselves from "
              "now on.",
        "th": "สำเร็จแล้ว ต่อไปปุ่มจะเริ่มทำงานเองทุกครั้ง",
    },
    "auto.notyet": {
        "en": "Not yet. Nothing is broken - the line just has not been "
              "run. You can come back and try again any time.",
        "th": "ยังไม่สำเร็จ ไม่มีอะไรเสียหาย แค่ยังไม่ได้พิมพ์บรรทัดนั้น "
              "กลับมาลองใหม่เมื่อไหร่ก็ได้",
    },
    "check.autostart": {
        "en": "Starting by itself",
        "th": "การเริ่มทำงานเอง",
    },
    "status.needsroot": {
        "en": "needs a grown-up",
        "th": "ต้องให้ผู้ใหญ่ช่วย",
    },
    "install.moved": {
        "en": "I also tidied away an old copy at {0}",
        "th": "และเก็บกวาดไฟล์เก่าที่ {0} ให้แล้ว",
    },
    "install.service": {
        "en": "The robot is set to start the buttons by itself.",
        "th": "ตั้งค่าให้หุ่นยนต์เริ่มปุ่มเองแล้ว",
    },

    # -- help ---------------------------------------------------------
    "help.title": {
        "en": "Something is wrong",
        "th": "มีบางอย่างผิดปกติ",
    },
    "help.intro": {
        "en": "These five things go wrong most often. Try them in "
              "order.",
        "th": "ห้าอย่างนี้เกิดขึ้นบ่อยที่สุด ลองไล่ตามลำดับ",
    },
    "help.1": {
        "en": "The cable is loose. Push it in at both ends.",
        "th": "สายหลวม เสียบให้แน่นทั้งสองด้าน",
    },
    "help.2": {
        "en": "The robot is off. Press its middle button and wait for "
              "the menu.",
        "th": "หุ่นยนต์ปิดอยู่ กดปุ่มกลางแล้วรอจนขึ้นเมนู",
    },
    "help.3": {
        "en": "The controller is asleep. Press the round PS button.",
        "th": "จอยหลับอยู่ กดปุ่ม PS กลม",
    },
    "help.4": {
        "en": "The controller is flat. Plug it into a charger. It can "
              "charge while you use it.",
        "th": "จอยแบตหมด เสียบสายชาร์จ ใช้ไปชาร์จไปได้",
    },
    "help.5": {
        "en": "A motor cable is loose. The robot needs two motors.",
        "th": "สายมอเตอร์หลวม หุ่นยนต์ต้องมีมอเตอร์สองตัว",
    },
    "help.details": {
        "en": "Press d for the technical details, or Enter to go back.",
        "th": "กด d เพื่อดูรายละเอียดทางเทคนิค หรือ Enter เพื่อกลับ",
    },
}


def check_tables():
    # type: () -> list
    """Keys that are missing from a language. Empty means all present.

    Called by a test rather than at import: a missing string should stop
    a commit, not a child's robot.
    """
    problems = []
    for key, values in sorted(TEXT.items()):
        for language in LANGUAGES:
            if not values.get(language):
                problems.append("{0} has no {1}".format(key, language))
    return problems


def load_language():
    """The remembered language, or English."""
    try:
        with open(LANGUAGE_FILE) as handle:
            choice = handle.read().strip()
    except Exception:
        return DEFAULT_LANGUAGE
    return choice if choice in LANGUAGES else DEFAULT_LANGUAGE


def save_language(language):
    """Remember the choice. Never raises: it is only a preference."""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(LANGUAGE_FILE, "w") as handle:
            handle.write(language)
    except Exception:
        pass


def t(key, language=DEFAULT_LANGUAGE, *args):
    """One string, in one language, with `{0}` slots filled.

    Falls back to English, then to the key itself. A key that does not
    exist renders as its own name, which is ugly on purpose: it is
    obvious in a screenshot and impossible to mistake for a sentence.
    """
    values = TEXT.get(key)
    if values is None:
        return key
    text = values.get(language) or values.get(DEFAULT_LANGUAGE) or key
    if args:
        try:
            return text.format(*args)
        except (IndexError, KeyError):
            return text
    return text
