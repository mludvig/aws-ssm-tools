#!/usr/bin/env python3

# Open SSH connections through AWS Session Manager
#
# See https://aws.nz/aws-utils/ssm-ssh for more info.
#
# Author: Michael Ludvig (https://aws.nz)

# The script can list available instances, resolve instance names,
# and host names, etc. In the end it executes 'ssh' with the correct
# parameters to actually start the SSH session.

import os
import sys
import logging
import argparse
import botocore.exceptions

from .common import *
from .resolver import InstanceResolver

logger = logging.getLogger()

def parse_args(argv):
    """
    Parse command line arguments.
    """

    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False)

    add_general_parameters(parser, long_only=True)

    group_instance = parser.add_argument_group('Instance Selection')
    group_instance.add_argument('--list', dest='list', action="store_true", help='List instances available for SSM Session')

    parser.description = 'Open SSH connection through Session Manager'
    parser.epilog = f'''
IMPORTANT: instances must be registered in AWS Systems Manager (SSM)
before you can start a shell session! Instances not registered in SSM
will not be recognised by {parser.prog} nor show up in --list output.

Visit https://aws.nz/aws-utils/ssm-ssh for more info and usage examples.

Author: Michael Ludvig
'''

    # Parse supplied arguments
    args, extra_args = parser.parse_known_args(argv)

    # If --version do it now and exit
    if args.show_version:
        show_version(args)

    # Require exactly one of INSTANCE or --list
    if bool(extra_args) + bool(args.list) != 1:
        parser.error("Specify either --list or SSH Options including instance name")

    return args, extra_args

def start_ssh_session(ssh_args, profile=None, region=None):
    aws_args = ""
    if profile:
        aws_args += f"--profile {profile} "
    if region:
        aws_args += f"--region {region} "
    proxy_option = f"-o ProxyCommand='aws {aws_args} ssm start-session --target %h --document-name AWS-StartSSHSession --parameters portNumber=%p'"
    ssh_args = " ".join([ f"'{arg}'" for arg in ssh_args ])
    command = f'ssh {proxy_option} {ssh_args}'
    logger.info("Running: %s", command)
    os.system(command)

def main():
    global logger

    ## Split command line to main args and optional command to run
    args, extra_args = parse_args(sys.argv[1:])

    if args.log_level == logging.INFO:
        extra_args.append('-v')
    if args.log_level == logging.DEBUG:
        extra_args.append('-vv')

    logger = configure_logging("ssm-ssh", args.log_level)

    if not verify_plugin_version("1.1.23", logger):
        quit(1)

    try:
        instance_resolver = InstanceResolver(args)

        if args.list:
            instance_resolver.print_list()
            quit(0)

        # Loop through all possible instance names and try to resolve them.
        ssh_args = []
        instance_id = None
        for arg in extra_args:
            # If we already have instance id just copy the args
            if instance_id:
                ssh_args.append(arg)
                continue

            # Some args that can't be an instance name
            if arg.startswith('-') or arg.find(':') > -1 or arg.find(os.path.sep) > -1:
                ssh_args.append(arg)
                continue

            # This may be an instance name - try to resolve it
            login_name = None
            if arg.find('@') > -1:  # username@hostname format
                login_name, instance = arg.split('@', 1)
            else:
                instance = arg

            instance_id = instance_resolver.resolve_instance(instance)
            if not instance_id:
                # Not resolved as an instance name - put back to args
                ssh_args.append(arg)
                continue

            # Woohoo we've got an instance id!
            logger.info(f"Resolved instance name '{instance}' to '{instance_id}'")
            ssh_args.append(instance_id)

            if login_name:
                ssh_args.extend(['-l', login_name])

        if not instance_id:
            logger.warning("Could not resolve Instance ID for '%s'", instance)
            logger.warning("Perhaps the '%s' is not registered in SSM?", instance)
            quit(1)

        start_ssh_session(ssh_args=ssh_args, profile=args.profile, region=args.region)

    except (botocore.exceptions.BotoCoreError,
            botocore.exceptions.ClientError) as e:
        logger.error(e)
        quit(1)

if __name__ == "__main__":
    main()
