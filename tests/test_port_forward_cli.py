from typing import Any

import pytest

from ssm_tools import ssm_port_forward_cli
from ssm_tools.ssm_port_forward_cli import parse_args, start_port_forward


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
