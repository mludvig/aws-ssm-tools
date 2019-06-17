import logging

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
def add_general_parameters(parser):
    """
    Add General Options used by all ssm-* tools.
    """
    group_general = parser.add_argument_group('General Options')
    group_general.add_argument('--profile', '-p', dest='profile', type=str, help='Configuration profile from ~/.aws/{credentials,config}')
    group_general.add_argument('--region', '-g', dest='region', type=str, help='Set / override AWS region.')
    group_general.add_argument('--verbose', '-v', action='store_const', dest='log_level', const=logging.INFO, default=logging.WARN, help='Increase log_level level')
    group_general.add_argument('--debug', '-d', action='store_const', dest='log_level', const=logging.DEBUG, help='Increase log_level level')
    group_general.add_argument('--help', '-h', action="help", help='Print this help and exit')

    return group_general

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
