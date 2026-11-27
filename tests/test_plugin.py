import json
import os
import pathlib
from collections.abc import Callable
from typing import Any

import boto3
import botocore.exceptions
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


@pytest.mark.skipif(os.name != "posix", reason="fake plugin is a shell script")
def test_windows_ctrl_c(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, ssm: Any) -> None:
    """The console sends Ctrl-C to the plugin too, which ends the session itself. See docs/adr/0019-windows-and-macos.md"""
    fake = tmp_path / "session-manager-plugin"
    fake.write_text("#!/bin/sh\nsleep 0.5; kill -INT $PPID; sleep 0.5; exit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path), prepend=os.pathsep)
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(ssm, "terminate_session", lambda **_kwargs: pytest.fail("terminated a clean session"))
    assert run_plugin(ssm, RESPONSE, PARAMETERS, None) == 0


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


class FakeSSM:
    """get_connection_status() answers with 'results' in turn, then stops the watchdog"""

    def __init__(self, *results: str | Exception) -> None:
        self.results = list(results)
        self.calls = 0
        self.watchdog: plugin.Watchdog | None = None

    def get_connection_status(self, Target: str) -> dict[str, str]:  # noqa: N803
        assert Target == "i-0123456789abcdef0"
        self.calls += 1
        if not self.results:
            assert self.watchdog
            self.watchdog.stop()
            return {"Status": "connected"}
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return {"Status": result}


class FakePlugin:
    def __init__(self, exits_on_term: bool, watchdog: plugin.Watchdog) -> None:
        self.signals: list[str] = []
        self.exits_on_term = exits_on_term
        self.watchdog = watchdog

    def terminate(self) -> None:
        self.signals.append("TERM")
        if self.exits_on_term:
            self.watchdog.stop()

    def kill(self) -> None:
        self.signals.append("KILL")


def watched(ssm: FakeSSM, exits_on_term: bool = True) -> tuple[plugin.Watchdog, Any]:
    watchdog = plugin.Watchdog(ssm, "i-0123456789abcdef0", None)
    ssm.watchdog = watchdog
    return watchdog, FakePlugin(exits_on_term, watchdog)


@pytest.fixture
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin, "WATCH_INTERVAL", 0)
    monkeypatch.setattr(plugin, "KILL_AFTER", 0)


@pytest.mark.usefixtures("no_wait")
@pytest.mark.parametrize(("exits_on_term", "signals"), [(True, ["TERM"]), (False, ["TERM", "KILL"])])
def test_watchdog_ends_the_plugin(caplog: pytest.LogCaptureFixture, exits_on_term: bool, signals: list[str]) -> None:
    ssm = FakeSSM("connected", "notconnected", "connected", "notconnected", "notconnected")
    watchdog, proc = watched(ssm, exits_on_term)
    watchdog._run(proc)
    assert (ssm.calls, proc.signals, watchdog.fired) == (5, signals, True)  # Only two in a row
    assert "i-0123456789abcdef0 is no longer connected to SSM (notconnected), ending the session" in caplog.text


@pytest.mark.usefixtures("no_wait")
def test_watchdog_retries_our_errors() -> None:
    error = botocore.exceptions.EndpointConnectionError(endpoint_url="https://ssm")
    ssm = FakeSSM("notconnected", error, "notconnected")
    watchdog, proc = watched(ssm)
    watchdog._run(proc)
    assert (ssm.calls, proc.signals) == (3, ["TERM"])


@pytest.mark.usefixtures("no_wait")
def test_watchdog_gives_up_on_client_errors() -> None:
    error = botocore.exceptions.ClientError({"Error": {"Code": "AccessDeniedException"}}, "GetConnectionStatus")
    ssm = FakeSSM(error, "notconnected")
    watchdog, proc = watched(ssm)
    watchdog._run(proc)
    assert (ssm.calls, proc.signals, watchdog.fired) == (1, [], False)


@pytest.mark.usefixtures("no_wait")
def test_watchdog_stops_with_the_plugin() -> None:
    ssm = FakeSSM("connected")
    watchdog, proc = watched(ssm)
    watchdog._run(proc)
    assert (ssm.calls, proc.signals, watchdog.fired) == (2, [], False)


def test_no_watchdog_with_mfa(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = "[profile mfa]\nmfa_serial = arn:aws:iam::123456789012:mfa/me\n[profile plain]\n"
    (tmp_path / "aws-config").write_text(config)
    started: list[str] = []
    monkeypatch.setattr(plugin.threading.Thread, "start", lambda thread: started.append(thread.name))
    proc: Any = None
    plugin.Watchdog(FakeSSM(), "i-0123456789abcdef0", "mfa").watch(proc)
    assert not started
    plugin.Watchdog(FakeSSM(), "i-0123456789abcdef0", "plain").watch(proc)
    assert len(started) == 1


@pytest.mark.skipif(os.name != "posix", reason="fake plugin is a shell script")
@pytest.mark.parametrize(("script", "code"), [("exec sleep 30", 143), ("trap '' TERM; exec sleep 30", 137)])
def test_watchdog_in_run_plugin(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    ssm: Any,
    stub: Callable[[Any], Stubber],
    script: str,
    code: int,
) -> None:
    fake = tmp_path / "session-manager-plugin"
    fake.write_text(f"#!/bin/sh\n{script}\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path), prepend=os.pathsep)
    monkeypatch.setattr(plugin, "WATCH_INTERVAL", 0.1)
    monkeypatch.setattr(plugin, "KILL_AFTER", 0.5)
    target = {"Target": PARAMETERS["Target"]}
    stubber = stub(ssm)
    for _ in range(plugin.NOT_CONNECTED_LIMIT):
        stubber.add_response("get_connection_status", {**target, "Status": "notconnected"}, target)
    stubber.add_response("terminate_session", {"SessionId": "s-1"}, {"SessionId": "s-1"})
    assert run_plugin(ssm, RESPONSE, PARAMETERS, None) == code
