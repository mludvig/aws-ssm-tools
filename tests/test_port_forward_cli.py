import os
import socket
import sys
from typing import Any

import pytest

from ssm_tools import ssm_port_forward_cli
from ssm_tools.ssm_port_forward_cli import check_local_port, parse_args, start_port_forward


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
            {"portNumber": ["80"], "localPortNumber": ["80"]},
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
    assert start_port_forward(ssm, "i-1", parse_args(extra + argv)) == 0
    [(client, response, sent, sent_profile)] = calls
    assert (client, response, sent_profile) == (ssm, {"SessionId": "s-1"}, profile)
    assert sent == {"Target": "i-1", "DocumentName": document, "Parameters": parameters, **reason}
    assert ssm.calls == [sent]


@pytest.mark.parametrize(
    ("argv", "ports"),
    [
        (["--remote-port", "80"], (80, 80)),
        (["--remote-port", "65535", "-L", "0"], (65535, 0)),
        (["--remote-port", "1", "--local-port", "65535"], (1, 65535)),
    ],
)
def test_ports(argv: list[str], ports: tuple[int, int]) -> None:
    args = parse_args([*argv, "web"])
    assert (args.remote_port, args.local_port) == ports


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
    assert f"Can't listen on local port {port}" in caplog.text


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
