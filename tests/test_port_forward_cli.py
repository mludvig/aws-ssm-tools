import os
import re
import socket
import sys
import threading
import time
from typing import Any

import pytest

from ssm_tools import ssm_port_forward_cli
from ssm_tools.ssm_port_forward_cli import check_local_port, parse_args, start_port_forward


class FakeResolver:
    ssm_client = None

    def resolve_instance(self, instance: str) -> tuple[str, dict[str, Any]]:
        return instance, {}


class FakeSSM:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def start_session(self, **parameters: Any) -> dict[str, Any]:
        self.calls.append(parameters)
        return {"SessionId": "s-1"}


@pytest.mark.parametrize(
    ("argv", "document", "parameters"),
    [
        (
            ["--remote-port", "80", "web"],
            "AWS-StartPortForwardingSession",
            {"portNumber": ["80"], "localPortNumber": ["0"]},
        ),
        (
            ["-L", "8080:localhost:80", "web"],
            "AWS-StartPortForwardingSession",
            {"portNumber": ["80"], "localPortNumber": ["8080"]},
        ),
        (
            ["--remote", "[fd00::1]:5432", "web"],
            "AWS-StartPortForwardingSessionToRemoteHost",
            {"portNumber": ["5432"], "localPortNumber": ["0"], "host": ["fd00::1"]},
        ),
        (
            ["--remote-port", "5432", "--local-port", "15432", "-H", "db.internal", "web"],
            "AWS-StartPortForwardingSessionToRemoteHost",
            {"portNumber": ["5432"], "localPortNumber": ["15432"], "host": ["db.internal"]},
        ),
    ],
)
@pytest.mark.parametrize(
    ("extra", "reason", "profile"),
    [([], {}, None), (["--reason", "why", "--profile", "lab"], {"Reason": "why"}, "lab")],
)
def test_parameters(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    document: str,
    parameters: dict[str, Any],
    extra: list[str],
    reason: dict[str, str],
    profile: str | None,
) -> None:
    calls = []
    ssm = FakeSSM()
    monkeypatch.setattr(ssm_port_forward_cli, "run_plugin", lambda *args: calls.append(args) or 0)
    args = parse_args(extra + argv)
    assert start_port_forward(ssm, "i-1", args, args.local_port) == 0
    [(client, response, sent, sent_profile)] = calls
    assert (client, response, sent_profile) == (ssm, {"SessionId": "s-1"}, profile)
    assert sent == {"Target": "i-1", "DocumentName": document, "Parameters": parameters, **reason}
    assert ssm.calls == [sent]


