import os
import shlex
import subprocess
from typing import Any

import pytest

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
    monkeypatch.setattr(ecs_session_cli, "execute_command", lambda _container, _args, command: commands.append(command))
    monkeypatch.setattr("sys.argv", ["ecs-session", "-c", "id", "nobody@web"])
    ecs_session_cli.main()
    assert commands == [user_command("nobody", "id")]
