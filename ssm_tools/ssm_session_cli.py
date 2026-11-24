#!/usr/bin/env python3

# Convenience wrapper around Session Manager's StartSession
# can resolve instance id from Name tag, hostname, IP address, etc.
#
# See https://aws.nz/aws-utils/ssm-session for more info.
#
# Author: Michael Ludvig (https://aws.nz)

# The script can list available instances, resolve instance names,
# and host names, etc. In the end it runs session-manager-plugin to
# actually start the session.

import argparse
import json
import logging
import os
import shlex
import signal
import sys
from typing import Any

import botocore.exceptions

from .common import (
    ESCAPE_HELP,
    add_general_parameters,
    configure_logging,
    handle_boto_error,
    session_reason,
    show_version,
    unique_login,
    verify_plugin_version,
)
from .picker import target_selector
from .plugin import PLUGIN_ENV_VERSION, run_plugin
from .resolver import InstanceResolver

logger = logging.getLogger("ssm-tools.ec2-session")


def parse_args(argv: list) -> argparse.Namespace:
    """
    Parse command line arguments.
    """

    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False)

    add_general_parameters(parser)

    group_instance = parser.add_argument_group("Instance Selection")
    group_instance.add_argument(
        "INSTANCE",
        nargs="?",
        metavar="[USER@]INSTANCE",
        help="Instance ID, Name, Host name or IP address",
    )
    group_instance.add_argument(
        "--list",
        dest="list",
        action="store_true",
        help="List instances available for SSM Session",
    )

    group_session = parser.add_argument_group("Session Parameters")
    group_session.add_argument(
        "--user",
        "-u",
        "-l",
        "--sudo",
        dest="user",
        metavar="USER",
        action="append",
        default=[],
        help="SUDO to USER after opening the session, the same as USER@INSTANCE. Linux only. "
        "Can't be used together with --document-name / --parameters. (optional)",
    )
    group_session.add_argument("--reason", "-r", type=session_reason, help="The reason for connecting to the instance.")
    group_session.add_argument(
        "--command",
        "-c",
        dest="command",
        metavar="COMMAND",
        help="Command to run in the SSM Session. Can't be used together with --user. "
        "If you need to run the COMMAND as a different USER prepend the command with the appropriate 'sudo -u USER ...'. (optional)",
    )
    group_session.add_argument(
        "--document-name",
        dest="document_name",
        help="Document to execute, e.g. AWS-StartInteractiveCommand (optional)",
    )
    group_session.add_argument(
        "--parameters",
        dest="parameters",
        help='Parameters for the --document-name in JSON, e.g. \'{"command": ["sudo -i -u ec2-user"]}\' (optional)',
    )

    parser.description = "Start SSM Shell Session to an EC2 instance"
    parser.epilog = f"""
IMPORTANT: instances must be registered in AWS Systems Manager (SSM)
before you can start a shell session! Instances not registered in SSM
will not be recognised by {parser.prog} nor show up in --list output.
{ESCAPE_HELP}
Visit https://aws.nz/aws-utils/ssm-session for more info and usage examples.

Author: Michael Ludvig
"""

    # Parse supplied arguments
    args = parser.parse_args(argv)

    # If --version do it now and exit
    if args.show_version:
        show_version(args)

    # USER@INSTANCE splits on the last '@' like ssh, see docs/adr/0006-one-login-syntax.md
    login, _, args.INSTANCE = (args.INSTANCE or "").rpartition("@")
    try:
        args.user = unique_login(*args.user, login)
    except ValueError as e:
        parser.error(str(e))

    if args.parameters and not args.document_name:
        parser.error("--parameters can only be used together with --document-name")
    if args.parameters:
        try:
            args.parameters = json.loads(args.parameters)
        except ValueError as e:
            parser.error(f'--parameters must be JSON, e.g. \'{{"command": ["top"]}}\': {e}')

    if bool(args.user) + bool(args.command) + bool(args.document_name) > 1:
        parser.error(
            """
Use only one of --user / --command / --document-name
If you need to run the COMMAND as a specific USER then prepend
the command with the appropriate: sudo -i -u USER COMMAND
""",
        )

    return args


def start_session(ssm_client: Any, instance_id: str, args: argparse.Namespace) -> int:
    parameters: dict[str, Any] = {"Target": instance_id}
    if args.reason:
        parameters["Reason"] = args.reason

    # Run --user / --command through AWS-StartInteractiveCommand
    command = f"sudo -i -u {shlex.quote(args.user)}" if args.user else args.command
    if command:
        parameters["DocumentName"] = "AWS-StartInteractiveCommand"
        parameters["Parameters"] = {"command": [command]}
    else:
        # Or use the provided values
        if args.document_name:
            parameters["DocumentName"] = args.document_name
        if args.parameters:
            parameters["Parameters"] = args.parameters

    logger.debug("Starting session: %s", parameters)
    return run_plugin(ssm_client, ssm_client.start_session(**parameters), parameters, args.profile)


def main() -> int:
    # Only on Linux / Unix / Mac because Windows don't interpret Ctrl-Z
    if os.name == "posix":
        # Ignore Ctrl-Z - pass it to the shell
        signal.signal(signal.SIGTSTP, signal.SIG_IGN)

    ## Split command line to main args and optional command to run
    args = parse_args(sys.argv[1:])

    configure_logging(args.log_level)

    try:
        instance_resolver = InstanceResolver(args)

        if args.list:
            instance_resolver.print_list()
            sys.exit(0)

        if not verify_plugin_version(logger, required=PLUGIN_ENV_VERSION, interactive=True):
            sys.exit(1)

        if not args.INSTANCE:
            headers, session_details = instance_resolver.print_list(quiet=True)
            selected_session = target_selector(headers, session_details)
            instance_id = selected_session["InstanceId"]
        else:
            instance_id, _ = instance_resolver.resolve_instance(args.INSTANCE)

        if not instance_id:
            logger.warning("Could not resolve Instance ID for '%s'", args.INSTANCE)
            logger.warning("Perhaps the '%s' is not registered in SSM?", args.INSTANCE)
            sys.exit(1)

        # PowerShell has no sudo
        if args.user and instance_resolver.platform_type(instance_id) == "Windows":
            logger.error("--user is not supported on Windows instances")
            sys.exit(1)

        return start_session(instance_resolver.ssm_client, instance_id, args)

    except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError) as e:
        handle_boto_error(e, logger, args.profile)
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
