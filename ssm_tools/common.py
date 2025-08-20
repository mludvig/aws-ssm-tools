import argparse
import logging
import pathlib
import subprocess
import sys
from typing import Optional

import boto3
import botocore.credentials
import packaging.version
from botocore.exceptions import (
    ClientError,
    ProfileNotFound,
    SSOTokenLoadError,
    TokenRetrievalError,
)

from . import __version__ as ssm_tools_version

__all__ = []

# ---------------------------------------------------------

__all__.append("configure_logging")


def configure_logging(level: int) -> None:
    """
    Configure logging format and level.
    """
    if level == logging.DEBUG:
        logging_format = "[%(name)s] %(levelname)s: %(message)s"
    else:
        logging_format = "%(levelname)s: %(message)s"

    # Default log level is set to WARNING
    logging.basicConfig(level=logging.WARNING, format=logging_format)
    # Except for our modules
    logging.getLogger("ssm-tools").setLevel(level)


# ---------------------------------------------------------

__all__.append("add_general_parameters")


def add_general_parameters(parser: argparse.ArgumentParser, long_only: bool = False) -> argparse._ArgumentGroup:
    """
    Add General Options used by all ssm-* tools.
    """

    # Remove short options if long_only==True
    def _get_opts(opt_long: str, opt_short: str) -> list[str]:
        opts = [opt_long]
        if not long_only:
            opts.append(opt_short)
        return opts

    group_general = parser.add_argument_group("General Options")
    group_general.add_argument(
        *_get_opts("--profile", "-p"),
        dest="profile",
        type=str,
        help="Configuration profile from ~/.aws/{credentials,config}",
    )
    group_general.add_argument(*_get_opts("--region", "-g"), dest="region", type=str, help="Set / override AWS region.")
    group_general.add_argument(
        *_get_opts("--verbose", "-v"),
        action="store_const",
        dest="log_level",
        const=logging.INFO,
        default=logging.INFO,
        help="Default log level. Show informational messages only.",
    )
    group_general.add_argument(
        *_get_opts("--debug", "-d"),
        action="store_const",
        dest="log_level",
        const=logging.DEBUG,
        help="Increase log level.",
    )
    group_general.add_argument(
        *_get_opts("--quiet", "-q"),
        action="store_const",
        dest="log_level",
        const=logging.WARNING,
        help="Decrease log level. Only show warnings and errors.",
    )
    group_general.add_argument(
        *_get_opts("--version", "-V"),
        action="store_true",
        dest="show_version",
        help=f"Show package version and exit. Version is {ssm_tools_version}",
    )
    group_general.add_argument(*_get_opts("--help", "-h"), action="help", help="Print this help and exit")

    return group_general


# ---------------------------------------------------------

__all__.append("show_version")


def show_version(args: argparse.Namespace) -> None:
    """
    Show package version and exit.
    """
    version_string = f"ssm-tools/{ssm_tools_version}"
    if args.log_level <= logging.INFO:
        version_string += f" python/{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        version_string += f" boto3/{boto3.__version__}"
    print(version_string)
    sys.exit(0)


# ---------------------------------------------------------

__all__.append("bytes_to_human")


def bytes_to_human(size: float) -> tuple[float, str]:
    """
    Convert Bytes to more readable units
    """
    units = ["B", "kB", "MB", "GB", "TB"]
    unit_idx = 0  # Start with Bytes
    while unit_idx < len(units) - 1:
        if size < 2048:
            break
        size /= 1024.0
        unit_idx += 1
    return size, units[unit_idx]


# ---------------------------------------------------------

__all__.append("seconds_to_human")


def seconds_to_human(seconds: float, decimal: int = 3) -> str:
    """
    Convert seconds to HH:MM:SS[.SSS]

    If decimal==0 only full seconds are used.
    """
    secs = int(seconds)
    fraction = seconds - secs

    mins = int(secs / 60)
    secs = secs % 60

    hours = int(mins / 60)
    mins = mins % 60

    ret = f"{hours:02d}:{mins:02d}:{secs:02d}"
    if decimal:
        fraction = int(fraction * (10**decimal))
        ret += f".{fraction:0{decimal}d}"

    return ret


# ---------------------------------------------------------

__all__.append("verify_plugin_version")


