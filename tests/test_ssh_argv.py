import re
import shlex
import shutil
from typing import Any

import pytest

from ssm_tools import ssm_ssh_cli
from ssm_tools.ssm_ssh_cli import SshArgs, parse_args, proxy_command

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
    "ProxyCommand=aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters portNumber=%p",
]


class FakeResolver:
    def __init__(self, _args: Any) -> None:
        pass

    def resolve_instance(self, name: str) -> tuple[str, dict[str, Any]]:
        return INSTANCES.get(name, name if name.startswith(("i-", "mi-")) else ""), {}

    def print_list(self, quiet: bool = False) -> tuple[str, list[dict[str, str]]]:
        return "headers", [{"InstanceId": "i-0fedcba9876543210"}]


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> Any:
    """
    Run ec2-ssh with 'argv', return the ssh argv and the (instance, login, key) the key was sent for.
    Without 'real_ssh' ssh_login() says "sshuser".
    """

    def _run(*argv: str, real_ssh: bool = False) -> tuple[list[str], list[tuple[str, str, str]]]:
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
        if not real_ssh:
            monkeypatch.setattr(ssm_ssh_cli, "ssh_login", lambda _options, host: f"sshuser-for-{host}")
        monkeypatch.setattr("sys.argv", ["ec2-ssh", *argv])
        ssm_ssh_cli.main()
        return execs[0], sent

    return _run


WEB = "i-0123456789abcdef0"
HYBRID = "mi-0123456789abcdef0"


@pytest.mark.parametrize(
    ("argv", "ssh_argv", "sent"),
    [
        (
            ["-l", "ec2-user", "web", *RSYNC_COMMAND],
            ["-l", "ec2-user", "--", f"ec2-user@{WEB}", *RSYNC_COMMAND],
            [(WEB, "ec2-user", "")],
        ),
        (["-l", "u", "-s", "--", "web", "sftp"], ["-l", "u", "-s", "--", f"u@{WEB}", "sftp"], [(WEB, "u", "")]),
        (
            ["-i", "~/.ssh/k", "a@b@web", "ls", "-la"],
            ["-i", "~/.ssh/k", "--", f"a@b@{WEB}", "ls", "-la"],
            [(WEB, "a@b", "~/.ssh/k")],
        ),
        (["--debug", "u@web", "hello"], ["-v", "--", f"u@{WEB}", "hello"], [(WEB, "u", "")]),
        (["--no-send-key", "-u", "u", "web", "--", "-p", "1"], ["--", f"u@{WEB}", "-p", "1"], []),
        (["--send-key", "--user", "u", "-l", "u", "u@web"], ["-l", "u", "--", f"u@{WEB}"], [(WEB, "u", "")]),
        # No user given: ask ssh for the name we were given
        (["-o", "User=x", "web"], ["-o", "User=x", "--", f"sshuser-for-web@{WEB}"], [(WEB, "sshuser-for-web", "")]),
        (
            [WEB, "uptime"],
            ["--", f"sshuser-for-{WEB}@{WEB}", "uptime"],
            [(WEB, f"sshuser-for-{WEB}", "")],
        ),
        # Hybrid managed instance: EC2 Instance Connect can't send it a key
        (["u@" + HYBRID], ["--", f"u@{HYBRID}"], []),
        # Not an instance: e.g. an ssh_config alias, passed to ssh untouched and no key sent
        (["-l", "u", "myalias", "uptime"], ["-l", "u", "--", "myalias", "uptime"], []),
        # No destination: the picker
        (["-u", "u"], ["--", "u@i-0fedcba9876543210"], [("i-0fedcba9876543210", "u", "")]),
        (
            [],
            ["--", "sshuser-for-i-0fedcba9876543210@i-0fedcba9876543210"],
            [("i-0fedcba9876543210", "sshuser-for-i-0fedcba9876543210", "")],
        ),
    ],
)
def test_ssh_command(run: Any, argv: list[str], ssh_argv: list[str], sent: list[tuple[str, str, str]]) -> None:
    command, sent_keys = run(*argv)
    assert command == ["ssh", *PROXY, *ssh_argv]
    assert sent_keys == sent


