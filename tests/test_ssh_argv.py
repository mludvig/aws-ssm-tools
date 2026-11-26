import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from typing import Any

import pytest

from ssm_tools import ec2_instance_connect, ssm_ssh_cli
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
SSH = ["(child)", "ssh"] if os.name == "nt" else ["ssh"]  # See test_windows_waits_for_ssh
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
    Without 'real_ssh' ssh says the user is "sshuser-for-DESTINATION", without 'real_keys' the key is "KEY".
    """

    def _run(
        *argv: str,
        real_ssh: bool = False,
        real_keys: bool = False,
    ) -> tuple[list[str], list[tuple[str, str, str]]]:
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
        monkeypatch.setattr(ssm_ssh_cli, "run_session", lambda command: execs.append(["(child)", *command]) or 0)
        if not real_ssh:
            monkeypatch.setattr(
                ssm_ssh_cli,
                "ssh_config",
                lambda _options, dest: {"user": [f"sshuser-for-{dest}"], "serveraliveinterval": ["60"]},
            )
        if not real_keys:
            monkeypatch.setattr(ssm_ssh_cli, "select_key", lambda _config: "KEY")
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
            [(WEB, "ec2-user", "KEY")],
        ),
        (["-l", "u", "-s", "--", "web", "sftp"], ["-l", "u", "-s", "--", f"u@{WEB}", "sftp"], [(WEB, "u", "KEY")]),
        (
            ["-i", "~/.ssh/k", "a@b@web", "ls", "-la"],
            ["-i", "~/.ssh/k", "--", f"a@b@{WEB}", "ls", "-la"],
            [(WEB, "a@b", "KEY")],
        ),
        (["--debug", "u@web", "hello"], ["-v", "--", f"u@{WEB}", "hello"], [(WEB, "u", "KEY")]),
        (["--no-send-key", "-u", "u", "web", "--", "-p", "1"], ["--", f"u@{WEB}", "-p", "1"], []),
        (["--send-key", "--user", "u", "-l", "u", "u@web"], ["-l", "u", "--", f"u@{WEB}"], [(WEB, "u", "KEY")]),
        # No user given: ask ssh for the name we were given
        (["-o", "User=x", "web"], ["-o", "User=x", "--", f"sshuser-for-web@{WEB}"], [(WEB, "sshuser-for-web", "KEY")]),
        (
            [WEB, "uptime"],
            ["--", f"sshuser-for-{WEB}@{WEB}", "uptime"],
            [(WEB, f"sshuser-for-{WEB}", "KEY")],
        ),
        # Hybrid managed instance: EC2 Instance Connect can't send it a key
        (["u@" + HYBRID], ["--", f"u@{HYBRID}"], []),
        # Not an instance: e.g. an ssh_config alias, passed to ssh untouched and no key sent
        (["-l", "u", "myalias", "uptime"], ["-l", "u", "--", "myalias", "uptime"], []),
        # No destination: the picker
        (["-u", "u"], ["--", "u@i-0fedcba9876543210"], [("i-0fedcba9876543210", "u", "KEY")]),
        (
            [],
            ["--", "sshuser-for-i-0fedcba9876543210@i-0fedcba9876543210"],
            [("i-0fedcba9876543210", "sshuser-for-i-0fedcba9876543210", "KEY")],
        ),
    ],
)
def test_ssh_command(run: Any, argv: list[str], ssh_argv: list[str], sent: list[tuple[str, str, str]]) -> None:
    command, sent_keys = run(*argv)
    assert command == [*SSH, *PROXY, *ssh_argv]
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
@pytest.mark.parametrize(
    ("config", "argv", "added"),
    [
        ("", ["u@web"], True),
        ("", ["myalias"], True),
        ("", ["-o", "ServerAliveInterval=5", "u@web"], False),
        ("Host i-*\n  ServerAliveInterval 30\n", ["u@web"], False),  # ssh connects to the instance ID
        ("Host web\n  ServerAliveInterval 30\n", ["u@web"], True),
    ],
)
def test_server_alive_interval(run: Any, tmp_path: Any, config: str, argv: list[str], added: bool) -> None:
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(config)
    command, _ = run("-F", str(ssh_config), *argv, real_ssh=True)
    assert ("ServerAliveInterval=15" in command) == added


@pytest.mark.skipif(not shutil.which("ssh"), reason="needs ssh")
def test_ssh_config_error(caplog: pytest.LogCaptureFixture) -> None:
    with pytest.raises(SystemExit) as exc:
        ssm_ssh_cli.ssh_config(["-o", "NoSuchOption=1"], "web")
    assert exc.value.code == 255
    assert "ssh -G failed" in caplog.text


@pytest.fixture
def no_user_keys(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """No ~/.ssh keys and no agent. Return an ssh_config file to write to."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    return tmp_path / "ssh_config"


