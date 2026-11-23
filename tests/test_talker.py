from typing import Any
from unittest.mock import Mock

import pexpect
import pytest

from ssm_tools import talker
from ssm_tools.talker import SessionError, SsmTalker


def connect(monkeypatch: pytest.MonkeyPatch, script: str, reason: str | None = None) -> tuple[SsmTalker, Mock]:
    """Connect with the plugin replaced by 'sh -c script'."""
    ssm = Mock()
    ssm.start_session.return_value = {"SessionId": "s-1"}

    def plugin_command(*_args: Any) -> tuple[list[str], dict[str, str]]:
        return ["sh", "-c", script], {}

    monkeypatch.setattr(talker, "plugin_command", plugin_command)
    return SsmTalker(ssm, "i-1", None, reason), ssm


@pytest.mark.parametrize("prompt", ["sh-5.2$ ", "[root@ip-10-0-0-1 /]# ", "host% ", "> "])
def test_connect_sets_our_prompt(monkeypatch: pytest.MonkeyPatch, prompt: str) -> None:
    # Echo on, as on a real instance, until we turn it off. Like the plugin, exit 0 whatever the shell did.
    session, ssm = connect(monkeypatch, f"stty echo; PS1='{prompt}' sh -i; exit 0", reason="why")
    ssm.start_session.assert_called_once_with(Target="i-1", Reason="why")
    session._child.sendline("echo $((6*7))")
    session.wait_for_prompt()
    assert session.output() == "42"  # Not lined up with the echo of setting PS1
    session.exit()
    ssm.terminate_session.assert_not_called()  # Its exit 0 means the session has ended


def test_connect_fails_when_the_session_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SessionError, match="the session ended. Got: 'Cannot perform start session: EOF'"):
        connect(monkeypatch, "echo Cannot perform start session: EOF; exit 1")


def test_ctrl_c_while_connecting_closes_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupt(_self: SsmTalker) -> None:
        raise KeyboardInterrupt

    exits = []
    monkeypatch.setattr(SsmTalker, "wait_for_prompt", interrupt)
    monkeypatch.setattr(SsmTalker, "exit", lambda self: exits.append(self._session_id))
    with pytest.raises(KeyboardInterrupt):
        connect(monkeypatch, "PS1='$ ' sh -i")
    assert exits == ["s-1"]


def test_connect_fails_when_the_plugin_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    ssm = Mock()
    ssm.start_session.return_value = {"SessionId": "s-1"}
    monkeypatch.setattr(talker, "plugin_command", lambda *_args: (["no-such-plugin"], {}))
    with pytest.raises(SessionError, match="Can't run no-such-plugin"):
        SsmTalker(ssm, "i-1", None)
    ssm.terminate_session.assert_called_once_with(SessionId="s-1")


def test_expect_timeout() -> None:
    session = SsmTalker.__new__(SsmTalker)
    session._child = pexpect.spawn("sh", ["-c", "echo partial; sleep 5"], encoding="utf-8", timeout=0.5)
    with pytest.raises(SessionError, match="Waiting for the agent, timed out. Got: 'partial'"):
        session.expect(["never"], "the agent")
    session._child.close(force=True)


def test_output_drops_terminal_controls() -> None:
    session = SsmTalker.__new__(SsmTalker)
    session._child = Mock(before="\x1b[?2004l\r\r\nioctl: busy\r\r\n\x1b]0;title\x07# ERROR\x1b[?2004h\r\n")
    assert session.output() == "ioctl: busy\n]0;title# ERROR"


def test_exit_terminates_after_another_ctrl_c(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupt(_char: str) -> None:
        raise KeyboardInterrupt

    session = SsmTalker.__new__(SsmTalker)
    session._ssm_client, session._session_id = Mock(), "s-1"
    session._child = pexpect.spawn("sleep", ["5"], encoding="utf-8", timeout=5)
    monkeypatch.setattr(session._child, "sendcontrol", interrupt)
    with pytest.raises(KeyboardInterrupt):
        session.exit()
    assert not session._child.isalive()
    session._ssm_client.terminate_session.assert_called_once_with(SessionId="s-1")


def test_exit_terminates_a_failed_session() -> None:
    session = SsmTalker.__new__(SsmTalker)
    session._ssm_client, session._session_id = Mock(), "s-1"
    session._child = pexpect.spawn("sh", ["-c", "exit 3"], encoding="utf-8", timeout=5)
    session.exit()
    session._ssm_client.terminate_session.assert_called_once_with(SessionId="s-1")
