#!/usr/bin/env python3

# Open SSH connections through AWS Session Manager
#
# See https://aws.nz/aws-utils/ec2-ssh for more info.
#
# Author: Michael Ludvig (https://aws.nz)

# The script can list available instances, resolve instance names,
# and host names, etc. In the end it executes 'ssh' with the correct
# parameters to actually start the SSH session.

import argparse
import dataclasses
import logging
import os
import sys

import botocore.exceptions

from .common import (
    add_general_parameters,
    configure_logging,
    handle_boto_error,
    show_version,
    target_selector,
    verify_awscli_version,
    verify_plugin_version,
)
from .ec2_instance_connect import EC2InstanceConnectHelper
from .resolver import InstanceResolver

logger = logging.getLogger("ssm-tools.ec2-ssh")

MIN_PLUGIN_VERSION = "1.1.23"  # AWS-StartSSHSession support
MIN_AWSCLI_VERSION_ENDPOINT = "2.12.0"  # aws ec2-instance-connect open-tunnel

# ssh(1) option letters without and with a value, as in OpenSSH's getopt() string
SSH_FLAGS = "46AaCfGgKkMNnqsTtVvXxYy"
SSH_VALUE_FLAGS = "BbcDEeFIiJLlmOoPpQRSWw"


@dataclasses.dataclass
class SshArgs:
    """The ssh command line: options (one flag and its value per token), destination and remote command."""

    options: list[str] = dataclasses.field(default_factory=list)
    destination: str = ""
    command: list[str] = dataclasses.field(default_factory=list)

    def option(self, flag: str) -> str:
        """Return the first value of a value option, e.g. option("-l"), or ''."""
        options = iter(self.options)
        for option in options:
            if option[1] in SSH_VALUE_FLAGS:
                value = next(options)
                if option == flag:
                    return value
        return ""


def split_ssh_argv(argv: list[str], own_options: dict[str, bool]) -> tuple[list[str], SshArgs]:
    """
    Split argv the way ssh(1) parses it: options before and after the destination, up to
    the first word after it, which starts the remote command. '--' ends the options.

    Long options and the short ones in 'own_options' ({option: takes_value}) are ours,
    everything else is ssh's. The remote command is never looked at.
    Raises ValueError for an option without its value.
    See docs/adr/0003-ec2-ssh-argv-parsing.md
    """
    own: list[str] = []
    ssh = SshArgs()
    args = list(argv)

    def value(option: str) -> str:
        if not args:
            raise ValueError(f"option {option} requires a value")
        return args.pop(0)

    while args:
        arg = args.pop(0)

        if arg == "--":
            if not ssh.destination and args:
                ssh.destination = args.pop(0)
            ssh.command = args
            break

        if arg.startswith("--"):
            option = arg.split("=", 1)[0]
            own.append(arg)
            if own_options.get(option) and "=" not in arg:
                own.append(value(option))
            continue

        if not arg.startswith("-") or arg == "-":
            if ssh.destination:
                ssh.command = [arg, *args]
                break
            ssh.destination = arg
            continue

        # A cluster of short options, e.g. -tt, -p2222 or -vl user
        for pos, letter in enumerate(arg[1:], 2):
            option = "-" + letter
            is_own = option in own_options
            target = own if is_own else ssh.options
            target.append(option)
            if own_options[option] if is_own else letter in SSH_VALUE_FLAGS:
                target.append(arg[pos:] or value(option))
                break

    return own, ssh


