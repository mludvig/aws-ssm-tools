#!/usr/bin/env python3

# Convenience wrapper around 'aws ssm start-session' for port forwarding.
# Supports both local port forwarding and remote host forwarding.
#
# See https://github.com/mludvig/aws-ssm-tools for more info.
#
# Author: Michael Ludvig (https://aws.nz)

import argparse
import logging
import os
import sys

import botocore.exceptions

from .common import add_general_parameters, configure_logging, handle_boto_error, show_version, target_selector
from .resolver import InstanceResolver

logger = logging.getLogger("ssm-tools.ssm-port-forward")


def parse_args(argv: list) -> argparse.Namespace:
    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False)

    add_general_parameters(parser)

    group_instance = parser.add_argument_group("Instance Selection")
    group_instance.add_argument("INSTANCE", nargs="?", help="Instance ID, Name, Host name or IP address")
    group_instance.add_argument(
        "--list",
        "-l",
        dest="list",
        action="store_true",
        help="List instances available for SSM Session",
    )

    group_fwd = parser.add_argument_group("Port Forwarding")
    group_fwd.add_argument(
        "--local-port",
        "-L",
        dest="local_port",
        metavar="PORT",
        required=False,
        help="Local port to listen on. If omitted, same as --remote-port.",
    )
    group_fwd.add_argument(
        "--remote-port",
        dest="remote_port",
        metavar="PORT",
        required=False,
        help="Remote port to forward to (on the instance, or on --remote-host).",
    )
    group_fwd.add_argument(
        "--remote-host",
        "-H",
        dest="remote_host",
        metavar="HOST",
        help="Forward to this host instead of the SSM instance itself (e.g. an RDS endpoint). "
        "Uses AWS-StartPortForwardingSessionToRemoteHost document.",
    )
    group_fwd.add_argument("--reason", "-r", help="The reason for the session.")

    parser.description = "Port-forward through an SSM Session to an EC2 instance"
    parser.epilog = f"""
Examples:
  # RDP to a Windows instance
  {parser.prog} my-instance --remote-port 3389 --local-port 13389

  # Forward to an RDS instance in the same VPC
  {parser.prog} my-bastion --remote-port 5432 --remote-host my-db.cluster.rds.amazonaws.com

IMPORTANT: instances must be registered in AWS Systems Manager (SSM)
before you can use port forwarding.

Author: Michael Ludvig
"""

    args = parser.parse_args(argv)

    if args.show_version:
        show_version(args)

    if not args.list and not args.remote_port:
        parser.error("--remote-port is required")

    if not args.local_port:
        args.local_port = args.remote_port

    return args


def start_port_forward(instance_id: str, args: argparse.Namespace) -> None:
    exec_args = ["aws", "ssm", "start-session"]
    if args.profile:
        exec_args += ["--profile", args.profile]
    if args.region:
        exec_args += ["--region", args.region]
    if args.reason:
        exec_args += ["--reason", args.reason]

    if args.remote_host:
        exec_args += [
            "--document-name",
            "AWS-StartPortForwardingSessionToRemoteHost",
            "--parameters",
            f"portNumber={args.remote_port},localPortNumber={args.local_port},host={args.remote_host}",
        ]
    else:
        exec_args += [
            "--document-name",
            "AWS-StartPortForwardingSession",
            "--parameters",
            f"portNumber={args.remote_port},localPortNumber={args.local_port}",
        ]

    exec_args += ["--target", instance_id]
    logger.debug("Running: %s", exec_args)

    if os.name == "nt":
        # os.execvp doesn't replace the process on Windows; use subprocess instead
        import subprocess

        try:
            sys.exit(subprocess.call(exec_args))
        except KeyboardInterrupt:
            sys.exit(0)
    else:
        os.execvp(exec_args[0], exec_args)


def main() -> int:
    args = parse_args(sys.argv[1:])

    configure_logging(args.log_level)

    try:
        if args.list:
            InstanceResolver(args).print_list()
            sys.exit(0)

        if not args.INSTANCE:
            headers, session_details = InstanceResolver(args).print_list(quiet=True)
            selected_session = target_selector(headers, session_details)
            instance_id = selected_session["InstanceId"]
        else:
            instance_id, _ = InstanceResolver(args).resolve_instance(args.INSTANCE)

        if not instance_id:
            logger.warning("Could not resolve Instance ID for '%s'", args.INSTANCE)
            logger.warning("Perhaps the '%s' is not registered in SSM?", args.INSTANCE)
            sys.exit(1)

        start_port_forward(instance_id, args)

    except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError) as e:
        handle_boto_error(e, logger, args.profile)
        sys.exit(1)

    return 0


if __name__ == "__main__":
    main()
