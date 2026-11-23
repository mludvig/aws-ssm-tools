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


@pytest.mark.parametrize("name", ALL_MODULES)
def test_import(name: str) -> None:
    importlib.import_module(f"ssm_tools.{name}")


@pytest.mark.parametrize("name", CLI_MODULES)
def test_help(name: str, capsys: pytest.CaptureFixture) -> None:
    module = importlib.import_module(f"ssm_tools.{name}")
    with pytest.raises(SystemExit) as exc:
        module.parse_args(["--help"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("usage:")


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
