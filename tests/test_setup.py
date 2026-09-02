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


def brick_listing(overrides=None):
    """What `md5sum` on the brick would print, per-program folders."""
    have = digests()
    have.update(overrides or {})
    return "\n".join(
        "{0}  {1}".format(have[n], checks.brick_path(n))
        for n in sorted(checks.PROGRAMS))


def test_matching_digests_report_ready(monkeypatch):
    out = brick_listing()
    fake_ssh(monkeypatch, [("md5sum", (True, out))])
    verdict, state, _ = checks.check_programs("robot@x")
    assert (verdict, state) == (checks.OK, "ready")


def test_a_different_digest_reports_old_not_missing(monkeypatch):
    """An old copy is subtler than a missing one: it runs.

    It behaves like the version it was rather than the version you
    have, which is a worse afternoon than a file that is not there.
    """
    stale = hashlib.md5(b"an older version").hexdigest()
    out = brick_listing({"tank_drive.py": stale})
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
    fake_ssh(monkeypatch, [("md5sum", (True, brick_listing()))])
    ok, _, _ = checks.install("robot@x")
    assert ok is True

    fake_ssh(monkeypatch, [("md5sum", (True, ""))])
    ok, detail, _ = checks.install("robot@x")
    assert ok is False
    assert "missing" in detail


def test_the_programs_it_installs_are_the_ones_that_exist():
    """Both names must resolve to real files in agent/."""
    for name in checks.PROGRAMS:
        assert (ROOT / "agent" / name).is_file(), name
        assert checks.local_digest(name) is not None


def test_the_install_targets_survive_a_reboot():
    """/tmp does not, and the File Browser is where a child looks."""
    for name, directory in checks.PROGRAMS.items():
        assert directory.startswith("/home/robot"), name
        assert not directory.startswith("/tmp"), name


def test_the_two_programs_live_in_separate_folders():
    """One is started for a session; the other runs all the time.

    Keeping pad_buttons out of tanks_1 is what makes that difference
    visible in the File Browser rather than only in a docstring.
    """
    assert checks.PROGRAMS["tank_drive.py"] != \
        checks.PROGRAMS["pad_buttons.py"]
    assert checks.PROGRAMS["pad_buttons.py"] == "/home/robot/pad_buttons"


def test_install_clears_the_place_pad_buttons_used_to_live():
    """A leftover copy is not harmless: Brickman lists and runs it."""
    assert "/home/robot/tanks_1/pad_buttons.py" in checks.OLD_LOCATIONS
    # ...and never the place it lives now.
    for name in checks.PROGRAMS:
        assert checks.brick_path(name) not in checks.OLD_LOCATIONS


def test_install_reports_what_it_tidied_away(monkeypatch):
    def _ssh(host, command, timeout=None, data=None):
        if "md5sum" in command:
            return True, brick_listing()
        if command.startswith("test -f") and "rm -f" in command:
            return True, "removed"
        return True, ""
    monkeypatch.setattr(checks, "_ssh", _ssh)
    ok, _, notes = checks.install("robot@x")
    assert ok is True
    assert "/home/robot/tanks_1/pad_buttons.py" in notes


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



# ---------------------------------------------------------------------
# Starting by itself
# ---------------------------------------------------------------------

def autostart_reply(unit="yes", enabled="enabled", linger="yes",
                    active="active"):
    return "unit={0}\nenabled={1}\nlinger={2}\nactive={3}".format(
        unit, enabled, linger, active)


@pytest.mark.parametrize("reply,expected", [
    (autostart_reply(), "on"),
    (autostart_reply(unit="no", enabled="unknown", linger="no",
                     active="unknown"), "off"),
    (autostart_reply(linger="no", enabled="disabled",
                     active="inactive"), "needs_root"),
    (autostart_reply(enabled="disabled", active="inactive"), "off"),
    (autostart_reply(active="inactive"), "off"),
])
def test_autostart_states(monkeypatch, reply, expected):
    fake_ssh(monkeypatch, [("unit=", (True, reply))])
    assert checks.check_autostart("robot@x")[1] == expected


def test_disabled_does_not_read_as_enabled(monkeypatch):
    """The bug this parser was rewritten to avoid.

    "disabled" contains "enabled" and "inactive" contains "active", so
    substring matching reported a stopped, disabled service as running.
    Every probe now labels its own answer and is compared exactly.
    """
    fake_ssh(monkeypatch, [
        ("unit=", (True, autostart_reply(enabled="disabled",
                                         active="inactive")))])
    verdict, state, _ = checks.check_autostart("robot@x")
    assert verdict == checks.BAD
    assert state == "off"


def test_lingering_is_named_separately_because_it_needs_root(monkeypatch):
    """It is the only step the operator cannot do for themselves."""
    fake_ssh(monkeypatch, [("unit=", (True, autostart_reply(
        linger="no")))])
    verdict, state, detail = checks.check_autostart("robot@x")
    assert state == "needs_root"
    assert detail == checks.LINGER_COMMAND
    assert detail.startswith("sudo ")


