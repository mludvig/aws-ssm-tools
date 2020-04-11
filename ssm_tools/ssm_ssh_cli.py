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
    group_instance.add_argument('INSTANCE', nargs='?', help='Instance ID, Name, Host name or IP address')
    group_instance.add_argument('--list', dest='list', action="store_true", help='List instances available for SSM Session')

    parser.description = 'Open SSH connection through Session Manager'
    parser.epilog = f'''
IMPORTANT: instances must be registered in AWS Systems Manager (SSM)
before you can start a shell session! Instances not registered in SSM
will not be recognised by {parser.prog} nor show up in --list output.

Visit https://aws.nz/aws-utils/ssm-session for more info and usage examples.

Author: Michael Ludvig
'''

    # Parse supplied arguments
    args, extra_args = parser.parse_known_args(argv)

    # If --version do it now and exit
    if args.show_version:
        show_version(args)

    # Require exactly one of INSTANCE or --list
    if bool(args.INSTANCE) + bool(args.list) != 1:
        parser.error("Specify either INSTANCE or --list")

    return args, extra_args

def start_ssh_session(instance_id, profile=None, region=None, ssh_args=[]):
    extra_args = ""
    if profile:
        extra_args += f"--profile {profile} "
    if region:
        extra_args += f"--region {region} "
    proxy_option = f"-o ProxyCommand='aws {extra_args} ssm start-session --target %h --document-name AWS-StartSSHSession --parameters portNumber=%p'"
    ssh_args = " ".join([ f"'{arg}'" for arg in ssh_args ])
    command = f'ssh {proxy_option} {instance_id} {ssh_args}'
    logger.info("INFO: Running: %s", command)
    logger.info("")
    os.system(command)

def main():
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
        if args.list:
            InstanceResolver(args).print_list()
            quit(0)

        login_name = None
        instance_split = args.INSTANCE.split('@', 1)
        if len(instance_split) > 1:
            login_name, instance = instance_split
            extra_args.append(f"-l{login_name}")
        else:
            instance = instance_split[0]

        instance_id = InstanceResolver(args).resolve_instance(instance)

        if not instance_id:
            logger.warning("Could not resolve Instance ID for '%s'", instance)
            logger.warning("Perhaps the '%s' is not registered in SSM?", instance)
            quit(1)

        start_ssh_session(instance_id, profile=args.profile, region=args.region, ssh_args=extra_args)

    except (botocore.exceptions.BotoCoreError,
            botocore.exceptions.ClientError) as e:
        logger.error(e)
        quit(1)

if __name__ == "__main__":
    main()
