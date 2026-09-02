"""Tests for the setup wizard's decisions and its two languages.

None of this needs a brick, a terminal or a child. What it does need to
catch is the failure mode a beginner tool has that a developer tool does
not: a string that is missing in one language shows as a blank box to
somebody who cannot guess what it should have said.
"""

import ast
import hashlib
from pathlib import Path

import pytest

from ev3ctl import messages, setup_checks as checks

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------
# The two languages
# ---------------------------------------------------------------------

def test_every_string_exists_in_every_language():
    """A blank box is worse to a beginner than an awkward phrase."""
    assert messages.check_tables() == []


def test_every_key_used_by_the_wizard_exists():
    """Found in the parse tree, not by grepping the text.

    A comment mentioning a key is not a use of one. This repository has
    already been bitten by exactly that: a test that searched source
    text for a method name failed on its own docstring.
    """
    source = (ROOT / "src" / "ev3ctl" / "cli" / "setup.py").read_text()
    tree = ast.parse(source)

    used = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = None
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name not in ("t", "say"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value,
                                                          str):
            used.add(first.value)

    assert used, "no message keys found - has the wizard changed shape?"
    missing = sorted(k for k in used if k not in messages.TEXT)
    assert missing == [], "used but not defined: {0}".format(missing)


def test_a_missing_thai_string_falls_back_to_english():
    """Degrade to English, never to nothing."""
    assert messages.t("menu.quit", "th") == "ออกจากโปรแกรม"
    # A key that exists in neither renders as itself: ugly on purpose,
    # so it is obvious in a screenshot and cannot read as a sentence.
    assert messages.t("no.such.key", "th") == "no.such.key"
    assert messages.t("no.such.key", "en") == "no.such.key"


def test_placeholders_survive_translation():
    """Both languages must keep the {0} the caller fills in."""
    for language in messages.LANGUAGES:
        assert "{0}" in messages.TEXT["install.copying"][language]
        assert "{0}" in messages.TEXT["install.failed"][language]
    assert messages.t("install.copying", "en", "tank_drive.py") == \
        "Copying tank_drive.py"


def test_a_bad_placeholder_does_not_crash_the_wizard():
    """Better a sentence with a gap than a traceback mid-menu."""
    assert messages.t("menu.quit", "en", "unused") == "Quit"


def test_the_language_file_is_a_preference_not_a_project_fact():
    """It lives under the user's config, not in the repository."""
    assert ".config" in messages.LANGUAGE_FILE
    assert str(ROOT) not in messages.LANGUAGE_FILE


def test_an_unknown_saved_language_falls_back(tmp_path, monkeypatch):
    bad = tmp_path / "lang"
    bad.write_text("klingon")
    monkeypatch.setattr(messages, "LANGUAGE_FILE", str(bad))
    assert messages.load_language() == "en"


# ---------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------

def fake_ssh(monkeypatch, replies):
    """Replace the one place that talks to the brick."""
    calls = []

    def _ssh(host, command, timeout=None, data=None):
        calls.append(command)
        for fragment, reply in replies:
            if fragment in command:
                return reply
        return True, ""

    monkeypatch.setattr(checks, "_ssh", _ssh)
    return calls


def test_link_check_uses_ssh_and_not_ping(monkeypatch):
    """mDNS hands back a dead IPv4 address for a brick that answers.

    A checklist that sends the operator to debug a working link is
    worse than no checklist - the reason is recorded beside
    DEFAULT_CHECKLIST in errors.py.
    """
    calls = fake_ssh(monkeypatch, [("true", (True, ""))])
    assert checks.check_link("robot@x")[0] == checks.OK
    assert calls == ["true"]


def test_link_check_reports_bad_when_ssh_fails(monkeypatch):
    fake_ssh(monkeypatch, [("true", (False, "no route to host"))])
    verdict, detail = checks.check_link("robot@x")
    assert verdict == checks.BAD
    assert "no route" in detail


def test_two_motors_pass_and_one_does_not(monkeypatch):
    fake_ssh(monkeypatch, [("tacho-motor", (True, "2"))])
    assert checks.check_motors("robot@x")[0] == checks.OK
    fake_ssh(monkeypatch, [("tacho-motor", (True, "1"))])
    assert checks.check_motors("robot@x")[0] == checks.BAD
    fake_ssh(monkeypatch, [("tacho-motor", (True, ""))])
    assert checks.check_motors("robot@x")[0] == checks.BAD


def test_the_gamepad_is_counted_by_exact_name(monkeypatch):
    """hid-sony makes three devices for one controller.

    The grep is anchored at both ends, so the touchpad and motion
    siblings do not count as three controllers.
    """
    calls = fake_ssh(monkeypatch, [("Wireless Controller", (True, "1"))])
    assert checks.check_gamepad("robot@x")[0] == checks.OK
    assert '^N: Name="Wireless Controller"$' in calls[0]

    fake_ssh(monkeypatch, [("Wireless Controller", (True, "0"))])
    assert checks.check_gamepad("robot@x")[0] == checks.BAD


def digests():
    return {name: checks.local_digest(name) for name in checks.PROGRAMS}


