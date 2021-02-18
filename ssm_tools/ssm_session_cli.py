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
import signal
import argparse
import botocore.exceptions

from .common import *
from .resolver import InstanceResolver

logger = logging.getLogger()

signal.signal(signal.SIGTSTP, signal.SIG_IGN)   # Ignore Ctrl-Z - pass it on to the shell

def parse_args(argv):
    """
    Parse command line arguments.
    """

    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False)

    add_general_parameters(parser)

    group_instance = parser.add_argument_group('Instance Selection')
    group_instance.add_argument('INSTANCE', nargs='?', help='Instance ID, Name, Host name or IP address')
    group_instance.add_argument('--list', '-l', dest='list', action="store_true", help='List instances available for SSM Session')

    group_session = parser.add_argument_group('Session Parameters')
    group_session.add_argument('--user', '-u', '--sudo', dest='user', metavar="USER", help='SUDO to USER after opening the session. Can\'t be used together with --document-name / --parameters. (optional)')
    group_session.add_argument('--document-name', dest='document_name', help='Document to execute, e.g. AWS-StartInteractiveCommand (optional)')
    group_session.add_argument('--parameters', dest='parameters', help='Parameters for the --document-name, e.g. \'command=["sudo -i -u ec2-user"]\' (optional)')

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

    if args.parameters and not args.document_name:
        parser.error("--parameters can only be used together with --document-name")

    if args.user and args.document_name:
            parser.error("--user can't used used together with --document-name")

    return args, extras

def start_session(instance_id, args):
    aws_args = ""
    if args.profile:
        aws_args += f"--profile {args.profile} "
    if args.region:
        aws_args += f"--region {args.region} "

    ssm_args = ""
    if args.user:
        # Fake --document-name / --parameters for --user
        ssm_args += f"--document-name AWS-StartInteractiveCommand --parameters 'command=[\"sudo -i -u {args.user}\"]'"
    else:
        # Or use the provided values
        if args.document_name:
            ssm_args += f"--document-name {args.document_name} "
        if args.parameters:
            ssm_args += f"--parameters {args.parameters} "

    command = f'aws {aws_args} ssm start-session --target {instance_id} {ssm_args}'
    logger.info("Running: %s", command)
    os.system(command)

def main():
    ## Split command line to main args and optional command to run
    args, extras = parse_args(sys.argv[1:])

    global logger
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

        start_session(instance_id, args)

    except (botocore.exceptions.BotoCoreError,
            botocore.exceptions.ClientError) as e:
        logger.error(e)
        quit(1)

if __name__ == "__main__":
    main()
