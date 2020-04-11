import sys
import logging
import subprocess
import boto3
import packaging.version
from . import __version__ as ssm_tools_version

__all__ = []

# ---------------------------------------------------------

__all__.append("configure_logging")
def configure_logging(logger_name, level):
    """
    Configure logging format and level.
    """
    streamHandler = logging.StreamHandler()
    formatter = logging.Formatter(
        "[%(name)s] %(levelname)s: %(message)s"
    )
    streamHandler.setFormatter(formatter)
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    logger.addHandler(streamHandler)
    logger.debug("Logging level set to DEBUG")

    return logger

# ---------------------------------------------------------

__all__.append("add_general_parameters")
def add_general_parameters(parser, long_only = False):
    """
    Add General Options used by all ssm-* tools.
    """

    # Remove short options if long_only==True
    def _get_opts(opt_long, opt_short):
        opts = [ opt_long ]
        if not long_only:
            opts.append(opt_short)
        return opts

    group_general = parser.add_argument_group('General Options')
    group_general.add_argument(*_get_opts('--profile', '-p'), dest='profile', type=str, help='Configuration profile from ~/.aws/{credentials,config}')
    group_general.add_argument(*_get_opts('--region',  '-g'), dest='region', type=str, help='Set / override AWS region.')
    group_general.add_argument(*_get_opts('--verbose', '-v'), action='store_const', dest='log_level', const=logging.INFO, default=logging.WARN, help='Increase log_level level')
    group_general.add_argument(*_get_opts('--debug',   '-d'), action='store_const', dest='log_level', const=logging.DEBUG, help='Increase log_level level')
    group_general.add_argument(*_get_opts('--version', '-V'), action='store_true', dest='show_version', help=f'Show package version and exit. Version is {ssm_tools_version}')
    group_general.add_argument(*_get_opts('--help',    '-h'), action="help", help='Print this help and exit')

    return group_general

# ---------------------------------------------------------

__all__.append("show_version")
def show_version(args):
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
def bytes_to_human(size):
    """
    Convert Bytes to more readable units
    """
    units = [ 'B', 'kB', 'MB', 'GB', 'TB' ]
    unit_idx = 0    # Start with Bytes
    while unit_idx < len(units)-1:
        if size < 2048:
            break
        size /= 1024.0
        unit_idx += 1
    return size, units[unit_idx]

# ---------------------------------------------------------

__all__.append("seconds_to_human")
def seconds_to_human(seconds, decimal=3):
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
def verify_plugin_version(version_required, logger):
    """
    Verify that a session-manager-plugin is installed
    and is of a required version or newer.
    """
    session_manager_plugin = 'session-manager-plugin'

    try:
        result = subprocess.run([session_manager_plugin, '--version'], stdout=subprocess.PIPE)
        plugin_version = result.stdout.decode('ascii').strip()
        logger.debug(f"{session_manager_plugin} version {plugin_version}")

        if packaging.version.parse(plugin_version) >= packaging.version.parse(version_required):
            return True

        logger.error(f"ERROR: session-manager-plugin version {plugin_version} is installed, {version_required} is required")
    except FileNotFoundError as e:
        logger.error(f"ERROR: {session_manager_plugin} not installed")

    logger.error("ERROR: Check out https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html for instructions")

    return False