def test_matching_digests_report_ready(monkeypatch):
    have = digests()
    out = "\n".join("{0}  {1}/{2}".format(have[n], checks.BRICK_DIR, n)
                    for n in checks.PROGRAMS)
    fake_ssh(monkeypatch, [("md5sum", (True, out))])
    verdict, state, _ = checks.check_programs("robot@x")
    assert (verdict, state) == (checks.OK, "ready")


def test_a_different_digest_reports_old_not_missing(monkeypatch):
    """An old copy is subtler than a missing one: it runs.

    It behaves like the version it was rather than the version you
    have, which is a worse afternoon than a file that is not there.
    """
    stale = hashlib.md5(b"an older version").hexdigest()
    have = digests()
    out = "{0}  {1}/tank_drive.py\n{2}  {1}/pad_buttons.py".format(
        stale, checks.BRICK_DIR, have["pad_buttons.py"])
    fake_ssh(monkeypatch, [("md5sum", (True, out))])
    verdict, state, detail = checks.check_programs("robot@x")
    assert (verdict, state) == (checks.BAD, "old")
    assert "tank_drive.py" in detail


def test_an_absent_file_reports_missing(monkeypatch):
    fake_ssh(monkeypatch, [("md5sum", (True, ""))])
    verdict, state, detail = checks.check_programs("robot@x")
    assert (verdict, state) == (checks.BAD, "missing")
    assert "tank_drive.py" in detail


def test_install_verifies_the_copy_landed(monkeypatch):
    """A truncated copy that reported success would be found later.

    By a child. On the floor. So the digests are read back.
    """
    have = digests()
    good = "\n".join("{0}  {1}/{2}".format(have[n], checks.BRICK_DIR, n)
                     for n in checks.PROGRAMS)
    fake_ssh(monkeypatch, [("md5sum", (True, good))])
    ok, _ = checks.install("robot@x")
    assert ok is True

    fake_ssh(monkeypatch, [("md5sum", (True, ""))])
    ok, detail = checks.install("robot@x")
    assert ok is False
    assert "missing" in detail


def test_the_programs_it_installs_are_the_ones_that_exist():
    """Both names must resolve to real files in agent/."""
    for name in checks.PROGRAMS:
        assert (ROOT / "agent" / name).is_file(), name
        assert checks.local_digest(name) is not None


def test_the_install_target_survives_a_reboot():
    """/tmp does not, and the File Browser is where a child looks."""
    assert checks.BRICK_DIR.startswith("/home/robot")
    assert not checks.BRICK_DIR.startswith("/tmp")


# ---------------------------------------------------------------------
# The menu
# ---------------------------------------------------------------------

def wizard():
    from ev3ctl.cli.setup import Wizard
    return Wizard("robot@x")


def test_the_menu_wraps_at_both_ends():
    wiz = wizard()
    from ev3ctl.cli.setup import ITEMS
    wiz.handle("UP")
    assert wiz.selected == len(ITEMS) - 1
    wiz.handle("DOWN")
    assert wiz.selected == 0


def test_a_held_arrow_moves_once_per_key():
    """`Keyboard.read` returns a list; a held key gives several."""
    wiz = wizard()
    for key in ["DOWN", "DOWN", "DOWN"]:
        wiz.handle(key)
    assert wiz.selected == 3


def test_enter_returns_the_selected_item():
    from ev3ctl.cli.setup import ITEMS
    wiz = wizard()
    wiz.handle("DOWN")
    assert wiz.handle("ENTER") == ITEMS[1]


def test_a_number_key_jumps_straight_there():
    from ev3ctl.cli.setup import ITEMS
    wiz = wizard()
    assert wiz.handle("2") == ITEMS[1]
    assert wiz.selected == 1


def test_q_quits():
    wiz = wizard()
    wiz.handle("q")
    assert wiz.quit is True


def test_nothing_claims_to_be_ok_before_it_is_checked():
    """A green tick before anything was looked at would be a lie."""
    wiz = wizard()
    label, style = wiz.status_of("check")
    assert style == "dim"
    assert label == messages.t("status.unknown", "en")


def test_the_language_toggle_cycles_and_is_remembered(monkeypatch,
                                                      tmp_path):
    from ev3ctl.cli import setup as setup_cli
    monkeypatch.setattr(messages, "LANGUAGE_FILE",
                        str(tmp_path / "lang"))
    monkeypatch.setattr(messages, "CONFIG_DIR", str(tmp_path))
    wiz = wizard()
    assert wiz.language == "en"
    setup_cli.do_language(wiz)
    assert wiz.language == "th"
    assert wiz.say("menu.quit") == "ออกจากโปรแกรม"
    setup_cli.do_language(wiz)
    assert wiz.language == "en"


def test_every_menu_item_has_an_action_or_is_quit():
    from ev3ctl.cli.setup import ACTIONS, ITEMS
    for item in ITEMS:
        assert item == "quit" or item in ACTIONS, item


@pytest.mark.parametrize("language", messages.LANGUAGES)
def test_every_menu_row_renders_in_both_languages(language):
    from ev3ctl.cli.setup import ITEMS
    wiz = wizard()
    wiz.language = language
    for item in ITEMS:
        label = wiz.say("menu." + item)
        assert label and label != "menu." + item, (item, language)