def verify_plugin_version(version_required: str, logger: logging.Logger) -> bool:
    """
    Verify that a session-manager-plugin is installed
    and is of a required version or newer.
    """
    session_manager_plugin = "session-manager-plugin"

    try:
        result = subprocess.run([session_manager_plugin, "--version"], stdout=subprocess.PIPE, check=False)
        plugin_version = result.stdout.decode("ascii").strip()
        logger.debug(f"{session_manager_plugin} version {plugin_version}")

        if packaging.version.parse(plugin_version) >= packaging.version.parse(version_required):
            return True

        logger.error(f"session-manager-plugin version {plugin_version} is installed, {version_required} is required")
    except FileNotFoundError:
        logger.error(f"{session_manager_plugin} not installed")

    logger.error(
        "Check out https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html for instructions",
    )

    return False


# ---------------------------------------------------------


__all__.append("verify_awscli_version")


def verify_awscli_version(version_required: str, logger: logging.Logger) -> bool:
    """
    Verify that the aws-cli is installed and is of a required version or newer.
    """
    aws_cli = "aws"

    try:
        result = subprocess.run([aws_cli, "--version"], stdout=subprocess.PIPE, check=False)
        plugin_version = result.stdout.decode("ascii").strip().split(" ")[0].split("/")[1]
        logger.debug(f"AWS-CLI version {plugin_version}")

        if packaging.version.parse(plugin_version) >= packaging.version.parse(version_required):
            return True

        logger.error(f"AWS-CLI version {plugin_version} is installed, {version_required} is required")
    except FileNotFoundError:
        logger.error("AWS-CLI is not installed")

    return False


# ---------------------------------------------------------


__all__.append("target_selector")


def target_selector(headers: str, targets: list[dict[str, str]]) -> dict[str, str]:

    # Simple term menu does not support windows as of 2025-08-14
    if not sys.platform.startswith("win"):
        from simple_term_menu import TerminalMenu

        terminal_menu = TerminalMenu(
            [text["summary"] for text in targets],
            title=headers,
            show_search_hint=True,
            show_search_hint_text="Select a connection. Press 'q' to quit, or '/' to search.",
        )
        selected_index = terminal_menu.show()
    else:
        print("  {}".format(headers.replace("\n", "\n  ")))
        items = [text["summary"] for text in targets]

        # Calculate padding for numbers
        width = len(str(len(items) - 1))

        for i, item in enumerate(items):
            print(f"{i:>{width}} | {item}")

        print()
        try:
            selected_index = input("Enter the number for the server you want to connect to: ")
        except KeyboardInterrupt:
            print("\nExiting...")
            sys.exit(0)
        except ValueError:
            print("Invalid input. Please enter a number only.")
            sys.exit(1)

    if selected_index is None or not str(selected_index).isdigit():
        print(f"User input '{selected_index}' - Exiting")
        sys.exit(0)

    try:
        selected_target = targets[int(selected_index)]  # Cast to int to make mypy happy
    except IndexError as e:
        print(f"Invalid selection - {e}")
        sys.exit(1)
    print(headers)
    print(f"  {selected_target['summary']}")
    return selected_target


# ---------------------------------------------------------

__all__.append("AWSSessionBase")


class AWSSessionBase:
    def __init__(self, args: argparse.Namespace) -> None:
        # aws-cli compatible MFA cache
        cli_cache = pathlib.Path("~/.aws/cli/cache").expanduser()

        # Construct boto3 session with MFA cache
        self.session = boto3.session.Session(profile_name=args.profile, region_name=args.region)
        self.session._session.get_component("credential_provider").get_provider("assume-role").cache = (
            botocore.credentials.JSONFileCache(cli_cache)
        )


def sso_login(profile_name: Optional[str]) -> None:
    """
    Check if we need to login to an AWS SSO session.
    """

    try:
        if profile_name:
            session = boto3.Session(profile_name=profile_name)
        else:
            session = boto3.Session()
        session.client("sts").get_caller_identity()

    # We now catch a specific tuple of exceptions that indicate a credential issue.
    except (SSOTokenLoadError, TokenRetrievalError) as e:
        print(f"Credential error detected: {e}")
        print(f"SSO session for profile '{profile_name if profile_name else 'Default'}' is likely expired or invalid.")

        command = ["aws", "sso", "login"]
        if profile_name:
            command.extend(["--profile", profile_name])

        subprocess.run(command, check=True)
    except ProfileNotFound as e:
        print(f"Please check your ~/.aws folder. {e}")
        sys.exit(1)
    except ClientError as e:
        # Access denied means session is valid.
        if e.response.get("Error", {}).get("Code") != "AccessDenied":
            raise
