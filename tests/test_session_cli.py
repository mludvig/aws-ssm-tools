import shlex
from typing import Any

import pytest

from ssm_tools import ssm_session_cli
from ssm_tools.ssm_session_cli import parse_args

WEB = "i-0123456789abcdef0"


@pytest.mark.parametrize(
    ("argv", "user", "instance"),
    [
        (["web"], "", "web"),
        (["-u", "u", "web"], "u", "web"),
        (["-l", "u", "web"], "u", "web"),
        (["--sudo", "u", "web"], "u", "web"),
        (["u@web"], "u", "web"),
        (["-u", "u", "u@web"], "u", "web"),
        (["a@b@web"], "a@b", "web"),  # split on the last '@', like ssh
        (["-u", "u"], "u", ""),  # the picker
    ],
)
def test_user(argv: list[str], user: str, instance: str) -> None:
    args = parse_args(argv)
    assert (args.user, args.INSTANCE, args.list) == (user, instance, False)


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["-u", "a", "b@web"], "conflicting login names: a, b"),
        (["-u", "a", "-l", "b", "web"], "conflicting login names: a, b"),
        (["-c", "id", "u@web"], "Use only one of --user / --command / --document-name"),
    ],
)
def test_bad_user(capsys: pytest.CaptureFixture, argv: list[str], message: str) -> None:
    with pytest.raises(SystemExit) as exc:
        parse_args(argv)
    assert exc.value.code == 2
    assert message in capsys.readouterr().err


class FakeSSM:
    def start_session(self, **parameters: Any) -> dict[str, Any]:
        return {"SessionId": "s-1", **parameters}


class FakeResolver:
    platform = "Linux"
    ssm_client = FakeSSM()

    def __init__(self, _args: Any) -> None:
        pass

    def resolve_instance(self, _name: str) -> tuple[str, dict[str, Any]]:
        return WEB, {}

    def platform_type(self, _instance_id: str) -> str:
        return self.platform


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Run ec2-session with 'argv' and return the StartSession parameters it would use, or None."""

    def _run(*argv: str) -> dict[str, Any] | None:
        sessions: list[dict[str, Any]] = []

        def run_plugin(_client: Any, response: dict[str, Any], parameters: dict[str, Any], _profile: Any) -> int:
            assert response == {"SessionId": "s-1", **parameters}  # The plugin gets the session we started
            sessions.append(parameters)
            return 0

        monkeypatch.setattr(ssm_session_cli, "InstanceResolver", FakeResolver)
        monkeypatch.setattr(ssm_session_cli, "verify_plugin_version", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(ssm_session_cli, "run_plugin", run_plugin)
        monkeypatch.setattr("sys.argv", ["ec2-session", *argv])
        ssm_session_cli.main()
        return sessions[0] if sessions else None

    return _run


def remote_command(parameters: dict[str, Any]) -> str:
    """The command the instance runs, as AWS-StartInteractiveCommand gets it."""
    assert parameters["DocumentName"] == "AWS-StartInteractiveCommand"
    assert parameters["Parameters"] == {"command": [parameters["Parameters"]["command"][0]]}
    return parameters["Parameters"]["command"][0]


@pytest.mark.parametrize(
    "command",
    ["echo a,b,c", 'echo "hello world"', "echo 'q'", "echo [x] {y}", "ls | wc -l && echo ok; true", "a, b"],
)
def test_command(run: Any, command: str) -> None:
    assert remote_command(run("-c", command, "web")) == command


@pytest.mark.parametrize("user", ["labuser", "labuser;id", "'", '"', "$(id -un)", "a b", "a@b"])
def test_sudo_user(run: Any, user: str) -> None:
    assert shlex.split(remote_command(run("-u", user, "web"))) == ["sudo", "-i", "-u", user]


def test_plain_session(run: Any) -> None:
    assert run("web") == {"Target": WEB}
    assert run("--reason", "a, \"b\" 'c'", "web") == {"Target": WEB, "Reason": "a, \"b\" 'c'"}


def test_document(run: Any, capsys: pytest.CaptureFixture) -> None:
    document = ["--document-name", "AWS-StartInteractiveCommand"]
    assert run(*document, "--parameters", '{"command": ["echo a,b"]}', "web") == {
        "Target": WEB,
        "DocumentName": "AWS-StartInteractiveCommand",
        "Parameters": {"command": ["echo a,b"]},
    }
    with pytest.raises(SystemExit) as exc:
        run(*document, "--parameters", 'command=["top"]', "web")  # The aws CLI shorthand
    assert exc.value.code == 2
    assert "--parameters must be JSON" in capsys.readouterr().err


def test_user_on_windows(run: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(FakeResolver, "platform", "Windows")
    with pytest.raises(SystemExit) as exc:
        run("-u", "Administrator", "web")
    assert exc.value.code == 1
    assert "--user is not supported on Windows instances" in caplog.text
    assert run("-c", "Get-Process", "web") is not None
