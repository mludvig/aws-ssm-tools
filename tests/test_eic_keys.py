import os
import pathlib
import shutil
import subprocess

import pytest

from ssm_tools import ec2_instance_connect
from ssm_tools.ec2_instance_connect import agent_keys, fingerprint, select_key

pytestmark = pytest.mark.skipif(not shutil.which("ssh-keygen"), reason="needs ssh-keygen")


def make_key(path: pathlib.Path, key_type: str = "ed25519", *options: str, pub: bool = True) -> str:
    """Create a key pair, without the .pub unless 'pub', and return its public key."""
    subprocess.run(["ssh-keygen", "-q", "-t", key_type, "-N", "", *options, "-f", str(path)], check=True)
    public_key = path.with_suffix(".pub").read_text().strip()
    if not pub:
        path.with_suffix(".pub").unlink()
    return public_key


@pytest.fixture
def agent(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The keys in a fake agent."""
    keys: list[str] = []
    monkeypatch.setattr(ec2_instance_connect, "agent_keys", lambda _identity_agent: keys)
    return keys


def test_ed25519_file_after_missing_and_ecdsa(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    agent: list[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    make_key(tmp_path / "id_ecdsa", "ecdsa")
    ed25519 = make_key(tmp_path / "id_ed25519")
    files = [f"~/{name}" for name in ("id_rsa", "id_ecdsa", "id_ed25519")]  # As ssh -G shows them
    assert select_key({"identityfile": files}) == ed25519


def test_ecdsa_agent_key_skipped(tmp_path: pathlib.Path, agent: list[str]) -> None:
    agent += [make_key(tmp_path / "ecdsa", "ecdsa"), make_key(tmp_path / "ed25519")]
    assert select_key({}) == agent[1]


def test_identities_only(tmp_path: pathlib.Path, agent: list[str]) -> None:
    agent.append(make_key(tmp_path / "agent"))
    file_key = make_key(tmp_path / "file")
    config = {"identityfile": [str(tmp_path / "file")], "identitiesonly": ["yes"]}
    assert select_key(config) == file_key
    assert select_key({**config, "identitiesonly": ["no"]}) == agent[0]  # Agent keys go before files


def test_agent_copy_of_a_file_goes_first(tmp_path: pathlib.Path, agent: list[str]) -> None:
    agent += [make_key(tmp_path / "agent"), make_key(tmp_path / "second")]
    make_key(tmp_path / "first")
    files = [str(tmp_path / "first"), str(tmp_path / "second")]
    assert select_key({"identityfile": files}) == agent[1]


def test_encrypted_key_without_pub(tmp_path: pathlib.Path, agent: list[str], caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "encrypted"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "secret", "-f", str(path)], check=True)
    public_key = path.with_suffix(".pub").read_text().strip()
    path.with_suffix(".pub").unlink()
    config = {"identityfile": [str(path)], "identitiesonly": ["yes"]}

    agent += [make_key(tmp_path / "other"), public_key]
    assert select_key(config) == public_key  # Matched by fingerprint, without the passphrase

    agent.clear()
    with pytest.raises(SystemExit) as exc:
        select_key(config)
    assert exc.value.code == 1
    assert "None of the SSH keys" in caplog.text


def test_unencrypted_pem_key_without_pub(tmp_path: pathlib.Path, agent: list[str]) -> None:
    public_key = make_key(tmp_path / "pem", "rsa", "-m", "PEM", pub=False)
    assert select_key({"identityfile": [str(tmp_path / "pem")]}).split()[:2] == public_key.split()[:2]


def test_fingerprint(tmp_path: pathlib.Path) -> None:
    public_key = make_key(tmp_path / "key")
    shown = subprocess.run(["ssh-keygen", "-l", "-f", str(tmp_path / "key")], capture_output=True, text=True)
    assert fingerprint(public_key) == shown.stdout.split()[1]


@pytest.mark.skipif(os.name != "posix", reason="fake ssh-add is a shell script")
@pytest.mark.parametrize(
    ("identity_agent", "socket"),
    [("", "/env.sock"), ("SSH_AUTH_SOCK", "/env.sock"), ("~/agent.sock", "/home/u/agent.sock"), ("none", None)],
)
def test_agent_keys(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    identity_agent: str,
    socket: str | None,
) -> None:
    ssh_add = tmp_path / "ssh-add"
    ssh_add.write_text('#!/bin/sh\necho "ssh-ed25519 AAAA $SSH_AUTH_SOCK"\n')
    ssh_add.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path), prepend=os.pathsep)
    monkeypatch.setenv("SSH_AUTH_SOCK", "/env.sock")
    monkeypatch.setenv("HOME", "/home/u")
    assert agent_keys(identity_agent) == ([f"ssh-ed25519 AAAA {socket}"] if socket else [])