def make_key(path: Any) -> str:
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)], check=True)
    return (path.parent / f"{path.name}.pub").read_text().strip()


@pytest.mark.skipif(not shutil.which("ssh-keygen"), reason="needs ssh")
def test_sends_the_key_for_the_instance_id(run: Any, tmp_path: Any, no_user_keys: Any) -> None:
    keys = {name: make_key(tmp_path / name) for name in ("web", "instance", "user")}
    no_user_keys.write_text(
        f"Host web\n  IdentityFile {tmp_path}/web\n"
        f"Match host i-* user u\n  IdentityFile {tmp_path}/user\n"
        f"Host i-*\n  IdentityFile {tmp_path}/instance\n",
    )
    _, sent_keys = run("-F", str(no_user_keys), "u@web", real_ssh=True, real_keys=True)
    assert sent_keys == [(WEB, "u", keys["user"])]  # ssh connects to u@INSTANCE_ID, not to web
    _, sent_keys = run("-F", str(no_user_keys), "x@web", real_ssh=True, real_keys=True)
    assert sent_keys == [(WEB, "x", keys["instance"])]


def test_windows_waits_for_ssh(run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "name", "nt")  # Where os.execvp() returns at once and loses the exit code
    command, _ = run("u@web")
    assert command[:2] == ["(child)", "ssh"]


@pytest.mark.skipif(not shutil.which("ssh-keygen"), reason="needs ssh")
def test_gen_key(run: Any, tmp_path: Any, no_user_keys: Any) -> None:
    mine = make_key(tmp_path / "mine")
    no_user_keys.write_text(f"Host *\n  IdentityFile {tmp_path}/mine\n")
    command, sent_keys = run("--gen-key", "-F", str(no_user_keys), "u@web", real_ssh=True, real_keys=True)
    assert command[:2] == ["(child)", "ssh"]  # Not exec'd: we delete the key when ssh exits
    key_file = command[command.index("-i") + 1]
    assert command[command.index("-i") :][:4] == ["-i", key_file, "-o", "IdentitiesOnly=yes"]
    (instance_id, login, public_key), *_ = sent_keys
    assert (instance_id, login) == (WEB, "u")
    assert public_key.startswith("ssh-ed25519 ") and public_key.endswith(" ec2-ssh") and public_key != mine
    assert not os.path.exists(os.path.dirname(key_file))


def test_gen_key_without_sending(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit) as exc:
        parse_args(["--gen-key", "--no-send-key", "web"])
    assert exc.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def run_proxy_command(proxy: str) -> list[str]:
    """Expand ssh's %-tokens in 'proxy' like ssh does and split it like the shell does."""
    tokens = {"%": "%", "h": WEB, "p": "22"}
    command = re.sub("%(.)", lambda match: tokens[match.group(1)], proxy)
    if os.name != "nt":
        return shlex.split(command)
    # Win32-OpenSSH starts it without a shell, and aws.exe splits its command line as Python does
    program, _, args = command.partition(" ")
    echo = subprocess.list2cmdline([sys.executable, "-c", "import json, sys; print(json.dumps(sys.argv[1:]))"])
    return [program, *json.loads(subprocess.run(f"{echo} {args}", capture_output=True, check=True).stdout)]


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


