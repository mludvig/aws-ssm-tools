import os
import signal
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from ssm_tools import ssm_tunnel_agent

# ssm-tunnel-agent runs on the Linux instance
if sys.platform != "linux":
    pytest.skip("ssm-tunnel-agent is Linux only", allow_module_level=True)


@pytest.fixture
def agent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[list[str]]:
    """Run the agent's main() with fake commands and tun device. Yields the commands it runs."""
    commands: list[str] = []
    monkeypatch.setattr(ssm_tunnel_agent, "run_command", lambda *command, **_kw: commands.append(" ".join(command)))
    tun_read, tun_write = os.pipe()  # Nothing to read until the agent closes it
    monkeypatch.setattr(ssm_tunnel_agent, "setup_tun", lambda _name: tun_read)
    monkeypatch.setattr(sys, "argv", ["ssm-tunnel-agent", "100.64.0.3", "100.64.0.2"])
    monkeypatch.setattr(ssm_tunnel_agent, "forwarding_was_off", False)
    monkeypatch.setattr(ssm_tunnel_agent, "IP_FORWARD", str(tmp_path / "ip_forward"))
    monkeypatch.setattr(ssm_tunnel_agent, "NET_DEVICES", str(tmp_path / "net"))
    (tmp_path / "ip_forward").write_text("1\n")
    (tmp_path / "net").mkdir()
    yield commands
    os.close(tun_write)


def stdin(monkeypatch: pytest.MonkeyPatch, closed: bool) -> None:
    read_fd, write_fd = os.pipe()
    if closed:
        os.close(write_fd)
    monkeypatch.setattr(sys, "stdin", os.fdopen(read_fd))


def test_eof_ends_the_agent(
    agent: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stdin(monkeypatch, closed=True)
    ssm_tunnel_agent.main()  # No IndexError
    ready = f"# Agent device tunSSM.0.3 is ready [100.64.0.3] version {ssm_tunnel_agent.AGENT_VERSION}\n"
    assert ready in capsys.readouterr().out
    assert agent[-3:] == [
        "sudo ip link set tunSSM.0.3 down",
        "sudo ip tuntap del tunSSM.0.3 mode tun",
        "sudo iptables -t nat -D POSTROUTING -m comment --comment tunSSM.0.3 -s 100.64.0.2 -j MASQUERADE",
    ]


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
def test_signal_ends_the_agent(agent: list[str], monkeypatch: pytest.MonkeyPatch, signum: int) -> None:
    stdin(monkeypatch, closed=False)
    threading.Timer(0.3, os.kill, (os.getpid(), signum)).start()
    with pytest.raises(SystemExit) as exc:
        ssm_tunnel_agent.main()
    assert exc.value.code == 128 + signum
    assert "sudo ip tuntap del tunSSM.0.3 mode tun" in agent


def test_another_signal_doesnt_stop_the_cleanup(agent: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """bash sends SIGHUP twice when the session goes."""
    tun_reader = ssm_tunnel_agent.tun_reader

    def reader(*args: int) -> None:
        tun_reader(*args)
        os.kill(os.getpid(), signal.SIGHUP)  # While main() waits for us

    monkeypatch.setattr(ssm_tunnel_agent, "tun_reader", reader)
    stdin(monkeypatch, closed=True)
    ssm_tunnel_agent.main()
    assert "sudo ip tuntap del tunSSM.0.3 mode tun" in agent


def test_delete_tun_finishes_after_a_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    """e.g. when create_tun() cleans up after the first one."""
    signal.signal(signal.SIGHUP, lambda signum, _frame: sys.exit(128 + signum))
    commands = []

    def run_command(*command: str, **_kwargs: bool) -> None:
        commands.append(" ".join(command))
        if "link set tunSSM.0.3 down" in commands[-1]:
            os.kill(os.getpid(), signal.SIGHUP)

    monkeypatch.setattr(ssm_tunnel_agent, "run_command", run_command)
    monkeypatch.setattr(ssm_tunnel_agent, "forwarding_was_off", False)
    ssm_tunnel_agent.delete_tun("tunSSM.0.3", "100.64.0.3", "100.64.0.2")
    assert "sudo ip tuntap del tunSSM.0.3 mode tun" in commands


@pytest.mark.parametrize(
    ("forwarding", "other_tunnel", "restored"),
    [("0", False, True), ("0", True, False), ("1", False, False)],
)
def test_ip_forwarding_is_restored(
    agent: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    forwarding: str,
    other_tunnel: bool,
    restored: bool,
) -> None:
    (tmp_path / "ip_forward").write_text(f"{forwarding}\n")
    (tmp_path / "net" / "eth0").touch()
    if other_tunnel:
        (tmp_path / "net" / "tunSSM.7.9").touch()
    stdin(monkeypatch, closed=True)
    ssm_tunnel_agent.main()
    turned_on = "sudo sysctl -q -w net.ipv4.ip_forward=1" in agent
    turned_off = "sudo sysctl -q -w net.ipv4.ip_forward=0" in agent
    assert turned_on == (forwarding == "0")
    assert turned_off == restored


def test_tun_reader_stops_on_a_read_error() -> None:
    fd = os.open(".", os.O_RDONLY)  # Readable, but read() fails with EISDIR
    stop_fd, _stop_writer = os.pipe()
    thread = threading.Thread(target=ssm_tunnel_agent.tun_reader, args=(fd, stop_fd), daemon=True)
    thread.start()
    thread.join(5)
    os.close(fd)
    assert not thread.is_alive()


def test_device_is_deleted_after_the_reader_stops(agent: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Else the device is still attached, and busy."""
    readers = []
    tun_reader = ssm_tunnel_agent.tun_reader

    def reader(*args: int) -> None:
        readers.append("running")
        tun_reader(*args)
        readers.append("stopped")

    monkeypatch.setattr(ssm_tunnel_agent, "tun_reader", reader)
    monkeypatch.setattr(ssm_tunnel_agent, "delete_tun", lambda *_args: readers.append("delete_tun"))
    stdin(monkeypatch, closed=True)
    ssm_tunnel_agent.main()
    assert readers == ["running", "stopped", "delete_tun"]


def test_run_command(capsys: pytest.CaptureFixture[str]) -> None:
    ssm_tunnel_agent.run_command("sh", "-c", 'test "$0" = "a; b"', "a; b")  # No shell in between
    ssm_tunnel_agent.run_command("false", check=False)
    with pytest.raises(subprocess.CalledProcessError):
        ssm_tunnel_agent.run_command("false")
    assert capsys.readouterr().out.startswith('# sh -c test "$0" = "a; b" a; b\n# false\n')


def test_create_tun_deletes_it_on_ctrl_c(monkeypatch: pytest.MonkeyPatch) -> None:
    commands = []

    def run_command(*command: str, **_kwargs: bool) -> None:
        commands.append(" ".join(command))
        if "link set tunSSM.0.3 up" in commands[-1]:
            raise KeyboardInterrupt

    monkeypatch.setattr(ssm_tunnel_agent, "run_command", run_command)
    with pytest.raises(KeyboardInterrupt):
        ssm_tunnel_agent.create_tun("tunSSM.0.3", "100.64.0.3", "100.64.0.2")
    assert "sudo ip tuntap del tunSSM.0.3 mode tun" in commands
