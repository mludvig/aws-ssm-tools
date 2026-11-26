import argparse
import io
import logging
import pathlib
import re
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import boto3
import botocore.credentials
import botocore.exceptions
import botocore.session
import packaging.version

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

    # Names can hold characters that e.g. an ASCII or cp1252 stdout can't show. Don't crash on them.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(errors="replace")

    # Default log level is set to WARNING
    logging.basicConfig(level=logging.WARNING, format=logging_format)
    # Except for our modules
    logging.getLogger("ssm-tools").setLevel(level)
    # botocore warns with a traceback before it raises, e.g. on an expired SSO token. We report what it raises.
    # See docs/adr/0016-error-hints-and-botocore-logging.md
    logging.getLogger("botocore").setLevel(logging.WARNING if level == logging.DEBUG else logging.ERROR)


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
    group_general.add_argument(
        *_get_opts("--region", "-g"),
        dest="region",
        type=str,
        help="Set / override AWS region.",
    )
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

PLUGIN_INSTALL_URL = (
    "https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html"
)
# First with 'Enter ~.' to end a shell session, see docs/adr/0002-plugin-version-warning.md
PLUGIN_ESCAPE_VERSION = "1.2.835.0"
# The oldest that AWS will keep supporting, see docs/adr/0021-plugin-supported-version.md
PLUGIN_SUPPORTED_VERSION = "1.2.764.0"

__all__.append("ESCAPE_HELP")

ESCAPE_HELP = f"""
To end a session that stopped responding type Enter ~. (Enter, tilde, dot).
That needs session-manager-plugin {PLUGIN_ESCAPE_VERSION} or newer.
"""


def _installed_version(
    command: list[str],
    logger: logging.Logger,
    extract: Callable[[str], str] = lambda output: output,
) -> packaging.version.Version | None:
    """
    Return the version printed by 'command', or None if we can't parse it.
    Raises OSError if the command isn't installed.
    """
    output = subprocess.run(command, stdout=subprocess.PIPE, check=False).stdout.decode(errors="replace").strip()
    try:
        version = packaging.version.Version(extract(output))
    except (IndexError, packaging.version.InvalidVersion):
        logger.warning("Can't parse the version from '%s': %r", " ".join(command), output)
        return None
    logger.debug("%s version %s", command[0], version)
    return version


__all__.append("verify_plugin_version")


def verify_plugin_version(logger: logging.Logger, required: str | None = None, interactive: bool = False) -> bool:
    """
    Verify that session-manager-plugin is installed and, if given, of the 'required' version or newer.
    Warn if AWS will soon stop supporting it, or for 'interactive' sessions if it's too old to support 'Enter ~.'.
    """
    try:
        version = _installed_version(["session-manager-plugin", "--version"], logger)
    except OSError:
        logger.error("session-manager-plugin is not installed, see %s", PLUGIN_INSTALL_URL)
        return False

    if version is None:  # Don't block on output we don't understand
        return True

    if required and version < packaging.version.Version(required):
        logger.error(
            "session-manager-plugin %s is installed, %s or newer is required, see %s",
            version,
            required,
            PLUGIN_INSTALL_URL,
        )
        return False

    if version < packaging.version.Version(PLUGIN_SUPPORTED_VERSION):
        logger.warning(
            "AWS will soon stop supporting session-manager-plugin %s, upgrade to %s or newer: %s",
            version,
            PLUGIN_SUPPORTED_VERSION,
            PLUGIN_INSTALL_URL,
        )
    elif interactive and version < packaging.version.Version(PLUGIN_ESCAPE_VERSION):
        logger.warning(
            "session-manager-plugin %s can't end a stuck session with 'Enter ~.', upgrade to %s or newer: %s",
            version,
            PLUGIN_ESCAPE_VERSION,
            PLUGIN_INSTALL_URL,
        )

    return True


# ---------------------------------------------------------


__all__.append("verify_awscli_version")


def verify_awscli_version(version_required: str, logger: logging.Logger) -> bool:
    """
    Verify that the aws-cli is installed and is of a required version or newer.
    """
    try:
        # E.g. aws-cli/2.15.0 Python/3.11.6 Linux/6.5.0 exe/x86_64.ubuntu.22
        version = _installed_version(["aws", "--version"], logger, lambda output: output.split(" ")[0].split("/")[1])
    except OSError:
        logger.error("AWS-CLI is not installed")
        return False

    if version and version < packaging.version.Version(version_required):
        logger.error("AWS-CLI version %s is installed, %s is required", version, version_required)
        return False

    return True


# ---------------------------------------------------------


__all__.append("handle_boto_error")

# Errors that 'aws sso login' fixes, for an SSO profile. See docs/adr/0016-error-hints-and-botocore-logging.md
_SSO_ERRORS = (botocore.exceptions.SSOError, botocore.exceptions.TokenRetrievalError)
_EXPIRED_CODES = {"ExpiredToken", "ExpiredTokenException"}


def is_sso_profile(profile: str | None) -> bool:
    """Does the profile (or the default one) get its credentials from AWS SSO / IAM Identity Center?"""
    try:
        config = botocore.session.Session(profile=profile).get_scoped_config()
    except botocore.exceptions.ProfileNotFound:
        return False
    return "sso_session" in config or "sso_start_url" in config


def handle_boto_error(e: Exception, logger: logging.Logger, profile: str | None = None) -> None:
    logger.error(e)
    expired = isinstance(e, botocore.exceptions.ClientError) and e.response["Error"]["Code"] in _EXPIRED_CODES
    if (expired or isinstance(e, _SSO_ERRORS)) and is_sso_profile(profile):
        profile_flag = f" --profile {profile}" if profile else ""
        logger.error("Hint: log in again with: aws sso login%s", profile_flag)


# ---------------------------------------------------------

__all__.append("unique_login")


def unique_login(*logins: str) -> str:
    """
    Return the login name given as -u / -l / --user / USER@TARGET, or '' if none.
    Raises ValueError if they disagree. See docs/adr/0006-one-login-syntax.md
    """
    unique = {login for login in logins if login}
    if len(unique) > 1:
        raise ValueError(f"conflicting login names: {', '.join(sorted(unique))}")
    return unique.pop() if unique else ""


# ---------------------------------------------------------

__all__.append("session_reason")


def session_reason(value: str) -> str:
    """argparse type for --reason: what StartSession accepts. See docs/adr/0014-validate-before-start-session.md"""
    if not re.fullmatch(r".{1,256}", value):
        raise argparse.ArgumentTypeError("must be 1-256 characters on one line")
    return value


__all__.append("port_number")


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


# ---------------------------------------------------------

__all__.append("printable")


def printable(text: str) -> str:
    """Replace control characters, e.g. ESC from an instance's self-reported name, with '?'."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "?", text)


# ---------------------------------------------------------

__all__.append("exit_on_signal")


def exit_on_signal(signum: int, _frame: Any) -> None:
    """Signal handler: exit through the 'finally' blocks with the shell's 128+N status."""
    raise SystemExit(128 + signum)


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
