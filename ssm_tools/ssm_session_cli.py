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

import argparse
import logging
import os
import signal
import sys
import time

import botocore.exceptions
from simple_term_menu import TerminalMenu

from .common import add_general_parameters, configure_logging, show_version
from .resolver import InstanceResolver

logger = logging.getLogger("ssm-tools.ec2-session")

# Only on Linux / Unix / Mac because Windows don't interpret Ctrl-Z
if os.name == "posix":
    # Ignore Ctrl-Z - pass it to the shell
    signal.signal(signal.SIGTSTP, signal.SIG_IGN)


def parse_args(argv: list) -> argparse.Namespace:
    """
    Parse command line arguments.
    """

    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False)

    add_general_parameters(parser)

    # fmt: off
    group_instance = parser.add_argument_group("Instance Selection")
    group_instance.add_argument("INSTANCE", nargs="?", help="Instance ID, Name, Host name or IP address")
    group_instance.add_argument("--list", "-l", dest="list", action="store_true", help="List instances available for SSM Session")

    group_session = parser.add_argument_group("Session Parameters")
    group_session.add_argument("--user", "-u", "--sudo", dest="user", metavar="USER", help="SUDO to USER after opening the session. Can't be used together with --document-name / --parameters. (optional)")
    group_session.add_argument("--reason", "-r", help="The reason for connecting to the instance.")
    group_session.add_argument("--command", "-c", dest="command", metavar="COMMAND", help="Command to run in the SSM Session. Can't be used together with --user. "
                               "If you need to run the COMMAND as a different USER prepend the command with the appropriate 'sudo -u USER ...'. (optional)")
    group_session.add_argument("--document-name", dest="document_name", help="Document to execute, e.g. AWS-StartInteractiveCommand (optional)")
    group_session.add_argument("--parameters", dest="parameters", help="Parameters for the --document-name, e.g. 'command=[\"sudo -i -u ec2-user\"]' (optional)")
    # fmt: on

    parser.description = "Start SSM Shell Session to an EC2 instance"
    parser.epilog = f"""
IMPORTANT: instances must be registered in AWS Systems Manager (SSM)
before you can start a shell session! Instances not registered in SSM
will not be recognised by {parser.prog} nor show up in --list output.

Visit https://aws.nz/aws-utils/ssm-session for more info and usage examples.

Author: Michael Ludvig
"""

    # Parse supplied arguments
    args = parser.parse_args(argv)

    # If --version do it now and exit
    if args.show_version:
        show_version(args)

    if args.parameters and not args.document_name:
        parser.error("--parameters can only be used together with --document-name")

    if bool(args.user) + bool(args.command) + bool(args.document_name) > 1:
        parser.error(
            """
Use only one of --user / --command / --document-name
If you need to run the COMMAND as a specific USER then prepend
the command with the appropriate: sudo -i -u USER COMMAND
""",
        )

    return args


def start_session(instance_id: str, args: argparse.Namespace) -> None:
    exec_args = ["aws", "ssm", "start-session"]
    if args.profile:
        exec_args += ["--profile", args.profile]
    if args.region:
        exec_args += ["--region", args.region]
    if args.reason:
        exec_args += ["--reason", args.reason]

    if args.user:
        # Fake --document-name / --parameters for --user
        exec_args += [
            "--document-name",
            "AWS-StartInteractiveCommand",
            "--parameters",
            f'command=["sudo -i -u {args.user}"]',
        ]
    if args.command:
        # Fake --document-name / --parameters for --command
        exec_args += ["--document-name", "AWS-StartInteractiveCommand", "--parameters", f"command={args.command}"]
    else:
        # Or use the provided values
        if args.document_name:
            exec_args += ["--document-name", args.document_name]
        if args.parameters:
            exec_args += ["--parameters", args.parameters]

    exec_args += ["--target", instance_id]
    logger.debug("Running: %s", exec_args)
    os.execvp(exec_args[0], exec_args)


def main() -> int:
    ## Split command line to main args and optional command to run
    args = parse_args(sys.argv[1:])

    configure_logging(args.log_level)

    try:
        if bool(args.INSTANCE) + bool(args.list) != 1:
            headers, session_details = InstanceResolver(args).print_list(quiet=True)
            terminal_menu = TerminalMenu(
                [text["summary"] for text in session_details],
                title=headers,
                show_search_hint=True,
                show_search_hint_text="Select a connection. Press 'q' to quit, or '/' to search.",
            )
            selected_index = terminal_menu.show()
            if selected_index:
                selected_session = session_details[selected_index]
                args.INSTANCE = selected_session["InstanceId"]
                print(headers)
                print(f"  {selected_session['summary']}")
            else:
                sys.exit(0)

        elif args.list:
            InstanceResolver(args).print_list()
            sys.exit(0)

        instance_id, _ = InstanceResolver(args).resolve_instance(args.INSTANCE)

        if not instance_id:
            logger.warning("Could not resolve Instance ID for '%s'", args.INSTANCE)
            logger.warning("Perhaps the '%s' is not registered in SSM?", args.INSTANCE)
            sys.exit(1)

        start_session(instance_id, args)

    except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError) as e:
        logger.error(e)
        sys.exit(1)

    return 0


if __name__ == "__main__":
    main()
