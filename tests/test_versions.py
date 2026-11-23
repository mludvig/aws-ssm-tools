import importlib
import logging
import subprocess
from typing import Any

import pytest

from ssm_tools import common, ssm_ssh_cli
from ssm_tools.common import verify_awscli_version, verify_plugin_version


def assert_logged(caplog: pytest.LogCaptureFixture, message: str) -> None:
    """Assert that 'message' was logged, or with an empty 'message' that nothing was warned about."""
    if message:
        assert message in caplog.text
    else:
        assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


@pytest.fixture
def version_output(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Make 'command --version' print the given text."""

    def _set(output: str) -> None:
        def _run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess(command, 0, stdout=output.encode())

        monkeypatch.setattr(common.subprocess, "run", _run)

    return _set


@pytest.mark.parametrize(
    ("output", "kwargs", "ok", "message"),
    [
        ("1.2.896.0\n", {"interactive": True}, True, ""),
        ("1.2.707.0\n", {"interactive": True}, True, "can't end a stuck session with 'Enter ~.'"),
        ("1.2.707.0\n", {}, True, ""),
        ("1.1.22.0\n", {"required": "1.1.23"}, False, "1.1.23 or newer is required"),
        ("1.1.23.0\n", {"required": "1.1.23"}, True, ""),
        ("", {"required": "1.1.23"}, True, "Can't parse the version"),
        ("garbage\xff", {"interactive": True}, True, "Can't parse the version"),
    ],
)
def test_plugin_version(
    version_output: Any,
    caplog: pytest.LogCaptureFixture,
    output: str,
    kwargs: dict[str, Any],
    ok: bool,
    message: str,
) -> None:
    version_output(output)
    assert verify_plugin_version(logging.getLogger("test"), **kwargs) is ok
    assert_logged(caplog, message)


@pytest.mark.parametrize(
    ("output", "ok", "message"),
    [
        ("aws-cli/2.15.0 Python/3.11.6 Linux/6.5.0 exe/x86_64.ubuntu.22\n", True, ""),
        ("aws-cli/2.11.0 Python/3.11.6 Linux/6.5.0\n", False, "2.11.0 is installed, 2.12.0 is required"),
        ("something else\n", True, "Can't parse the version"),
    ],
)
def test_awscli_version(
    version_output: Any,
    caplog: pytest.LogCaptureFixture,
    output: str,
    ok: bool,
    message: str,
) -> None:
    version_output(output)
    assert verify_awscli_version("2.12.0", logging.getLogger("test")) is ok
    assert_logged(caplog, message)


def test_not_installed(monkeypatch: pytest.MonkeyPatch, tmp_path: Any, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    logger = logging.getLogger("test")
    assert verify_plugin_version(logger) is False
    assert "session-manager-plugin is not installed" in caplog.text
    assert verify_awscli_version("2.12.0", logger) is False
    assert "AWS-CLI is not installed" in caplog.text


class FakeResolver:
    def __init__(self, _args: Any) -> None:
        pass

    def print_list(self) -> None:
        pass

    def resolve_instance(self, target: str) -> tuple[str, dict[str, Any]]:
        return target, {}


def no_plugin_check(*_args: Any, **_kwargs: Any) -> bool:
    raise AssertionError("session-manager-plugin version checked")


@pytest.mark.parametrize(
    ("module", "resolver"),
    [
        ("ssm_session_cli", "InstanceResolver"),
        ("ecs_session_cli", "ContainerResolver"),
        ("ssm_port_forward_cli", "InstanceResolver"),
        ("ssm_ssh_cli", "InstanceResolver"),
    ],
)
def test_list_skips_plugin_check(monkeypatch: pytest.MonkeyPatch, module: str, resolver: str) -> None:
    cli = importlib.import_module(f"ssm_tools.{module}")
    monkeypatch.setattr(cli, resolver, FakeResolver)
    monkeypatch.setattr(cli, "verify_plugin_version", no_plugin_check)
    monkeypatch.setattr("sys.argv", [module, "--list"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0


def test_ssh_use_endpoint_skips_plugin_check(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(ssm_ssh_cli, "InstanceResolver", FakeResolver)
    monkeypatch.setattr(ssm_ssh_cli, "verify_plugin_version", no_plugin_check)
    monkeypatch.setattr(ssm_ssh_cli, "verify_awscli_version", lambda *_args: True)
    monkeypatch.setattr(ssm_ssh_cli, "ssh_command", lambda *args: calls.append(args) or ["true"])
    monkeypatch.setattr(ssm_ssh_cli.os, "execvp", lambda *_args: None)
    monkeypatch.setattr("sys.argv", ["ec2-ssh", "--use-endpoint", "--no-send-key", "i-0123456789abcdef0"])
    ssm_ssh_cli.main()
    assert calls[0][3] is True  # use_endpoint


def test_ssh_use_endpoint_old_awscli_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssm_ssh_cli, "InstanceResolver", FakeResolver)
    monkeypatch.setattr(ssm_ssh_cli, "verify_awscli_version", lambda *_args: False)
    monkeypatch.setattr(ssm_ssh_cli, "verify_plugin_version", no_plugin_check)  # i.e. no SSM fallback
    monkeypatch.setattr("sys.argv", ["ec2-ssh", "--use-endpoint", "i-0123456789abcdef0"])
    with pytest.raises(SystemExit) as exc:
        ssm_ssh_cli.main()
    assert exc.value.code == 1
