#!/usr/bin/env python3

# Convenience wrapper around 'aws ecs execute-command'
#
# See https://aws.nz/aws-utils/ecs-session for more info.
#
# Author: Michael Ludvig (https://aws.nz)

# The script can list the available containers across all your ECS clusters.
# In the end it executes 'aws ecs execute-command' with the appropriate parameters.
# Supports both EC2 and Fargate ECS tasks.

import argparse
import logging
import re
import shlex
import sys
from typing import Any

import botocore.exceptions

from .common import (
    ESCAPE_HELP,
    add_general_parameters,
    configure_logging,
    handle_boto_error,
    show_version,
    unique_login,
    verify_plugin_version,
)
from .picker import target_selector
from .resolver import ContainerResolver
from .terminal import run_session

logger = logging.getLogger("ssm-tools.ecs-session")

# ECS Exec always runs as root. Switch to the user "$0" with whatever tool the
# container has, then run the command "$1". See docs/adr/0007-ecs-session-user-switch.md
USER_SWITCH_SCRIPT = (
    'U="$0" C="$1"; '
    "while IFS=: read -r n _ i _ _ h _; do "
    'if [ "$n" = "$U" ] || [ "$i" = "$U" ]; then U="$n"; export HOME="$h"; break; fi; '
    "done < /etc/passwd; "
    'if command -v runuser >/dev/null 2>&1; then exec runuser -u "$U" -- /bin/sh -c "$C"; '
    'elif command -v su >/dev/null 2>&1; then exec su -s /bin/sh "$U" -c "$C"; '
    'elif chroot --help 2>&1 | grep -q userspec; then exec chroot --userspec="$U" / /bin/sh -c "$C"; '
    "fi; "
    'echo "ecs-session: cannot switch to user $U: the container has no runuser, su or chroot --userspec" >&2; '
    "exit 126"
)
# A leading '-' would be an option to su / runuser
USER_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


def parse_args(argv: list) -> argparse.Namespace:
    """
    Parse command line arguments.
    """

    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False)

    add_general_parameters(parser)

    group_container = parser.add_argument_group("Container Selection")
    group_container.add_argument(
        "CONTAINER",
        nargs=argparse.ZERO_OR_MORE,
        metavar="[USER@]CONTAINER",
        help="Task ID, Container Name or IP address. Use multiple keywords (e.g. Task Name and IP) to narrow down ambiguous selections.",
    )
    group_container.add_argument(
        "--list",
        dest="list",
        action="store_true",
        help="List available containers configured for ECS RunTask",
    )
    group_container.add_argument(
        "--cluster",
        dest="cluster",
        metavar="CLUSTER",
        help="Specify an ECS cluster. (optional)",
    )

    group_session = parser.add_argument_group("Session Parameters")
    group_session.add_argument(
        "--command",
        "-c",
        dest="command",
        metavar="COMMAND",
        default="/bin/sh",
        help="Command to run inside the container. Default: /bin/sh",
    )
    group_session.add_argument(
        "--user",
        "-u",
        "-l",
        dest="user",
        metavar="USER",
        action="append",
        default=[],
        help="Run the command as USER instead of root, the same as USER@CONTAINER. "
        "The container needs runuser, su or chroot --userspec. (optional)",
    )

    parser.description = "Execute 'ECS Run Task' in a given container"
    parser.epilog = f"""
IMPORTANT: containers must have "execute-command" setting enabled or they
will not be recognised by {parser.prog} nor show up in --list output.
{ESCAPE_HELP}
Visit https://aws.nz/aws-utils/ecs-session for more info and usage examples.

Author: Michael Ludvig
"""

    # Parse supplied arguments
    args = parser.parse_args(argv)

    # USER@ goes on the first keyword, see docs/adr/0006-one-login-syntax.md
    login = ""
    if args.CONTAINER:
        login, _, args.CONTAINER[0] = args.CONTAINER[0].rpartition("@")
    try:
        args.user = unique_login(*args.user, login)
    except ValueError as e:
        parser.error(str(e))
    if args.user and not USER_RE.fullmatch(args.user):
        parser.error(f"invalid user name: {args.user}")

    # If --version do it now and exit
    if args.show_version:
        show_version(args)

    return args


def user_command(user: str, command: str) -> str:
    """Return the ECS Exec command that runs 'command' as 'user'. The agent splits it like a shell."""
    return shlex.join(["/bin/sh", "-c", USER_SWITCH_SCRIPT, user, command])


def execute_command(container: dict[str, Any], args: argparse.Namespace, command: str) -> int:
    exec_args = ["aws", "ecs", "execute-command"]
    if args.profile:
        exec_args += ["--profile", args.profile]
    if args.region:
        exec_args += ["--region", args.region]

    # fmt: off
    exec_args += [
        "--cluster", container["cluster_arn"],
        "--task", container["task_arn"],
        "--container", container["container_name"],
        "--command", command,
        "--interactive",
    ]
    # fmt: on

    logger.debug("Running: %s", exec_args)
    return run_session(exec_args)


def main() -> int:
    ## Split command line to main args and optional command to run
    args = parse_args(sys.argv[1:])

    configure_logging(args.log_level)

    try:
        if args.list:
            ContainerResolver(args).print_list()
            sys.exit(0)

        if not verify_plugin_version(logger, interactive=True):
            sys.exit(1)

        if not args.CONTAINER:
            headers, containers = ContainerResolver(args).print_list(quiet=True)
            selected_session = target_selector(headers, containers)
            args.CONTAINER = [
                selected_session["task_id"],
                selected_session["container_name"],
            ]

        container = ContainerResolver(args).resolve_container(keywords=args.CONTAINER)

        if not container:
            logger.warning("Could not find any container matching: %s", " AND ".join(args.CONTAINER))
            sys.exit(1)

        command = user_command(args.user, args.command) if args.user else args.command
        return execute_command(container, args, command)

    except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError) as e:
        handle_boto_error(e, logger, args.profile)
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