@pytest.mark.parametrize(
    ("value", "target"),
    [
        ("u@web.ssm:22", ("u", "web.ssm", 22)),
        ("a@b@i-0123456789abcdef0:2222", ("a@b", "i-0123456789abcdef0", 2222)),
    ],
)
def test_proxy_target(value: str, target: tuple[str, str, int]) -> None:
    args, _ = parse_args(["--proxy", value])
    assert args.proxy == target


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--proxy", "web.ssm:22"], "web.ssm:22 isn't USER@HOST:PORT"),
        (["--proxy", "u@web.ssm"], "isn't USER@HOST:PORT"),
        (["--proxy", "u@:22"], "isn't USER@HOST:PORT"),
        (["--proxy", "u@web.ssm:0"], "0 isn't a port number"),
        (["--proxy", "u@web.ssm:22", "--print-ssh-config"], "not allowed with argument"),
        (["--proxy", "u@web.ssm:22", "web"], "only go with --profile"),
        (["--print-ssh-config", "-i", "key"], "only go with --profile"),
        (["--print-ssh-config", "--gen-key"], "only go with --profile"),
        (["--proxy", "u@web.ssm:22", "--use-endpoint"], "only go with --profile"),
        (["--proxy", "u@web.ssm:22", "--list"], "only go with --profile"),
    ],
)
def test_bad_proxy(capsys: pytest.CaptureFixture, argv: list[str], message: str) -> None:
    with pytest.raises(SystemExit) as exc:
        parse_args(argv)
    assert exc.value.code == 2
    assert message in capsys.readouterr().err


@pytest.mark.parametrize(
    ("host", "name", "region"),
    [
        ("web.ssm", "web", ""),
        ("web.ap-southeast-6.ssm", "web", "ap-southeast-6"),
        ("WEB.US-GOV-WEST-1.SSM", "WEB", "us-gov-west-1"),
        ("web.eusc-de-east-1.ssm", "web", "eusc-de-east-1"),
        ("web.prod.ssm", "web.prod", ""),  # Not a region: part of the name
        ("db.prod.eu-west-1.ssm", "db.prod", "eu-west-1"),
        ("ap-southeast-2.ssm", "ap-southeast-2", ""),  # Nothing left for a name
        (f"{WEB}.eu-west-1.ssm", WEB, "eu-west-1"),
        (WEB, WEB, ""),
        ("web.example.com", "web.example.com", ""),
    ],
)
def test_split_host(host: str, name: str, region: str) -> None:
    assert ssm_ssh_cli.split_host(host) == (name, region)


