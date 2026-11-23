import json
import os
import pathlib
from collections.abc import Callable
from typing import Any

import boto3
import pytest
from botocore.stub import Stubber

from ssm_tools import plugin
from ssm_tools.plugin import RESPONSE_ENV, run_plugin

RESPONSE = {"SessionId": "s-1", "TokenValue": "secret", "StreamUrl": "wss://ssm/1", "ResponseMetadata": {}}
PARAMETERS = {"Target": "i-0123456789abcdef0", "DocumentName": "AWS-StartPortForwardingSession"}


@pytest.fixture
def ssm() -> Any:
    return boto3.session.Session().client("ssm")


@pytest.mark.skipif(os.name != "posix", reason="fake plugin is a shell script")
@pytest.mark.parametrize("code", [0, 3])
def test_runs_the_plugin(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    ssm: Any,
    stub: Callable[[Any], Stubber],
    code: int,
) -> None:
    fake = tmp_path / "session-manager-plugin"
    fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "$KEPT" "$@" "${RESPONSE_ENV}" > "{tmp_path}/out"\nexit {code}\n')
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path), prepend=os.pathsep)
    monkeypatch.setenv("KEPT", "our environment")
    if code:  # It went wrong, so we end the session
        stub(ssm).add_response("terminate_session", {"SessionId": "s-1"}, {"SessionId": "s-1"})
    else:  # pytest.fail() isn't an Exception, so run_plugin() can't swallow it
        monkeypatch.setattr(ssm, "terminate_session", lambda **_kwargs: pytest.fail("terminated a clean session"))

    assert run_plugin(ssm, RESPONSE, PARAMETERS, "lab profile") == code
    kept, *argv, session = (tmp_path / "out").read_text().splitlines()
    assert kept == "our environment"
    assert argv == [
        RESPONSE_ENV,  # Not the token itself
        "ap-southeast-2",
        "StartSession",
        "lab profile",
        json.dumps(PARAMETERS),
        "https://ssm.ap-southeast-2.amazonaws.com",
    ]
    assert json.loads(session) == {"SessionId": "s-1", "TokenValue": "secret", "StreamUrl": "wss://ssm/1"}


def test_no_plugin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    ssm: Any,
    stub: Callable[[Any], Stubber],
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    stub(ssm).add_response("terminate_session", {"SessionId": "s-1"}, {"SessionId": "s-1"})
    assert run_plugin(ssm, RESPONSE, PARAMETERS, None) == 127


def test_interrupted(monkeypatch: pytest.MonkeyPatch, ssm: Any, stub: Callable[[Any], Stubber]) -> None:
    def run_session(*_args: Any, **_kwargs: Any) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(plugin, "run_session", run_session)
    stub(ssm).add_response("terminate_session", {"SessionId": "s-1"}, {"SessionId": "s-1"})
    with pytest.raises(KeyboardInterrupt):
        run_plugin(ssm, RESPONSE, PARAMETERS, None)


@pytest.mark.parametrize(("error", "warned"), [("ValidationException", False), ("AccessDeniedException", True)])
def test_terminate_fails(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    ssm: Any,
    stub: Callable[[Any], Stubber],
    error: str,
    warned: bool,
) -> None:
    monkeypatch.setattr(plugin, "run_session", lambda *_args, **_kwargs: 255)
    stub(ssm).add_client_error("terminate_session", error)
    assert run_plugin(ssm, RESPONSE, PARAMETERS, None) == 255  # The plugin's code, not an exception
    assert ("may still be active" in caplog.text) is warned


def test_terminate_interrupted(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, ssm: Any) -> None:
    def terminate_session(**_kwargs: Any) -> None:
        raise KeyboardInterrupt  # e.g. at an MFA prompt for expired credentials

    monkeypatch.setattr(plugin, "run_session", lambda *_args, **_kwargs: 130)
    monkeypatch.setattr(ssm, "terminate_session", terminate_session)
    assert run_plugin(ssm, RESPONSE, PARAMETERS, None) == 130
    assert "may still be active" in caplog.text
