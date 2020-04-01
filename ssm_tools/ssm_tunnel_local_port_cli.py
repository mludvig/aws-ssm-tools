#!/usr/bin/env python3

# Set up Port forward tunnel to a SSM-enabled instance.
#
# Author: James Baker

import os
import logging
import argparse
import botocore.exceptions
import json

from .common import *
from .resolver import InstanceResolver

logger = logging.getLogger()


def parse_args():
    """
    Parse command line arguments.
    """

    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False
    )

    group_general = add_general_parameters(parser)

    group_instance = parser.add_argument_group("Instance Selection")
    group_instance.add_argument(
        "INSTANCE", nargs="?", help="Instance ID, Name, Host name or IP address"
    )
    group_instance.add_argument(
        "--list",
        "-l",
        dest="list",
        action="store_true",
        help="List instances registered in SSM.",
    )

    group_network = parser.add_argument_group("Networking Options")
    group_network.add_argument(
        "--port",
        metavar="PORT",
        dest="port",
        type=str,
        help="Port to route through ssm tunnel.",
    )

    parser.description = "Start port tunnel to a given SSM instance"
    parser.epilog = f"""
IMPORTANT: instances must be registered in AWS Systems Manager (SSM)
before you can copy files to/from them! Instances not registered in SSM
will not be recognised by {parser.prog} nor show up in --list output.

Author: James Baker
"""

    # Parse supplied arguments
    args = parser.parse_args()

    # If --version do it now and exit
    if args.show_version:
        show_version(args)

    # Require exactly one of INSTANCE or --list
    if bool(args.INSTANCE) + bool(args.list) != 1:
        parser.error("Specify either INSTANCE or --list")

    return args


def start_portfwd_session(instance_id, port, profile=None, region=None):
    extra_args = ""
    if profile:
        extra_args += f"--profile {profile} "
    if region:
        extra_args += f"--region {region} "

    data = {}
    data["portNumber"] = [port]
    data["localPortNumber"] = [port]
    json_data = json.dumps(data)
    command = f"aws {extra_args} ssm start-session --target {instance_id} --document-name AWS-StartPortForwardingSession --parameters '{json_data}'"
    logger.info("Running: %s", command)
    os.system(command)


def main():
    args = parse_args()

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

        start_portfwd_session(
            instance_id, port=args.port, profile=args.profile, region=args.region,
        )

    except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError) as e:
        logger.error(e)
        quit(1)


if __name__ == "__main__":
    main()
