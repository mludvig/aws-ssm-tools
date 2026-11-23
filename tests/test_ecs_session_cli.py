import os
import shlex
import subprocess
from collections.abc import Callable
from typing import Any

import boto3
import pytest
from botocore.stub import Stubber

from ssm_tools import ecs_session_cli
from ssm_tools.ecs_session_cli import USER_SWITCH_SCRIPT, parse_args, user_command


@pytest.mark.parametrize(
    ("argv", "user", "keywords"),
    [
        (["web"], "", ["web"]),
        (["-u", "nobody", "web", "nginx"], "nobody", ["web", "nginx"]),
        (["-l", "65534", "web"], "65534", ["web"]),
        (["nobody@web", "nginx"], "nobody", ["web", "nginx"]),
        (["--user", "app.user", "app.user@web"], "app.user", ["web"]),
        (["-u", "nobody"], "nobody", []),  # the picker
    ],
)
def test_user(argv: list[str], user: str, keywords: list[str]) -> None:
    args = parse_args(argv)
    assert (args.user, args.CONTAINER, args.list) == (user, keywords, False)


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["-u", "a", "b@web"], "conflicting login names: a, b"),
        (["-u", "a;b", "web"], "invalid user name: a;b"),
        (["--user=-c", "web"], "invalid user name: -c"),
        (["a b@web"], "invalid user name: a b"),
    ],
)
def test_bad_user(capsys: pytest.CaptureFixture, argv: list[str], message: str) -> None:
    with pytest.raises(SystemExit) as exc:
        parse_args(argv)
    assert exc.value.code == 2
    assert message in capsys.readouterr().err


@pytest.mark.parametrize("command", ["/bin/sh", "id", 'echo it\'s "$HOME"; ls | wc -l'])
def test_user_command(command: str) -> None:
    assert shlex.split(user_command("nobody", command)) == ["/bin/sh", "-c", USER_SWITCH_SCRIPT, "nobody", command]


@pytest.mark.skipif(not os.path.exists("/bin/sh"), reason="needs /bin/sh")
def test_user_switch_script_without_tools() -> None:
    # No runuser / su / chroot on the PATH: a clear error, and the uid was mapped to its name
    pwd = pytest.importorskip("pwd")
    result = subprocess.run(
        ["/bin/sh", "-c", USER_SWITCH_SCRIPT, str(os.getuid()), "id"],
        capture_output=True,
        text=True,
        env={"PATH": "/nonexistent"},
        check=False,
    )
    assert result.returncode == 126
    assert f"cannot switch to user {pwd.getpwuid(os.getuid()).pw_name}: " in result.stderr


def test_main_runs_command_as_user(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[str] = []

    class FakeResolver:
        def __init__(self, _args: Any) -> None:
            pass

        def resolve_container(self, keywords: list[str]) -> dict[str, Any]:
            return {"keywords": keywords}

    monkeypatch.setattr(ecs_session_cli, "ContainerResolver", FakeResolver)
    monkeypatch.setattr(ecs_session_cli, "verify_plugin_version", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ecs_session_cli, "execute_command", lambda *args: commands.append(args[-1]))
    monkeypatch.setattr("sys.argv", ["ecs-session", "-c", "id", "nobody@web"])
    ecs_session_cli.main()
    assert commands == [user_command("nobody", "id")]


def test_execute_command(monkeypatch: pytest.MonkeyPatch, stub: Callable[[Any], Stubber]) -> None:
    class Resolver:
        session = boto3.session.Session()
        ecs_client = session.client("ecs")

    container = {
        "cluster_name": "lab",
        "cluster_arn": "arn:aws:ecs:ap-southeast-2:123456789012:cluster/lab",
        "task_id": "0123456789abcdef0123456789abcdef",
        "task_arn": "arn:aws:ecs:ap-southeast-2:123456789012:task/lab/0123456789abcdef0123456789abcdef",
        "container_name": "app",
        "runtime_id": "0123456789abcdef0123456789abcdef-1234567890",
    }
    request = {
        "cluster": container["cluster_arn"],
        "task": container["task_arn"],
        "container": "app",
        "command": "id",
        "interactive": True,
    }
    session = {"sessionId": "ecs-execute-command-1", "tokenValue": "secret", "streamUrl": "wss://ssm/1"}
    stub(Resolver.ecs_client).add_response("execute_command", {"session": session}, request)
    calls = []
    monkeypatch.setattr(ecs_session_cli, "run_plugin", lambda *args: calls.append(args) or 0)

    args = parse_args(["--profile", "lab", "web"])
    assert ecs_session_cli.execute_command(Resolver(), container, args, "id") == 0  # type: ignore[arg-type]
    [(ssm_client, response, parameters, profile)] = calls
    assert ssm_client.meta.service_model.service_name == "ssm"
    assert response == {"SessionId": "ecs-execute-command-1", "TokenValue": "secret", "StreamUrl": "wss://ssm/1"}
    assert parameters == {"Target": f"ecs:lab_{container['task_id']}_{container['runtime_id']}"}
    assert profile == "lab"
