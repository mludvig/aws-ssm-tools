import os
import signal
import sys
import threading
import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import Mock

import pexpect
import pytest

from ssm_tools import ssm_tunnel_agent, ssm_tunnel_cli
from ssm_tools.common import exit_on_signal
from ssm_tools.ssm_tunnel_cli import SsmTunnel, parse_args
from ssm_tools.talker import PROMPT, SessionError, SsmTalker


@pytest.fixture
def tunnel(monkeypatch: pytest.MonkeyPatch) -> Iterator[SsmTunnel]:
    """An SsmTunnel without a session, its child a shell that prints one packet ('hi') and exits."""
    monkeypatch.setattr(SsmTalker, "connect", lambda *_args: None)
    old_handlers = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGHUP)}
    tunnel = SsmTunnel(Mock(), "i-1", None)
    tunnel._session_id = "s-1"
    tunnel._child = pexpect.spawn("sh", ["-c", "echo %aGk=; echo not-a-packet"], encoding="utf-8", timeout=5)
    yield tunnel
    tunnel._child.close(force=True)
    for signum, handler in old_handlers.items():
        signal.signal(signum, handler)


def run_briefly(target: Any) -> None:
    """Run target in a thread and fail if it hasn't returned within a few seconds."""
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(5)
    assert not thread.is_alive(), f"{target.__name__}() didn't return"


def test_remote_eof_stops_the_tunnel(tunnel: SsmTunnel, caplog: pytest.LogCaptureFixture) -> None:
    read_fd, tunnel._tun_fd = os.pipe()
    run_briefly(tunnel.remote_to_local)
    assert os.read(read_fd, 100) == b"hi"
    assert tunnel._exiting
    assert "tunnel went down" in caplog.text
    tunnel.stats_refresh = 0.01
    run_briefly(tunnel.display_stats)


def test_exit_after_the_session_died(tunnel: SsmTunnel) -> None:
    tunnel._child.expect(pexpect.EOF)
    tunnel._child.wait()
    tunnel.exit()  # No OSError from talking to a dead child


@pytest.mark.parametrize(
    ("traffic", "code"),
    [
        (lambda: os.kill(os.getpid(), signal.SIGTERM), 128 + signal.SIGTERM),
        (lambda: os.kill(os.getpid(), signal.SIGHUP), 128 + signal.SIGHUP),
        (lambda: None, 1),  # The session went down
    ],
)
def test_start_cleans_up(tunnel: SsmTunnel, monkeypatch: pytest.MonkeyPatch, traffic: Any, code: int) -> None:
    calls = []
    monkeypatch.setattr(tunnel, "open_remote_tunnel", lambda: None)
    monkeypatch.setattr(tunnel, "open_local_tunnel", lambda: None)
    monkeypatch.setattr(tunnel, "process_traffic", traffic)
    monkeypatch.setattr(tunnel, "exit", lambda: calls.append("exit"))
    monkeypatch.setattr(tunnel, "delete_tun", lambda: calls.append("delete_tun"))
    for signum in (signal.SIGTERM, signal.SIGHUP):  # As main() does
        signal.signal(signum, exit_on_signal)
    tunnel._exiting = code == 1
    with pytest.raises(SystemExit) as exc:
        tunnel.start("100.64.0.2", "100.64.0.3", [], "")
    assert exc.value.code == code
    assert calls == ["exit", "delete_tun"]


def respond(tunnel: SsmTunnel, output: str) -> None:
    """Make the session answer whatever we send with 'output'."""
    tunnel._child.close(force=True)
    script = f"read line; printf '%s' '{output}'; sleep 5"
    tunnel._child = pexpect.spawn("sh", ["-c", script], encoding="utf-8", echo=False, timeout=5)


def test_open_remote_tunnel(tunnel: SsmTunnel) -> None:
    respond(tunnel, "# sudo ip link set tunSSM.0.3 up\n# Agent device tunSSM.0.3 is ready [100.64.0.3]\n")
    tunnel.open_remote_tunnel()  # No error


@pytest.mark.parametrize(
    "error",
    ["bash: ssm-tunnel-agent: command not found", "sh: 1: ssm-tunnel-agent: not found"],
)
def test_open_remote_tunnel_no_agent(tunnel: SsmTunnel, error: str) -> None:
    respond(tunnel, f"{error}\n{PROMPT}")
    with pytest.raises(SessionError, match=f"not installed on i-1. Copy {ssm_tunnel_agent.__file__}"):
        tunnel.open_remote_tunnel()


def test_open_remote_tunnel_agent_failed(tunnel: SsmTunnel) -> None:
    respond(tunnel, f"ioctl(TUNSETIFF): Device or resource busy\n# ERROR: Cannot create tunSSM.0.3\n{PROMPT}")
    with pytest.raises(
        SessionError,
        match=r"ssm-tunnel-agent failed: ioctl.*busy\s+# ERROR: Cannot create tunSSM.0.3$",
    ):
        tunnel.open_remote_tunnel()


