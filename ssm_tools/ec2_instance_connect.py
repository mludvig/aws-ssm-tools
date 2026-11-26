# Needed for type hints
import argparse
import base64
import hashlib
import logging
import os
import subprocess
import sys

from .common import AWSSessionBase

logger = logging.getLogger("ssm-tools.ec2-instance-connect")

EIC_KEY_TYPES = ("ssh-rsa", "ssh-ed25519")  # All that EC2 Instance Connect accepts


def _output(command: list[str], env: dict[str, str] | None = None) -> str:
    """Return the command's output, or '' if it failed. It never gets to prompt for a passphrase."""
    result = subprocess.run(command, capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, check=False)
    if result.returncode != 0:
        logger.debug("%s failed: %s", " ".join(command), result.stderr.strip())
        return ""
    return result.stdout.strip()


def fingerprint(public_key: str) -> str:
    """The SHA256 fingerprint of a public key line, as ssh-keygen -l shows it."""
    blob = base64.b64decode(public_key.split()[1])
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def file_key(path: str) -> tuple[str, str]:
    """
    Return the fingerprint and public key of a private key file. The public key is '' if
    only the agent can tell it (an encrypted key without .pub), both are '' if it's no key.
    """
    try:
        with open(path + ".pub") as pub:
            public_key = pub.readline().strip()
    except OSError:
        public_key = _output(["ssh-keygen", "-y", "-P", "", "-f", path])  # Unencrypted keys
    if public_key:
        return fingerprint(public_key), public_key
    words = _output(["ssh-keygen", "-l", "-f", path]).split()  # E.g. "256 SHA256:... comment (ED25519)"
    return (words[1] if len(words) > 1 else ""), ""


def agent_keys(identity_agent: str) -> list[str]:
    """Return the public keys in the agent that ssh would use, as per its IdentityAgent."""
    if identity_agent == "none":
        return []
    env = None
    if identity_agent and identity_agent != "SSH_AUTH_SOCK":
        env = {**os.environ, "SSH_AUTH_SOCK": os.path.expandvars(os.path.expanduser(identity_agent))}
    return _output(["ssh-add", "-L"], env).splitlines()


def select_key(config: dict[str, list[str]], hint: str = "Give one with -i, or use --gen-key or --no-send-key.") -> str:
    """
    Return the first RSA or Ed25519 public key that ssh will offer with this 'ssh -G' config,
    in the order of OpenSSH's pubkey_prepare(). See docs/adr/0010-eic-key-selection.md
    """
    files: dict[str, str] = {}
    for name in config.get("identityfile", []):
        path = os.path.expanduser(name)
        if os.path.isfile(path):
            key_fingerprint, public_key = file_key(path)
            if key_fingerprint:
                files.setdefault(key_fingerprint, public_key)

    agent = {fingerprint(key): key for key in agent_keys(config.get("identityagent", [""])[0])}
    identities_only = config.get("identitiesonly") == ["yes"]

    # The agent's copies of the files first, then the other agent keys, then the rest of the files
    keys = [key for key_fingerprint, key in agent.items() if key_fingerprint in files]
    keys += [] if identities_only else [key for key_fingerprint, key in agent.items() if key_fingerprint not in files]
    keys += [key for key_fingerprint, key in files.items() if key_fingerprint not in agent]
    for key in keys:
        if key.split(" ", 1)[0] in EIC_KEY_TYPES:
            logger.info("Sending SSH key %s", fingerprint(key))
            return key

    logger.error(
        "None of the SSH keys that ssh would use is an RSA or Ed25519 key with a known public key, "
        "which EC2 Instance Connect needs. %s",
        hint,
    )
    sys.exit(1)


def generate_key(directory: str) -> str:
    """Create a key pair without a passphrase in 'directory' and return the private key's path."""
    path = os.path.join(directory, "id_ed25519")
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "ec2-ssh", "-f", path], check=True)
    return path


class EC2InstanceConnectHelper(AWSSessionBase):
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__(args)

        # Create boto3 client from session
        self.ec2ic_client = self.session.client("ec2-instance-connect")

    def send_ssh_key(self, instance_id: str, login_name: str, public_key: str) -> None:
        logger.debug("Sending SSH key for %s@%s: %s", login_name, instance_id, public_key)

        result = self.ec2ic_client.send_ssh_public_key(
            InstanceId=instance_id,
            InstanceOSUser=login_name,
            SSHPublicKey=public_key,
        )

        if not result["Success"]:
            logger.error("Failed to send SSH Key to %s", instance_id)
            sys.exit(1)
