import os
import signal
import threading
from collections.abc import Iterator
from typing import Any

import pexpect
import pytest

from ssm_tools.ssm_tunnel_cli import SsmTunnel
from ssm_tools.talker import SsmTalker


@pytest.fixture
def tunnel(monkeypatch: pytest.MonkeyPatch) -> Iterator[SsmTunnel]:
    """An SsmTunnel without a session, its child a shell that prints one packet ('hi') and exits."""
    monkeypatch.setattr(SsmTalker, "connect", lambda *_args: None)
    old_handlers = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGHUP)}
    tunnel = SsmTunnel("i-1", profile="", region="")
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
    tunnel._exiting = code == 1
    with pytest.raises(SystemExit) as exc:
        tunnel.start("100.64.0.2", "100.64.0.3", [], "")
    assert exc.value.code == code
    assert calls == ["exit", "delete_tun"]