@pytest.fixture
def run_proxy(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> Any:
    """
    Run ec2-ssh with 'argv' and return the region the resolver got, the keys sent,
    the StartSession parameters, and what went to stdout.
    """

    def _run(*argv: str, select_key: Any = None) -> tuple[str, list[tuple[str, ...]], dict[str, Any], str]:
        regions, sent, started = [], [], []

        class SsmClient:
            def start_session(self, **parameters: Any) -> dict[str, str]:
                started.append(parameters)
                return {"SessionId": "s-1"}

        class Resolver(FakeResolver):
            def __init__(self, args: Any) -> None:
                regions.append(args.region)
                self.ssm_client = SsmClient()

        class FakeEIC:
            def __init__(self, _args: Any) -> None:
                pass

            def send_ssh_key(self, *key_args: str) -> None:
                sent.append(key_args)

        monkeypatch.setattr(ssm_ssh_cli, "InstanceResolver", Resolver)
        monkeypatch.setattr(ssm_ssh_cli, "EC2InstanceConnectHelper", FakeEIC)
        monkeypatch.setattr(ssm_ssh_cli, "verify_plugin_version", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(ssm_ssh_cli, "ssh_config", lambda options, dest: {"for": [*options, dest]})
        monkeypatch.setattr(ssm_ssh_cli, "select_key", select_key or (lambda config, _hint: f"KEY for {config['for']}"))
        monkeypatch.setattr(ssm_ssh_cli, "run_plugin", lambda _client, response, _parameters, _profile: 0)
        monkeypatch.setattr("sys.argv", ["ec2-ssh", *argv])
        assert ssm_ssh_cli.main() == 0
        return regions[0], sent, started[0], capsys.readouterr().out

    return _run


def test_proxy(run_proxy: Any) -> None:
    region, sent, parameters, out = run_proxy("--region", "eu-west-1", "--reason", "why", "--proxy", "u@web.ssm:2222")
    assert region == "eu-west-1"
    assert sent == [(WEB, "u", "KEY for ['u@web.ssm']")]  # The keys ssh will offer to the host it was given
    assert parameters == {
        "Target": WEB,
        "DocumentName": "AWS-StartSSHSession",
        "Parameters": {"portNumber": ["2222"]},
        "Reason": "why",
    }
    assert out == ""  # stdout is ssh's connection


def test_proxy_region_in_the_name(run_proxy: Any) -> None:
    region, _, parameters, _ = run_proxy("--region", "eu-west-1", "--proxy", "u@web.ap-southeast-6.ssm:22")
    assert (region, parameters["Target"]) == ("ap-southeast-6", WEB)


@pytest.mark.parametrize("argv", [["--proxy", f"u@{HYBRID}:22"], ["--no-send-key", "--proxy", "u@web.ssm:22"]])
def test_proxy_without_a_key(run_proxy: Any, argv: list[str]) -> None:
    _, sent, parameters, _ = run_proxy(*argv)
    assert sent == []
    assert parameters["Target"] in (WEB, HYBRID)


def test_proxy_not_found(run_proxy: Any, capsys: pytest.CaptureFixture, caplog: pytest.LogCaptureFixture) -> None:
    with pytest.raises(SystemExit) as exc:
        run_proxy("--proxy", "u@nosuch.ssm:22")
    assert exc.value.code == 1
    assert "No online SSM instance matches 'nosuch'" in caplog.text
    assert capsys.readouterr().out == ""


REASON = ["--reason", "it's 100% %h"]


@pytest.mark.skipif(not shutil.which("ssh"), reason="needs ssh")
def test_print_ssh_config(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    tmp_path: Any,
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["ec2-ssh", "--profile", "my profile", "--region", "eu-west-1", *REASON, "--no-send-key", "--print-ssh-config"],
    )
    assert ssm_ssh_cli.main() == 0
    config = tmp_path / "ssh_config"
    config.write_text(capsys.readouterr().out)
    for host in (WEB, HYBRID, "web.ssm", "Web.AP-Southeast-6.ssm"):
        settings = ssm_ssh_cli.ssh_config(["-F", str(config)], f"u@{host}")
        assert settings["serveraliveinterval"] == ["15"]
        tokens = {"%": "%", "r": "u", "h": settings["hostname"][0], "p": "22"}
        command = re.sub("%(.)", lambda match: tokens[match.group(1)], settings["proxycommand"][0])
        if os.name != "nt":  # Win32-OpenSSH runs it without a shell
            assert shlex.split(command) == [
                *[os.path.abspath(sys.argv[0]), "--quiet"],
                *["--profile", "my profile", "--region", "eu-west-1", *REASON, "--no-send-key"],
                *["--proxy", f"u@{host.lower()}:22"],
            ]
    assert ssm_ssh_cli.ssh_config(["-F", str(config)], "u@web.example.com").get("proxycommand") is None


def test_print_ssh_config_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    """A PyInstaller exe's argv[0] is the name as typed, e.g. 'ec2-ssh' from PATH"""
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys.executable", os.path.join(os.sep, "opt", "ssm tools", "ec2-ssh"))
    monkeypatch.setattr("sys.argv", ["ec2-ssh"])
    proxy = ssm_ssh_cli.ssh_config_stanza("", "", "", send_key=True).splitlines()[1]
    assert proxy == f"    ProxyCommand {ssm_ssh_cli._quote(sys.executable)} --quiet --proxy %r@%h:%p"


def test_proxy_key_hint(run_proxy: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """ssh's -i and our --gen-key don't reach the proxy"""
    monkeypatch.setattr(ec2_instance_connect, "agent_keys", lambda _agent: [])
    with pytest.raises(SystemExit) as exc:
        run_proxy("--proxy", "u@web.ssm:22", select_key=ec2_instance_connect.select_key)
    assert exc.value.code == 1
    assert "Give one with IdentityFile in the ssh_config, or add --no-send-key to the ProxyCommand." in caplog.text