def parse_args(argv: list) -> tuple[argparse.Namespace, SshArgs]:
    """
    Parse command line arguments.
    """

    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
        allow_abbrev=False,
    )

    add_general_parameters(parser, long_only=True)

    group_instance = parser.add_argument_group("Instance Selection")
    group_instance.add_argument(
        "--list",
        dest="list",
        action="store_true",
        help="List instances available for SSM Session",
    )

    group_ec2ic = parser.add_argument_group("EC2 Instance Connect")
    group_ec2ic.add_argument("--reason", help="The reason for connecting to the instance.")
    group_ec2ic.add_argument(
        "--user",
        dest="user",
        metavar="USER",
        help="USER after opening the session.",
    )
    group_ec2ic.add_argument(
        "--no-send-key",
        dest="send_key",
        action="store_false",
        help="Send the SSH key to instance metadata using EC2 Instance Connect",
    )
    group_ec2ic.add_argument(
        "--use-endpoint",
        dest="use_endpoint",
        action="store_true",
        default=False,
        help="Connect using 'EC2 Instance Connect Endpoint'",
    )

    parser.description = "Open SSH connection through Session Manager"
    parser.epilog = f"""
IMPORTANT: instances must be registered in AWS Systems Manager (SSM)
before you can start a shell session! Instances not registered in SSM
will not be recognised by {parser.prog} nor show up in --list output.

Visit https://aws.nz/aws-utils/ec2-ssh for more info and usage examples.

Author: Michael Ludvig
"""

    # Our options are only taken from before the remote command, the rest is for ssh
    own_options = {option: action.nargs != 0 for action in parser._actions for option in action.option_strings}
    try:
        own_argv, ssh_args = split_ssh_argv(argv, own_options)
    except ValueError as e:
        parser.error(str(e))
    args = parser.parse_args(own_argv)

    # If --version do it now and exit
    if args.show_version:
        show_version(args)

    return args, ssh_args


def start_ssh_session(ssh_args: SshArgs, profile: str, region: str, use_endpoint: bool, reason: str = "") -> None:
    aws_args = ""
    if profile:
        aws_args += f"--profile {profile} "
    if region:
        aws_args += f"--region {region} "
    if reason:
        aws_args += f"--reason '{reason}' "

    if use_endpoint:
        proxy_option = ["-o", f"ProxyCommand=aws {aws_args} ec2-instance-connect open-tunnel --instance-id %h"]
    else:
        proxy_option = [
            "-o",
            f"ProxyCommand=aws {aws_args} ssm start-session --target %h --document-name AWS-StartSSHSession --parameters portNumber=%p",
        ]
    # Nothing after the destination may be read as an ssh option
    command = ["ssh", *proxy_option, *ssh_args.options, "--", ssh_args.destination, *ssh_args.command]
    logger.debug("Running: %s", command)
    os.execvp(command[0], command)


def main() -> int:
    args, ssh_args = parse_args(sys.argv[1:])

    if args.log_level == logging.DEBUG:
        ssh_args.options.insert(0, "-v")

    configure_logging(args.log_level)

    try:
        instance_resolver = InstanceResolver(args)

        if args.list:
            instance_resolver.print_list()
            sys.exit(0)

        if args.use_endpoint and not verify_awscli_version(MIN_AWSCLI_VERSION_ENDPOINT, logger):
            logger.error(
                "AWS CLI v%s or newer is required for --use-endpoint, falling back to SSM Session Manager",
                MIN_AWSCLI_VERSION_ENDPOINT,
            )
            args.use_endpoint = False

        if not args.use_endpoint and not verify_plugin_version(logger, required=MIN_PLUGIN_VERSION):
            sys.exit(1)

        login_name, _, host = ssh_args.destination.rpartition("@")  # OpenSSH splits on the last '@'
        if host:
            instance_id, _ = instance_resolver.resolve_instance(host)
        else:
            headers, session_details = instance_resolver.print_list(quiet=True)
            instance_id = target_selector(headers, session_details)["InstanceId"]

        if instance_id:
            logger.debug("Resolved instance name '%s' to '%s'", host, instance_id)
            ssh_args.destination = f"{login_name}@{instance_id}" if login_name else instance_id
            if args.send_key:
                EC2InstanceConnectHelper(args).send_ssh_key(
                    instance_id,
                    login_name or ssh_args.option("-l"),
                    ssh_args.option("-i"),
                )
        else:
            # E.g. an ssh_config alias, let ssh resolve it
            logger.info("No instance found for '%s', passing it to ssh as is", host)

        start_ssh_session(
            ssh_args=ssh_args,
            profile=args.profile,
            region=args.region,
            use_endpoint=args.use_endpoint,
            reason=args.reason,
        )

    except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError) as e:
        handle_boto_error(e, logger, args.profile)
        sys.exit(1)

    return 0


if __name__ == "__main__":
    main()
