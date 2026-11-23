from typing import Any

import pytest

from ssm_tools import ssm_ssh_cli
from ssm_tools.ssm_ssh_cli import SshArgs, parse_args

RSYNC_COMMAND = ["rsync", "--server", "--sender", "-logDtpre.iLsfxCIvu", ".", "/etc/hostname"]


@pytest.mark.parametrize(
    ("argv", "options", "destination", "command"),
    [
        (["host"], [], "host", []),
        (["host", "ls", "-la"], [], "host", ["ls", "-la"]),
        (["host", "grep", "-i", "root", "/etc/passwd"], [], "host", ["grep", "-i", "root", "/etc/passwd"]),
        # rsync -a -e ec2-ssh
        (["-l", "ec2-user", "host", *RSYNC_COMMAND], ["-l", "ec2-user"], "host", RSYNC_COMMAND),
        # scp (sftp) and scp -O
        (
            ["-x", "-oPermitLocalCommand=no", "-oClearAllForwardings=yes", "-l", "u", "-s", "--", "host", "sftp"],
            ["-x", "-o", "PermitLocalCommand=no", "-o", "ClearAllForwardings=yes", "-l", "u", "-s"],
            "host",
            ["sftp"],
        ),
        (["-x", "-l", "u", "--", "host", "scp", "-f", "x"], ["-x", "-l", "u"], "host", ["scp", "-f", "x"]),
        # Options after the destination are still ssh's, up to the remote command
        (["host", "-p", "2222", "ls", "-l"], ["-p", "2222"], "host", ["ls", "-l"]),
        (["host", "--", "-p", "1"], [], "host", ["-p", "1"]),
        # Clusters
        (["-vvv", "-p2222", "-oUser=x", "host"], ["-v", "-v", "-v", "-p", "2222", "-o", "User=x"], "host", []),
        (["-tl", "u", "host"], ["-t", "-l", "u"], "host", []),
        # Destinations are passed through, the '@' split is done later
        (["jdoe@corp.example@host"], [], "jdoe@corp.example@host", []),
        (["2001:db8::1"], [], "2001:db8::1", []),
        (["./not-a-path"], [], "./not-a-path", []),
        ([], [], "", []),
        (["-A"], ["-A"], "", []),
    ],
)
def test_ssh_argv(argv: list[str], options: list[str], destination: str, command: list[str]) -> None:
    _, ssh_args = parse_args(argv)
    assert ssh_args == SshArgs(options, destination, command)


@pytest.mark.parametrize(
    ("argv", "own", "command"),
    [
        (["--profile", "P", "host"], {"profile": "P"}, []),
        (["host", "--region=R", "--no-send-key"], {"region": "R", "send_key": False}, []),
        # Our options in the remote command are for the remote command
        (["host", "aws", "s3", "ls", "--profile", "X"], {"profile": None}, ["aws", "s3", "ls", "--profile", "X"]),
        (["host", "echo", "--list"], {"list": False}, ["echo", "--list"]),
    ],
)
def test_own_options(argv: list[str], own: dict[str, Any], command: list[str]) -> None:
    args, ssh_args = parse_args(argv)
    assert {key: getattr(args, key) for key in own} == own
    assert ssh_args.command == command


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["host", "-l"], "option -l requires a value"),
        (["-i"], "option -i requires a value"),
        (["host", "--profile"], "option --profile requires a value"),
        (["--prof", "X", "host"], "unrecognized arguments: --prof"),
    ],
)
def test_bad_options(capsys: pytest.CaptureFixture, argv: list[str], message: str) -> None:
    with pytest.raises(SystemExit) as exc:
        parse_args(argv)
    assert exc.value.code == 2
    assert message in capsys.readouterr().err


def test_option_value() -> None:
    ssh_args = SshArgs(["-v", "-o", "-l", "-l", "u", "-i", "k1", "-i", "k2"])
    assert ssh_args.option("-l") == "u"
    assert ssh_args.option("-i") == "k1"
    assert ssh_args.option("-p") == ""


INSTANCES = {"web": "i-0123456789abcdef0"}
PROXY = [
    "-o",
    "ProxyCommand=aws  ssm start-session --target %h --document-name AWS-StartSSHSession --parameters portNumber=%p",
]


class FakeResolver:
    def __init__(self, _args: Any) -> None:
        pass

    def resolve_instance(self, name: str) -> tuple[str, dict[str, Any]]:
        return INSTANCES.get(name, ""), {}

    def print_list(self, quiet: bool = False) -> tuple[str, list[dict[str, str]]]:
        return "headers", [{"InstanceId": "i-0fedcba9876543210"}]


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Run ec2-ssh with 'argv', return the ssh argv and the (instance, login, key) the key was sent for."""

    def _run(*argv: str) -> tuple[list[str], list[tuple[str, str, str]]]:
        sent: list[tuple[str, str, str]] = []
        execs: list[list[str]] = []

        class FakeEIC:
            def __init__(self, _args: Any) -> None:
                pass

            def send_ssh_key(self, *key_args: str) -> None:
                sent.append(key_args)  # type: ignore[arg-type]

        monkeypatch.setattr(ssm_ssh_cli, "InstanceResolver", FakeResolver)
        monkeypatch.setattr(ssm_ssh_cli, "EC2InstanceConnectHelper", FakeEIC)
        monkeypatch.setattr(ssm_ssh_cli, "verify_plugin_version", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(ssm_ssh_cli, "target_selector", lambda _headers, targets: targets[0])
        monkeypatch.setattr(ssm_ssh_cli.os, "execvp", lambda _file, command: execs.append(command))
        monkeypatch.setattr("sys.argv", ["ec2-ssh", *argv])
        ssm_ssh_cli.main()
        return execs[0], sent

    return _run


@pytest.mark.parametrize(
    ("argv", "ssh_argv", "sent"),
    [
        (
            ["-l", "ec2-user", "web", *RSYNC_COMMAND],
            ["-l", "ec2-user", "--", "i-0123456789abcdef0", *RSYNC_COMMAND],
            [("i-0123456789abcdef0", "ec2-user", "")],
        ),
        (
            ["-l", "u", "-s", "--", "web", "sftp"],
            ["-l", "u", "-s", "--", "i-0123456789abcdef0", "sftp"],
            [("i-0123456789abcdef0", "u", "")],
        ),
        (
            ["-i", "~/.ssh/k", "a@b@web", "ls", "-la"],
            ["-i", "~/.ssh/k", "--", "a@b@i-0123456789abcdef0", "ls", "-la"],
            [("i-0123456789abcdef0", "a@b", "~/.ssh/k")],
        ),
        (
            ["--debug", "u@web", "hello"],
            ["-v", "--", "u@i-0123456789abcdef0", "hello"],
            [("i-0123456789abcdef0", "u", "")],
        ),
        (["--no-send-key", "web", "--", "-p", "1"], ["--", "i-0123456789abcdef0", "-p", "1"], []),
        # Not an instance: e.g. an ssh_config alias, passed to ssh untouched and no key sent
        (["-l", "u", "myalias", "uptime"], ["-l", "u", "--", "myalias", "uptime"], []),
        # No destination: the picker
        (["-l", "u"], ["-l", "u", "--", "i-0fedcba9876543210"], [("i-0fedcba9876543210", "u", "")]),
    ],
)
def test_ssh_command(run: Any, argv: list[str], ssh_argv: list[str], sent: list[tuple[str, str, str]]) -> None:
    command, sent_keys = run(*argv)
    assert command == ["ssh", *PROXY, *ssh_argv]
    assert sent_keys == sent
