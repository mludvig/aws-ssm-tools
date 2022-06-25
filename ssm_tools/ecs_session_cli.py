#!/usr/bin/env python3

# Convenience wrapper around 'aws ecs execute-command'
#
# See https://aws.nz/aws-utils/ecs-session for more info.
#
# Author: Michael Ludvig (https://aws.nz)

# The script can list the available containers across all your ECS clusters.
# In the end it executes 'aws ecs execute-command' with the appropriate parameters.
# Supports both EC2 and Fargate ECS tasks.

import os
import sys
import logging
import signal
import argparse
import botocore.exceptions

from .common import *
from .resolver import ContainerResolver

logger = logging.getLogger()

def parse_args(argv):
    """
    Parse command line arguments.
    """

    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False)

    add_general_parameters(parser)

    group_container = parser.add_argument_group('Container Selection')
    group_container.add_argument('CONTAINER', nargs='?', help='Task ID, Container Name or IP address')
    group_container.add_argument('--list', '-l', dest='list', action="store_true", help='List available containers configured for ECS RunTask')
    group_container.add_argument('--cluster', dest='cluster', metavar='CLUSTER', help='Specify an ECS cluster. (optional)')

    group_session = parser.add_argument_group('Session Parameters')
    group_session.add_argument('--command', dest='command', metavar='COMMAND', default='/bin/sh', help='Command to run inside the container. Default: /bin/sh')

    parser.description = 'Execute "ECS Run Task" on a given container'
    parser.epilog = f'''
IMPORTANT: containers must have "execute-command" setting enabled or they
will not be recognised by {parser.prog} nor show up in --list output.

Visit https://aws.nz/aws-utils/ecs-session for more info and usage examples.

Author: Michael Ludvig
'''

    # Parse supplied arguments
    args, extras = parser.parse_known_args(argv)

    # If --version do it now and exit
    if args.show_version:
        show_version(args)

    # Require exactly one of CONTAINER or --list
    if bool(args.CONTAINER) + bool(args.list) != 1:
        parser.error("Specify either CONTAINER or --list")

    return args, extras

def start_session(container, args, command):
    exec_args = [ "aws", "ecs", "execute-command" ]
    if args.profile:
        exec_args += ["--profile", args.profile]
    if args.region:
        exec_args += ["--region", args.region]

    exec_args += [
        "--cluster", container["cluster_arn"],
        "--task", container["task_arn"],
        "--container", container["container_name"],
        "--command", command, "--interactive",
    ]

    logger.info("Running: %s", exec_args)
    os.execvp(exec_args[0], exec_args)

def main():
    ## Split command line to main args and optional command to run
    args, extras = parse_args(sys.argv[1:])

    global logger
    logger = configure_logging("ecs-session", args.log_level)

    try:
        if args.list:
            ContainerResolver(args).print_list()
            quit(0)

        container = ContainerResolver(args).resolve_container(args.CONTAINER)

        if not container:
            logger.warning("Could not find any container matching '%s'", args.CONTAINER)
            quit(1)

        start_session(container, args, args.command)

    except (botocore.exceptions.BotoCoreError,
            botocore.exceptions.ClientError) as e:
        logger.error(e)
        quit(1)

if __name__ == "__main__":
    main()