def test_use_endpoint_rejects_hybrid(
    run: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(ssm_ssh_cli, "verify_awscli_version", lambda *_args: True)
    with pytest.raises(SystemExit) as exc:
        run("--use-endpoint", f"u@{HYBRID}")
    assert exc.value.code == 1
    assert f"--use-endpoint only works with EC2 instances, not with {HYBRID}" in caplog.text


@pytest.mark.parametrize(
    "argv",
    [
        ["-u", "a", "b@web"],
        ["--user", "a", "-l", "b", "web"],
        ["-l", "a", "b@web"],
        ["-u", "a", "-u", "b", "web"],
    ],
)
def test_conflicting_logins(capsys: pytest.CaptureFixture, argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        parse_args(argv)
    assert exc.value.code == 2
    assert "conflicting login names: a, b" in capsys.readouterr().err


SSH_CONFIG = """
Host web
  User webuser
Host *
  User defaultuser
"""


@pytest.mark.skipif(not shutil.which("ssh"), reason="needs ssh")
@pytest.mark.parametrize(
    ("argv", "login"),
    [
        (["web"], "webuser"),
        (["-o", "User=optuser", "web"], "optuser"),
        (["-o", "User=optuser", "-l", "luser", "web"], "luser"),
        (["-u", "uuser", "-o", "User=optuser", "web"], "uuser"),
        (["--user", "uuser", "web"], "uuser"),
        (["atuser@web"], "atuser"),
        ([WEB], "defaultuser"),
        ([], "defaultuser"),  # the picker
    ],
)
def test_login_precedence(run: Any, tmp_path: Any, argv: list[str], login: str) -> None:
    config = tmp_path / "ssh_config"
    config.write_text(SSH_CONFIG)
    _, sent_keys = run("-F", str(config), *argv, real_ssh=True)
    assert sent_keys[0][1] == login


@pytest.mark.skipif(not shutil.which("ssh"), reason="needs ssh")
def test_ssh_login_error(caplog: pytest.LogCaptureFixture) -> None:
    with pytest.raises(SystemExit) as exc:
        ssm_ssh_cli.ssh_login(["-o", "NoSuchOption=1"], "web")
    assert exc.value.code == 255
    assert "ssh -G failed" in caplog.text


def run_proxy_command(proxy: str) -> list[str]:
    """Expand ssh's %-tokens in 'proxy' like ssh does and split it like the shell does."""
    tokens = {"%": "%", "h": WEB, "p": "22"}
    return shlex.split(re.sub("%(.)", lambda match: tokens[match.group(1)], proxy))


SSM_ARGV = ["ssm", "start-session", "--target", WEB, "--document-name", "AWS-StartSSHSession"]
SSM_ARGV += ["--parameters", "portNumber=22"]


@pytest.mark.parametrize("reason", ["it's", "100% sure", "ticket%h", "x'$(touch F)'", 'a "b" `c` ; d'])
def test_proxy_command_reason(reason: str) -> None:
    proxy = proxy_command("my profile", "ap-southeast-2", use_endpoint=False, reason=reason)
    assert run_proxy_command(proxy) == [
        "aws",
        *["--profile", "my profile", "--region", "ap-southeast-2"],
        *SSM_ARGV,
        *["--reason", reason],
    ]


def test_proxy_command_endpoint(caplog: pytest.LogCaptureFixture) -> None:
    proxy = proxy_command("", "", use_endpoint=True, reason="why")
    assert run_proxy_command(proxy) == ["aws", "ec2-instance-connect", "open-tunnel", "--instance-id", WEB]
    assert "--reason is not supported with --use-endpoint" in caplog.text
