#!/usr/bin/env python3

# Convenience wrapper around 'aws ssm start-session'
# can resolve instance id from Name tag, hostname, IP address, etc.
#
# See https://aws.nz/aws-utils/ssm-session for more info.
#
# Author: Michael Ludvig (https://aws.nz)

# The script can list available instances, resolve instance names,
# and host names, etc. In the end it executes 'aws' to actually
# start the session.

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

    add_general_parameters(parser)

    group_instance = parser.add_argument_group('Instance Selection')
    group_instance.add_argument('INSTANCE', nargs='?', help='Instance ID, Name, Host name or IP address')
    group_instance.add_argument('--list', '-l', dest='list', action="store_true", help='List instances available for SSM Session')

    parser.description = 'Start SSM Shell Session to a given instance'
    parser.epilog = f'''
IMPORTANT: instances must be registered in AWS Systems Manager (SSM)
before you can start a shell session! Instances not registered in SSM
will not be recognised by {parser.prog} nor show up in --list output.

Visit https://aws.nz/aws-utils/ssm-session for more info and usage examples.

Author: Michael Ludvig
'''

    # Parse supplied arguments
    args, extras = parser.parse_known_args(argv)

    # If --version do it now and exit
    if args.show_version:
        show_version(args)

    # Require exactly one of INSTANCE or --list
    if bool(args.INSTANCE) + bool(args.list) != 1:
        parser.error("Specify either INSTANCE or --list")

    return args, extras

def start_session(instance_id, extras, profile=None, region=None):
    extra_args = ""
    if profile:
        extra_args += f"--profile {profile} "
    if region:
        extra_args += f"--region {region} "

    parameters = " ".join(extras)

    command = f'aws {extra_args} ssm start-session --target {instance_id} {parameters}'
    logger.info("Running: %s", command)
    os.system(command)

def main():
    ## Split command line to main args and optional command to run
    args, extras = parse_args(sys.argv[1:])

    logger = configure_logging("ssm-session", args.log_level)

    try:
        instance = None
        if args.list:
            InstanceResolver(args).print_list()
            quit(0)

        instance_id = InstanceResolver(args).resolve_instance(args.INSTANCE)

        if not instance_id:
            logger.warning("Could not resolve Instance ID for '%s'", args.INSTANCE)
            logger.warning("Perhaps the '%s' is not registered in SSM?", args.INSTANCE)
            quit(1)

        start_session(instance_id, extras, profile=args.profile, region=args.region)

    except (botocore.exceptions.BotoCoreError,
            botocore.exceptions.ClientError) as e:
        logger.error(e)
        quit(1)

if __name__ == "__main__":
    main()