def test_silent_agent_stops_the_tunnel(
    tunnel: SsmTunnel,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keepalives hold it up, and it stops once they do."""
    monkeypatch.setattr(ssm_tunnel_cli, "agent_timeout_sec", 0.5)
    script = "for i in 1 2 3 4 5 6 7 8 9 10; do echo '#'; sleep 0.1; done; sleep 5"
    tunnel._child = pexpect.spawn("sh", ["-c", script], encoding="utf-8", timeout=0.1)
    started = time.time()
    run_briefly(tunnel.remote_to_local)
    assert time.time() - started > 1
    assert tunnel._exiting
    assert "Nothing from the agent" in caplog.text


def test_agent_exit_stops_the_tunnel(tunnel: SsmTunnel, caplog: pytest.LogCaptureFixture) -> None:
    respond(tunnel, f"Terminated\n{PROMPT}")
    tunnel._child.sendline("#")
    run_briefly(tunnel.remote_to_local)
    assert tunnel._exiting
    assert "ssm-tunnel-agent has exited" in caplog.text


def test_closing_the_session_ourselves_is_not_a_failure(tunnel: SsmTunnel, caplog: pytest.LogCaptureFixture) -> None:
    tunnel._child.close(force=True)
    tunnel._child = pexpect.spawn("sh", ["-c", f"sleep 0.5; printf '{PROMPT}'; sleep 5"], encoding="utf-8", timeout=5)
    thread = threading.Thread(target=tunnel.remote_to_local, daemon=True)
    thread.start()
    time.sleep(0.2)  # It's waiting in expect() when exit() makes the prompt come back
    tunnel._exiting = True
    thread.join(5)
    assert not thread.is_alive()
    assert "went down" not in caplog.text


def test_local_read_error_stops_the_tunnel(tunnel: SsmTunnel, caplog: pytest.LogCaptureFixture) -> None:
    tunnel._tun_fd = os.open(".", os.O_RDONLY)  # Readable, but read() fails with EISDIR
    try:
        run_briefly(tunnel.local_to_remote)
    finally:
        os.close(tunnel._tun_fd)
    assert tunnel._exiting
    assert "Tunnel failed" in caplog.text
    assert tunnel.stats["l2r"] == 0


@pytest.mark.parametrize(("failing", "deleted"), [("tuntap add", False), ("addr add", True)])
def test_create_tun_deletes_only_its_own(
    tunnel: SsmTunnel,
    monkeypatch: pytest.MonkeyPatch,
    failing: str,
    deleted: bool,
) -> None:
    commands = []

    def run_command(command: str, assert_0: bool = True) -> None:
        commands.append(command)
        assert failing not in command or not assert_0

    monkeypatch.setattr(tunnel, "run_command", run_command)
    with pytest.raises(SystemExit):
        tunnel.create_tun("tunSSM.0.2")
    assert any("tuntap del tunSSM.0.2" in command for command in commands) == deleted


@pytest.mark.parametrize(("failing", "deleted"), [("tuntap add", False), ("addr add", True)])
def test_agent_create_tun_deletes_only_its_own(monkeypatch: pytest.MonkeyPatch, failing: str, deleted: bool) -> None:
    commands = []

    def run_command(command: str, assert_0: bool = True) -> None:
        commands.append(command)
        assert failing not in command or not assert_0

    monkeypatch.setattr(ssm_tunnel_agent, "run_command", run_command)
    with pytest.raises(SystemExit):
        ssm_tunnel_agent.create_tun("tunSSM.0.3", "100.64.0.3", "100.64.0.2")
    assert any("tuntap del tunSSM.0.3" in command for command in commands) == deleted


def test_args() -> None:
    args = parse_args(["-R", "10.0.0.0/8", "--route", "10.1.0.0/16", "-r", "why", "i-1"])
    assert (args.routes, args.reason, args.INSTANCE) == (["10.0.0.0/8", "10.1.0.0/16"], "why", "i-1")


def test_ctrl_c_exits_130(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupt(_args: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(sys, "argv", ["ssm-tunnel", "i-1"])
    monkeypatch.setattr(ssm_tunnel_cli, "InstanceResolver", interrupt)
    with pytest.raises(SystemExit) as exc:
        ssm_tunnel_cli.main()
    assert exc.value.code == 130


def test_help_works_on_any_platform(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "argv", ["ssm-tunnel", "--help"])
    with pytest.raises(SystemExit) as exc:
        ssm_tunnel_cli.main()
    assert exc.value.code == 0
    assert "--route" in capsys.readouterr().out
