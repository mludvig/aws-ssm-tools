#!/usr/bin/env python3

# Open SSH connections through AWS Session Manager
#
# See https://aws.nz/aws-utils/ec2-ssh for more info.
#
# Author: Michael Ludvig (https://aws.nz)

# The script can list available instances, resolve instance names,
# and host names, etc. In the end it executes 'ssh' with the correct
# parameters to actually start the SSH session.

import os
import sys
import time
import logging
import argparse

from typing import Tuple, List

import botocore.exceptions

from .common import add_general_parameters, show_version, configure_logging, verify_plugin_version
from .resolver import InstanceResolver
from .ec2_instance_connect import EC2InstanceConnectHelper

logger = logging.getLogger("ssm-tools.ec2-ssh")


def parse_args(argv: list) -> Tuple[argparse.Namespace, List[str]]:
    """
    Parse command line arguments.
    """

    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False)

    add_general_parameters(parser, long_only=True)

    group_instance = parser.add_argument_group("Instance Selection")
    group_instance.add_argument("--list", dest="list", action="store_true", help="List instances available for SSM Session")

    group_ec2ic = parser.add_argument_group("EC2 Instance Connect")
    group_ec2ic.add_argument("--send-key", action="store_true", help="Send the SSH key to instance metadata using EC2 Instance Connect")

    parser.description = "Open SSH connection through Session Manager"
    parser.epilog = f"""
IMPORTANT: instances must be registered in AWS Systems Manager (SSM)
before you can start a shell session! Instances not registered in SSM
will not be recognised by {parser.prog} nor show up in --list output.

Visit https://aws.nz/aws-utils/ec2-ssh for more info and usage examples.

Author: Michael Ludvig
"""

    # Parse supplied arguments
    args, extra_args = parser.parse_known_args(argv)

    # If --version do it now and exit
    if args.show_version:
        show_version(args)

    # Require exactly one of INSTANCE or --list
    if bool(extra_args) + bool(args.list) != 1:
        parser.error("Specify either --list or SSH Options including instance name")

    return args, extra_args


def start_ssh_session(ssh_args: list, profile: str = None, region: str = None) -> None:
    aws_args = ""
    if profile:
        aws_args += f"--profile {profile} "
    if region:
        aws_args += f"--region {region} "
    proxy_option = ["-o", f"ProxyCommand=aws {aws_args} ssm start-session --target %h --document-name AWS-StartSSHSession --parameters portNumber=%p"]
    command = ["ssh"] + proxy_option + ssh_args
    logger.debug("Running: %s", command)
    os.execvp(command[0], command)


def main() -> int:
    ## Deprecate old script name
    if sys.argv[0].endswith("/ssm-ssh"):
        print('\033[31;1mWARNING:\033[33;1m "ssm-session" has been renamed to "ec2-session" - please update your scripts.\033[0m', file=sys.stderr)
        time.sleep(3)
        print(file=sys.stderr)

    ## Split command line to main args and optional command to run
    args, extra_args = parse_args(sys.argv[1:])

    if args.log_level == logging.DEBUG:
        extra_args.append("-v")

    configure_logging(args.log_level)

    if not verify_plugin_version("1.1.23", logger):
        sys.exit(1)

    try:
        instance_resolver = InstanceResolver(args)

        if args.list:
            instance_resolver.print_list()
            sys.exit(0)

        # Loop through all SSH args to find:
        # - instance name
        # - user name (for use with --send-key)
        # - key name (for use with --send-key)
        ssh_args = []
        instance_id = ""
        login_name = ""
        key_file_name = ""

        extra_args_iter = iter(extra_args)
        for arg in extra_args_iter:
            # User name argument
            if arg.startswith("-l"):
                ssh_args.append(arg)
                if len(arg) > 2:
                    login_name = arg[2:]
                else:
                    login_name = next(extra_args_iter)
                    ssh_args.append(login_name)
                continue

            # SSH key argument
            if arg.startswith("-i"):
                ssh_args.append(arg)
                if len(arg) > 2:
                    key_file_name = arg[2:]
                else:
                    key_file_name = next(extra_args_iter)
                    ssh_args.append(key_file_name)
                continue

            # If we already have instance id just copy the args
            if instance_id:
                ssh_args.append(arg)
                continue

            # Some args that can't be an instance name
            if arg.startswith("-") or arg.find(":") > -1 or arg.find(os.path.sep) > -1:
                ssh_args.append(arg)
                continue

            # This may be an instance name - try to resolve it
            maybe_login_name = None
            if arg.find("@") > -1:  # username@hostname format
                maybe_login_name, instance = arg.split("@", 1)
            else:
                instance = arg

            instance_id, _ = instance_resolver.resolve_instance(instance)
            if not instance_id:
                # Not resolved as an instance name - put back to args
                ssh_args.append(arg)
                maybe_login_name = None
                continue

            # We got a login name from 'login_name@instance'
            if maybe_login_name:
                login_name = maybe_login_name

            # Woohoo we've got an instance id!
            logger.info("Resolved instance name '%s' to '%s'", instance, instance_id)
            ssh_args.append(instance_id)

            if login_name:
                ssh_args.extend(["-l", login_name])

        if not instance_id:
            logger.warning("Could not resolve Instance ID for '%s'", instance)
            logger.warning("Perhaps the '%s' is not registered in SSM?", instance)
            sys.exit(1)

        if args.send_key:
            EC2InstanceConnectHelper(args).send_ssh_key(instance_id, login_name, key_file_name)

        start_ssh_session(ssh_args=ssh_args, profile=args.profile, region=args.region)

    except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError) as e:
        logger.error(e)
        sys.exit(1)

    return 0


if __name__ == "__main__":
    main()
