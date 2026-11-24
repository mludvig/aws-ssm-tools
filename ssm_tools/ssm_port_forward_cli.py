#!/usr/bin/env python3

# Convenience wrapper around Session Manager's StartSession for port forwarding.
# Supports both local port forwarding and remote host forwarding.
#
# See https://github.com/mludvig/aws-ssm-tools for more info.
#
# Author: Michael Ludvig (https://aws.nz)

import argparse
import logging
import os
import socket
import sys
from collections.abc import Callable
from typing import Any

import botocore.exceptions

from .common import (
    add_general_parameters,
    configure_logging,
    handle_boto_error,
    session_reason,
    show_version,
    verify_plugin_version,
)
from .picker import target_selector
from .plugin import PLUGIN_ENV_VERSION, run_plugin
from .resolver import InstanceResolver, instance_not_found

logger = logging.getLogger("ssm-tools.ssm-port-forward")


def port_number(lowest: int) -> Callable[[str], int]:
    """argparse type for a port from 'lowest' to 65535. See docs/adr/0014-validate-before-start-session.md"""

    def port(value: str) -> int:
        try:
            if lowest <= int(value) <= 65535:
                return int(value)
        except ValueError:
            pass
        raise argparse.ArgumentTypeError(f"{value} isn't a port number from {lowest} to 65535")

    return port


def parse_args(argv: list) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ssm-port-forward",  # Python 3.14 on Windows says "python.exe C:\...\Scripts\ssm-port-forward"
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )

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
        type=port_number(0),
        required=False,
        help="Local port to listen on, 0 for any free one. If omitted, same as --remote-port.",
    )
    group_fwd.add_argument(
        "--remote-port",
        dest="remote_port",
        metavar="PORT",
        type=port_number(1),
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
    group_fwd.add_argument("--reason", type=session_reason, help="The reason for the session.")

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

    if args.local_port is None:
        args.local_port = args.remote_port

    return args


def check_local_port(port: int) -> None:
    """Exit if we can't listen on the port. Else the plugin waits silently. See docs/adr/0014-validate-before-start-session.md"""
    if port == 0:  # The plugin picks a free one
        return
    with socket.socket() as sock:
        if os.name != "nt":  # Like the plugin's listener. On Windows it would let us share a port in use.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as e:
            logger.error("Can't listen on local port %d: %s. Choose another with --local-port.", port, e.strerror)
            sys.exit(1)


def start_port_forward(ssm_client: Any, instance_id: str, args: argparse.Namespace) -> int:
    parameters: dict[str, Any] = {
        "Target": instance_id,
        "DocumentName": "AWS-StartPortForwardingSession",
        "Parameters": {"portNumber": [str(args.remote_port)], "localPortNumber": [str(args.local_port)]},
    }
    if args.remote_host:
        parameters["DocumentName"] = "AWS-StartPortForwardingSessionToRemoteHost"
        parameters["Parameters"]["host"] = [args.remote_host]
    if args.reason:
        parameters["Reason"] = args.reason

    logger.debug("Starting session: %s", parameters)
    return run_plugin(ssm_client, ssm_client.start_session(**parameters), parameters, args.profile)


def main() -> int:
    args = parse_args(sys.argv[1:])

    configure_logging(args.log_level)

    try:
        instance_resolver = InstanceResolver(args)

        if args.list:
            instance_resolver.print_list()
            sys.exit(0)

        if not verify_plugin_version(logger, required=PLUGIN_ENV_VERSION):
            sys.exit(1)

        check_local_port(args.local_port)

        if not args.INSTANCE:
            headers, session_details = instance_resolver.print_list(quiet=True)
            selected_session = target_selector(headers, session_details)
            instance_id = selected_session["InstanceId"]
        else:
            instance_id, _ = instance_resolver.resolve_instance(args.INSTANCE)

        if not instance_id:
            instance_not_found(args.INSTANCE)

        return start_port_forward(instance_resolver.ssm_client, instance_id, args)

    except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError) as e:
        handle_boto_error(e, logger, args.profile)
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