@pytest.mark.parametrize(
    ("argv", "forward"),
    [
        (["--remote-port", "80"], ("127.0.0.1", 0, None, 80)),  # Any free local port
        (["--remote-port", "65535", "-L", "13389"], ("127.0.0.1", 13389, None, 65535)),  # -L PORT as before
        (["--remote-port", "1", "--local-port", "65535"], ("127.0.0.1", 65535, None, 1)),
        (["--remote", "5432"], ("127.0.0.1", 0, None, 5432)),
        (["--remote", "db:5432", "--local", "15432"], ("127.0.0.1", 15432, "db", 5432)),
        (["--remote", "localhost:22"], ("127.0.0.1", 0, None, 22)),  # The instance itself
        (["--remote-port", "22", "-H", "127.0.0.1"], ("127.0.0.1", 0, None, 22)),
        (["--remote", "db:5432", "--local", "0.0.0.0:0"], ("0.0.0.0", 0, "db", 5432)),
        (["--remote", "db:5432", "--local-host", "::1"], ("::1", 0, "db", 5432)),
        (["--remote", "db:5432", "--local", "[::]:5432"], ("::", 5432, "db", 5432)),
        (["-L", "8080:db:80"], ("127.0.0.1", 8080, "db", 80)),
        (["-L", "10.0.0.5:8080:db:80"], ("10.0.0.5", 8080, "db", 80)),
        (["-L", "*:8080:db:80"], ("0.0.0.0", 8080, "db", 80)),
        (["-L", ":8080:db:80"], ("0.0.0.0", 8080, "db", 80)),
        (["-L", "[::1]:0:[fd00::1]:80"], ("::1", 0, "fd00::1", 80)),
    ],
)
def test_forward(argv: list[str], forward: tuple[str, int, str | None, int]) -> None:
    args = parse_args([*argv, "web"])
    assert (args.local_host, args.local_port, args.remote_host, args.remote_port) == forward


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        ([], "a remote port is required"),
        (["--remote", "db:80", "--remote-port", "80"], "--remote-port and --remote both set the remote port"),
        (["--remote", "db:80", "-H", "db"], "--remote-host and --remote both set the remote host"),
        (["-L", "8080:db:80", "--remote-port", "80"], "--remote-port and -L both set the remote port"),
        (["-L", "1.2.3.4:8080:db:80", "--local-host", "::"], "--local-host and -L both set the local host"),
        (["--local", "8080", "--local-port", "8080", "--remote", "80"], "--local-port and --local both set"),
        (["--remote", "db:"], "db: isn't [HOST:]PORT"),
        (["--remote", ":80"], ":80 isn't [HOST:]PORT"),
        (["--remote", "fd00::1:80"], "fd00::1:80 isn't [HOST:]PORT"),
        (["--remote", "db:0"], "0 isn't a port number from 1 to 65535"),
        (["--remote", "80", "--local", "x:-1"], "-1 isn't a port number from 0 to 65535"),
        (["-L", "8080:db"], "8080:db isn't PORT or [BIND:]PORT:HOST:HOSTPORT"),
        (["-L", "8080::80"], "8080::80 isn't PORT or [BIND:]PORT:HOST:HOSTPORT"),
        (["-L", "8080:db:0"], "0 isn't a port number from 1 to 65535"),
    ],
)
def test_bad_forward(argv: list[str], message: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        parse_args([*argv, "web"])
    assert exc.value.code == 2
    assert message in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["--remote-port", "0"],
        ["--remote-port", "65536"],
        ["--remote-port", "abc"],
        ["--remote-port", "80,localPortNumber=1"],
        ["--remote-port", "80", "-L", "-1"],
        ["--remote-port", "80", "-L", "70000"],
    ],
)
def test_invalid_ports(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        parse_args([*argv, "web"])
    assert exc.value.code == 2
    assert "isn't a port number" in capsys.readouterr().err


def test_local_port_in_use(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Else the plugin waits silently."""
    monkeypatch.setattr(ssm_port_forward_cli, "InstanceResolver", lambda _args: None)
    monkeypatch.setattr(ssm_port_forward_cli, "verify_plugin_version", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ssm_port_forward_cli, "start_port_forward", lambda *_args: pytest.fail("Started a session"))
    with socket.create_server(("127.0.0.1", 0)) as listener:
        port = listener.getsockname()[1]
        monkeypatch.setattr(sys, "argv", ["ssm-port-forward", "--remote-port", "80", "-L", str(port), "i-1"])
        with pytest.raises(SystemExit) as exc:
            ssm_port_forward_cli.main()
    assert exc.value.code == 1
    assert f"Can't listen on 127.0.0.1 port {port}" in caplog.text


@pytest.mark.skipif(os.name == "nt", reason="POSIX only")
def test_local_port_after_a_closed_connection() -> None:
    """Its TIME_WAIT doesn't stop the plugin listening, so it mustn't stop us."""
    with socket.create_server(("127.0.0.1", 0)) as listener:
        port = listener.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port)) as client:
            server, _ = listener.accept()
            server.close()  # The side that closes first waits in TIME_WAIT
            client.recv(1)
    check_local_port(port)
    check_local_port(0)


def echo_server(port: int) -> None:
    """Echo one connection on 127.0.0.1:port, like the plugin's forwarded port would."""

    def serve() -> None:
        with socket.create_server(("127.0.0.1", port)) as server:
            conn, _ = server.accept()
            with conn:
                while data := conn.recv(1024):
                    conn.sendall(data)

    threading.Thread(target=serve, daemon=True).start()


def test_relay_for_other_local_hosts(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """The plugin only listens on 127.0.0.1, so other addresses need a relay. See docs/adr/0022-port-forward-options.md"""
    monkeypatch.setattr(ssm_port_forward_cli, "InstanceResolver", lambda _args: FakeResolver())
    monkeypatch.setattr(ssm_port_forward_cli, "verify_plugin_version", lambda *_args, **_kwargs: True)
    replies = []

    def session(_ssm: Any, instance_id: str, _args: Any, plugin_port: int) -> int:
        [relay_port] = re.findall(r"Listening on 0\.0\.0\.0 port (\d+)", caplog.text)
        assert plugin_port not in (0, int(relay_port))
        with socket.create_connection(("127.0.0.1", int(relay_port))) as client:
            time.sleep(0.5)  # The plugin starts listening a moment after the session
            echo_server(plugin_port)
            client.sendall(b"hello")
            client.shutdown(socket.SHUT_WR)
            replies.append(client.makefile("rb").read())
        return 0

    monkeypatch.setattr(ssm_port_forward_cli, "start_port_forward", session)
    monkeypatch.setattr(sys, "argv", ["ssm-port-forward", "--remote", "80", "--local", "0.0.0.0:0", "i-1"])
    threads = threading.active_count()
    assert ssm_port_forward_cli.main() == 0
    for _ in range(30):  # The relay stops once its listener is closed
        if threading.active_count() == threads:
            break
        time.sleep(0.1)
    assert threading.active_count() == threads
    assert replies == [b"hello"]
    assert "reachable from the network" in caplog.text


def test_relay_port_in_use(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(ssm_port_forward_cli, "InstanceResolver", lambda _args: None)
    monkeypatch.setattr(ssm_port_forward_cli, "verify_plugin_version", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ssm_port_forward_cli, "start_port_forward", lambda *_args: pytest.fail("Started a session"))
    with socket.create_server(("0.0.0.0", 0)) as listener:
        port = listener.getsockname()[1]
        monkeypatch.setattr(sys, "argv", ["ssm-port-forward", "-L", f"*:{port}:db:80", "i-1"])
        with pytest.raises(SystemExit) as exc:
            ssm_port_forward_cli.main()
    assert exc.value.code == 1
    assert f"Can't listen on 0.0.0.0 port {port}" in caplog.text
