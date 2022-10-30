import sys
import logging
import pathlib
import subprocess

# Needed for type hints
import argparse
from typing import List, Tuple, Optional

from .common import AWSSessionBase

logger = logging.getLogger("ssm-tools.ec2-instance-connect")


class EC2InstanceConnectHelper(AWSSessionBase):
    SSH_AGENT_LABEL = "{SSH-AGENT}"

    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__(args)

        # Create boto3 client from session
        self.ec2ic_client = self.session.client("ec2-instance-connect")

    def obtain_ssh_key(self, key_file_name: str) -> Tuple[str, Optional[str]]:
        def _read_ssh_agent_keys() -> List[str]:
            cp = subprocess.run(["ssh-add", "-L"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            if cp.returncode != 0:
                logger.debug("Failed to run: ssh-add -L: %s", cp.stderr.decode("utf-8").strip().replace("\n", " "))
                return []
            return cp.stdout.decode("utf-8").split("\n")

        def _read_ssh_public_key(key_file_name_pub: str) -> str:
            try:
                key_path = pathlib.Path(key_file_name_pub).expanduser()
                logger.debug("Going to read key from: %s", key_path)
                pub_key_raw = key_path.read_text().split("\n")
                for line in pub_key_raw:
                    if line.startswith("ecdsa-sha2-"):
                        logger.error("ECDSA keys are not yet supported by EC2 Instance Connect: %s", key_file_name_pub)
                        sys.exit(1)
                    if line.startswith("ssh-"):
                        return line
            except (FileNotFoundError, PermissionError) as ex:
                logger.debug("Could not read the public key: %s", ex)
            return ""

        if not key_file_name:
            # No key_file_name was specified, try ...
            # - SSH Agent keys (doesn't matter which one - we'll immediately connect to it)
            ssh_keys = _read_ssh_agent_keys()
            if ssh_keys:
                logger.info("Using SSH key from SSH Agent, should be as good as any.")
                return ssh_keys[0], self.SSH_AGENT_LABEL

            # - ~/.ssh/id_rsa.pub
            ssh_key = _read_ssh_public_key("~/.ssh/id_rsa.pub")
            if ssh_key:
                logger.info("Using SSH key from ~/.ssh/id_rsa.pub - should work in most cases")
                return ssh_key, "~/.ssh/id_rsa"

            # - ~/.ssh/id_dsa.pub
            ssh_key = _read_ssh_public_key("~/.ssh/id_dsa.pub")
            if ssh_key:
                logger.info("Using SSH key from ~/.ssh/id_dsa.pub - should work in most cases")
                return ssh_key, "~/.ssh/id_dsa"

        else:  # i.e. key_file_name is set
            logger.info("Looking for a public key matching: %s", key_file_name)

            # Try reading the public key file (key_file_name + ".pub" suffix)
            key_file_name_pub = key_file_name + ".pub"
            ssh_key = _read_ssh_public_key(key_file_name_pub)
            if ssh_key:
                logger.info("Found a matching SSH Public Key in %s", key_file_name_pub)
                return ssh_key, key_file_name

            # Try reading the public key from SSH Agent
            for line in _read_ssh_agent_keys():
                if line.endswith(key_file_name):
                    logger.info("Found a matching SSH Public Key through SSH Agent")
                    return line, self.SSH_AGENT_LABEL
            logger.debug("Could not find the public key for %s in SSH Agent", key_file_name)

            # Try extracting the public key from the provided private key
            logger.warning("Trying to extract the public key from %s - you may be asked for a passphrase!", key_file_name)
            cp = subprocess.run(["ssh-keygen", "-y", "-f", key_file_name], stdout=subprocess.PIPE, check=False)
            if cp.returncode == 0:
                logger.info("Extracted the public key from: %s", key_file_name)
                return cp.stdout.decode("utf-8").split("\n")[0], key_file_name
            logger.debug("Could not extract the public key from %s", key_file_name)

        logger.warning("Unable to find SSH public key from any available source.")
        logger.warning("Use --debug for more details on what we tried.")
        sys.exit(1)

    def send_ssh_key(self, instance_id: str, login_name: str, key_file_name: str) -> None:
        if not login_name:
            logger.error('Unable to figure out the EC2 login name. Use "-l {user}" or {user}@{instance}.')
            sys.exit(1)

        public_key, private_key_file = self.obtain_ssh_key(key_file_name)
        logger.debug("SSH Key from %s: %s", private_key_file, public_key)

        result = self.ec2ic_client.send_ssh_public_key(
            InstanceId=instance_id,
            InstanceOSUser=login_name,
            SSHPublicKey=public_key,
        )

        if not result["Success"]:
            logger.error("Failed to send SSH Key to %s", instance_id)
            sys.exit(1)