def test_nothing_in_the_wizard_runs_sudo():
    """The command is printed for the operator, never executed.

    CLAUDE.md forbids running sudo on the brick. Checked over the parse
    tree of both modules: the string may appear as a constant, but must
    never reach a subprocess or an ssh call.
    """
    for name in ("setup_checks.py", "cli/setup.py"):
        path = ROOT / "src" / "ev3ctl" / name
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and \
                        isinstance(arg.value, str):
                    assert "sudo " not in arg.value, (name, arg.value)


def test_the_unit_file_says_what_it_needs_to():
    unit = (ROOT / "agent" / "pad-buttons.service").read_text()
    assert "Type=simple" in unit
    assert "--foreground" in unit, "systemd needs it not to detach"
    assert "Restart=on-failure" in unit, "always would fight the toggle"
    assert "WantedBy=default.target" in unit
    assert checks.brick_path("pad_buttons.py") in unit


def test_the_program_understands_foreground():
    """The flag the unit passes has to exist in the program."""
    source = (ROOT / "agent" / "pad_buttons.py").read_text()
    assert '"--foreground" in sys.argv' in source
    tree = ast.parse(source)
    names = {n.name for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef)}
    assert "daemonise" in names


def test_the_menu_has_the_autostart_row():
    from ev3ctl.cli.setup import ACTIONS, ITEMS
    assert "autostart" in ITEMS
    assert "autostart" in ACTIONS
    for language in messages.LANGUAGES:
        assert messages.TEXT["menu.autostart"][language]


def test_the_autostart_row_shows_needs_a_grown_up():
    wiz = wizard()
    wiz.autostart = checks.BAD
    wiz.autostart_state = "needs_root"
    label, style = wiz.status_of("autostart")
    assert style == "warn"
    assert label == messages.t("status.needsroot", "en")


def _pad_buttons_module():
    """Import the agent program on the host.

    Safe because its module scope is imports and constants only - it
    touches no hardware until main() runs. That is not true of every
    program in agent/, so this helper exists rather than a shared one.
    """
    import importlib.util
    path = ROOT / "agent" / "pad_buttons.py"
    spec = importlib.util.spec_from_file_location("pad_buttons", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_proc(tmp_path, pid, cmdline):
    """Write one /proc/<pid>/cmdline, NUL-separated as the kernel does."""
    entry = tmp_path / str(pid)
    entry.mkdir()
    (entry / "cmdline").write_bytes(b"\x00".join(cmdline) + b"\x00")


def test_a_reused_pid_is_not_mistaken_for_a_running_copy(
        tmp_path, monkeypatch):
    """The reboot case, and the one that would do harm.

    The pidfile outlives a reboot and pids are reused, so a stale one
    can name a live process that is not us. Signalling that at boot
    would mean SIGTERM to a system daemon.
    """
    module = _pad_buttons_module()
    _fake_proc(tmp_path, 4242, [b"/lib/systemd/systemd-udevd"])
    monkeypatch.setattr(module, "PROC", str(tmp_path))
    monkeypatch.setattr(module, "PIDFILE", str(tmp_path / "pid"))
    monkeypatch.setattr(module.os, "kill", lambda pid, sig: None)
    (tmp_path / "pid").write_text("4242")

    assert module.running_pid() is None


def test_a_real_running_copy_is_still_found(tmp_path, monkeypatch):
    """The check must not be so strict that the toggle stops working."""
    module = _pad_buttons_module()
    _fake_proc(tmp_path, 4242, [
        b"/usr/bin/python3",
        b"/home/robot/pad_buttons/pad_buttons.py",
        b"--foreground",
    ])
    monkeypatch.setattr(module, "PROC", str(tmp_path))
    monkeypatch.setattr(module, "PIDFILE", str(tmp_path / "pid"))
    monkeypatch.setattr(module.os, "kill", lambda pid, sig: None)
    (tmp_path / "pid").write_text("4242")

    assert module.running_pid() == 4242


def test_a_dead_pid_is_not_a_running_copy(tmp_path, monkeypatch):
    module = _pad_buttons_module()

    def dead(pid, sig):
        raise OSError(3, "No such process")

    monkeypatch.setattr(module, "PROC", str(tmp_path))
    monkeypatch.setattr(module, "PIDFILE", str(tmp_path / "pid"))
    monkeypatch.setattr(module.os, "kill", dead)
    (tmp_path / "pid").write_text("4242")

    assert module.running_pid() is None


def test_no_pidfile_is_not_a_running_copy(tmp_path, monkeypatch):
    module = _pad_buttons_module()
    monkeypatch.setattr(module, "PIDFILE", str(tmp_path / "absent"))
    assert module.running_pid() is None
