import logging

__all__ = []

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

__all__.append("add_general_parameters")
def add_general_parameters(parser):
    """
    Add General Options used by all ssm-* tools.
    """
    group_general = parser.add_argument_group('General Options')
    group_general.add_argument('--profile', '-p', dest='profile', type=str, help='Configuration profile from ~/.aws/{credentials,config}')
    group_general.add_argument('--region', '-r', dest='region', type=str, help='Set / override AWS region.')
    group_general.add_argument('--verbose', '-v', action='store_const', dest='log_level', const=logging.INFO, default=logging.WARN, help='Increase log_level level')
    group_general.add_argument('--debug', '-d', action='store_const', dest='log_level', const=logging.DEBUG, help='Increase log_level level')
    group_general.add_argument('--help', '-h', action="help", help='Print this help and exit')
