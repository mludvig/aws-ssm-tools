import importlib
import pkgutil
import signal
import subprocess
import sys

import pytest

import ssm_tools
from ssm_tools import ecs_session_cli

ALL_MODULES = sorted(m.name for m in pkgutil.iter_modules(ssm_tools.__path__))
CLI_MODULES = ["ecs_session_cli", "ssm_port_forward_cli", "ssm_session_cli", "ssm_ssh_cli", "ssm_tunnel_cli"]
PROGS = {
    "ecs_session_cli": "ecs-session",
    "ssm_port_forward_cli": "ssm-port-forward",
    "ssm_session_cli": "ec2-session",
    "ssm_ssh_cli": "ec2-ssh",
    "ssm_tunnel_cli": "ssm-tunnel",
}


@pytest.mark.parametrize("name", ALL_MODULES)
def test_import(name: str) -> None:
    importlib.import_module(f"ssm_tools.{name}")


@pytest.mark.parametrize("name", CLI_MODULES)
def test_help(name: str, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module(f"ssm_tools.{name}")
    monkeypatch.setattr(sys, "argv", ["C:\\Python314\\Scripts\\ssm-ssh"])  # Not what we call ourselves
    with pytest.raises(SystemExit) as exc:
        module.parse_args(["--help"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith(f"usage: {PROGS[name]} ")


@pytest.mark.parametrize(
    ("name", "argv"),
    [
        ("ssm_session_cli", ["nosuch"]),
        ("ssm_port_forward_cli", ["nosuch", "--remote-port", "80", "--local-port", "0"]),
        ("ssm_tunnel_cli", ["nosuch"]),
    ],
)
def test_instance_not_found(
    name: str,
    argv: list[str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Resolver:
        def __init__(self, _args: object) -> None:
            pass

        def resolve_instance(self, _instance: str) -> tuple[str, dict]:
            return "", {}

    module = importlib.import_module(f"ssm_tools.{name}")
    monkeypatch.setattr(module, "InstanceResolver", Resolver)
    monkeypatch.setattr(module, "verify_plugin_version", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(sys, "platform", "linux")  # ssm-tunnel
    monkeypatch.setattr(signal, "signal", lambda *_args: None)  # ec2-session ignores Ctrl-Z, not in pytest
    monkeypatch.setattr(sys, "argv", [PROGS[name], *argv])
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 1
    assert "No online SSM instance matches 'nosuch'. It may be stopped" in caplog.text


@pytest.mark.parametrize("name", CLI_MODULES)
def test_version(name: str, capsys: pytest.CaptureFixture) -> None:
    module = importlib.import_module(f"ssm_tools.{name}")
    with pytest.raises(SystemExit) as exc:
        module.parse_args(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith(f"ssm-tools/{ssm_tools.__version__}")


@pytest.mark.skipif(not hasattr(signal, "SIGTSTP"), reason="POSIX only")
@pytest.mark.parametrize("name", CLI_MODULES)
def test_import_keeps_signal_handlers(name: str) -> None:
    # Fresh interpreter, because other tests may have imported the module already
    code = f"import signal, ssm_tools.{name}; print(signal.getsignal(signal.SIGTSTP) == signal.SIG_DFL)"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "True"


def test_ecs_session_rejects_unknown_options(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit) as exc:
        ecs_session_cli.parse_args(["--comand", "id", "web"])
    assert exc.value.code == 2
    assert "unrecognized arguments: --comand" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["ssm_port_forward_cli", "ssm_session_cli", "ssm_ssh_cli", "ssm_tunnel_cli"])
@pytest.mark.parametrize(("reason", "valid"), [("x" * 256, True), ("x" * 257, False), ("", False), ("a\nb", False)])
def test_reason(name: str, reason: str, valid: bool, capsys: pytest.CaptureFixture) -> None:
    """What StartSession accepts, checked before we call it."""
    argv = ["--reason", reason, "i-1"]
    if name == "ssm_port_forward_cli":
        argv = ["--remote-port", "80", *argv]
    parse_args = importlib.import_module(f"ssm_tools.{name}").parse_args
    if valid:
        args = parse_args(argv)
        assert (args[0] if isinstance(args, tuple) else args).reason == reason
        return
    with pytest.raises(SystemExit) as exc:
        parse_args(argv)
    assert exc.value.code == 2
    assert "must be 1-256 characters on one line" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("name", "argv"),
    [("ssm_session_cli", []), ("ssm_port_forward_cli", ["--remote-port", "80"])],
)
def test_reason_has_no_short_form(name: str, argv: list[str], capsys: pytest.CaptureFixture) -> None:
    """-r was --reason in 2.2.1. It's long-only now, and ssm-tunnel's -r is --route."""
    with pytest.raises(SystemExit) as exc:
        importlib.import_module(f"ssm_tools.{name}").parse_args([*argv, "-r", "why", "i-1"])
    assert exc.value.code == 2
    assert "unrecognized arguments: -r" in capsys.readouterr().err
