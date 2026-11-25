import io
import os
import signal
import subprocess
import sys
import types
from typing import Any

import pytest

from ssm_tools import picker
from ssm_tools.common import printable

HEADERS = "  Id  Name\n  --  ----"
TARGETS = [{"summary": "i-1  web", "Id": "i-1"}, {"summary": "i-2  db", "Id": "i-2"}]


class FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.fixture
def tty(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Make stdin and stdout terminals, stdin answering with 'typed'."""

    def _tty(typed: str = "", term: str | None = "xterm") -> None:
        monkeypatch.setattr(sys, "stdin", FakeTTY(typed))
        monkeypatch.setattr(sys, "stdout", FakeTTY())
        if term is None:
            monkeypatch.delenv("TERM", raising=False)
        else:
            monkeypatch.setenv("TERM", term)

    return _tty


@pytest.fixture
def menu(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Install a fake simple_term_menu. Its show() runs 'on_show' and returns what that returns."""
    if os.name == "nt":
        pytest.skip("simple_term_menu doesn't run on Windows")
    shown: dict[str, Any] = {}

    class TerminalMenu:
        def __init__(self, entries: list[str], title: str, **_kwargs: Any) -> None:
            shown.update(entries=entries, title=title)

        def show(self) -> int | None:
            return shown["on_show"]()  # type: ignore[no-any-return]

    monkeypatch.setitem(sys.modules, "simple_term_menu", types.SimpleNamespace(TerminalMenu=TerminalMenu))
    monkeypatch.setattr(picker.shutil, "get_terminal_size", lambda: os.terminal_size((13, 24)))
    return shown


@pytest.mark.parametrize("stdin_tty", [True, False])
def test_no_terminal_exits_2(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    stdin_tty: bool,
) -> None:
    monkeypatch.setattr(sys, "stdin", FakeTTY() if stdin_tty else io.StringIO())
    with pytest.raises(SystemExit) as exc:
        picker.target_selector(HEADERS, TARGETS)
    assert exc.value.code == 2
    assert "No target given" in caplog.text


def test_menu_selection(tty: Any, menu: Any, capsys: pytest.CaptureFixture) -> None:
    tty()
    menu["on_show"] = lambda: 1
    assert picker.target_selector(HEADERS, TARGETS)["Id"] == "i-2"
    out, err = capsys.readouterr()
    assert out == ""
    assert sys.stdout.getvalue() == ""  # type: ignore[attr-defined]
    assert err == f"{HEADERS}\n  i-2  db\n"


def test_menu_cancel_exits_130(tty: Any, menu: Any, caplog: pytest.LogCaptureFixture) -> None:
    tty()
    menu["on_show"] = lambda: None
    with pytest.raises(SystemExit) as exc:
        picker.target_selector(HEADERS, TARGETS)
    assert exc.value.code == 130
    assert "No target selected" in caplog.text


def test_menu_entries_fit_the_terminal(tty: Any, menu: Any) -> None:
    tty()
    menu["on_show"] = lambda: 0
    targets = [{"summary": "i-1  名前🚀abcdef"}, {"summary": "i-2  evil\x1b[2J|x"}]
    picker.target_selector(HEADERS, targets)
    # 13 columns, less 3 for the cursor and the last column
    assert menu["entries"] == ["i-1  名前", "i-2  evil?"]
    assert menu["title"] == "  Id  Name\n  --  ----"
    targets = [{"summary": "a|b"}]
    picker.target_selector(HEADERS, targets)
    assert menu["entries"] == ["a\\|b"]


def test_menu_signals(tty: Any, menu: Any) -> None:
    tty()
    before = {s: signal.getsignal(s) for s in (signal.SIGTSTP, signal.SIGQUIT, signal.SIGTERM, signal.SIGHUP)}

    def on_show() -> None:
        assert signal.getsignal(signal.SIGTSTP) == signal.SIG_IGN
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)

    menu["on_show"] = on_show
    with pytest.raises(SystemExit) as exc:
        picker.target_selector(HEADERS, TARGETS)
    assert exc.value.code == 128 + signal.SIGTERM
    assert {s: signal.getsignal(s) for s in before} == before


@pytest.mark.parametrize(
    "error",
    [ImportError(), NotImplementedError(), OSError(6, "No such device"), subprocess.CalledProcessError(3, "tput")],
)
def test_menu_failure_falls_back_to_numbers(
    tty: Any,
    menu: Any,
    capsys: pytest.CaptureFixture,
    error: Exception,
) -> None:
    tty("2\n")

    def on_show() -> None:
        raise error

    menu["on_show"] = on_show
    assert picker.target_selector(HEADERS, TARGETS)["Id"] == "i-2"
    assert "1)  i-1  web" in capsys.readouterr().err


@pytest.mark.parametrize("term", [None, "dumb"])
def test_numbered_list_without_term(tty: Any, menu: Any, capsys: pytest.CaptureFixture, term: str | None) -> None:
    tty("x\n0\n3\n1\n", term=term)
    menu["on_show"] = lambda: pytest.fail("the menu needs a TERM")
    assert picker.target_selector(HEADERS, TARGETS)["Id"] == "i-1"
    err = capsys.readouterr().err
    assert err.startswith("    Id  Name\n    --  ----\n1)  i-1  web\n2)  i-2  db\n")
    assert err.count("Select 1-2, or press Enter to quit: ") == 4


@pytest.mark.parametrize("typed", ["\n", ""])
def test_numbered_list_cancel(tty: Any, typed: str) -> None:
    tty(typed, term="dumb")
    with pytest.raises(SystemExit) as exc:
        picker.target_selector(HEADERS, TARGETS)
    assert exc.value.code == 130


def test_printable() -> None:
    assert printable("a\x1b[31m\x07\x9bb|ü名") == "a?[31m??b|ü名"
