"""
Run session-manager-plugin on a session we started through boto3, and end the session if the
plugin didn't. See docs/adr/0011-direct-plugin-invocation.md
"""

import json
import logging
from typing import Any

import botocore.exceptions

from .terminal import run_session

__all__ = ["PLUGIN_ENV_VERSION", "plugin_command", "run_plugin", "terminate_session"]

logger = logging.getLogger("ssm-tools.plugin")

PLUGIN_ENV_VERSION = "1.2.497.0"  # The first that reads the session from RESPONSE_ENV
RESPONSE_ENV = "AWS_SSM_START_SESSION_RESPONSE"


def plugin_command(
    ssm_client: Any,
    response: dict[str, Any],
    parameters: dict[str, Any],
    profile: str | None,
) -> tuple[list[str], dict[str, str]]:
    """
    The plugin's argv, and what it needs added to the environment, for the session in a
    StartSession 'response' (or its ECS equivalent) that was started with 'parameters'.
    """
    session = {key: response[key] for key in ("SessionId", "TokenValue", "StreamUrl")}
    command = [
        "session-manager-plugin",
        RESPONSE_ENV,  # The name of the variable that holds the session, so the token stays out of ps
        ssm_client.meta.region_name,
        "StartSession",
        profile or "",
        json.dumps(parameters),
        ssm_client.meta.endpoint_url,
    ]
    return command, {RESPONSE_ENV: json.dumps(session)}


def run_plugin(ssm_client: Any, response: dict[str, Any], parameters: dict[str, Any], profile: str | None) -> int:
    """
    Run the plugin on the session in 'response' (see plugin_command()), and return its exit code.
    """
    command, env = plugin_command(ssm_client, response, parameters, profile)
    code = 1
    try:
        code = run_session(command, env=env)
    finally:
        if code != 0:  # Killed, failed or never started: don't leave the session Active
            terminate_session(ssm_client, response["SessionId"])
    return code


def terminate_session(ssm_client: Any, session_id: str) -> None:
    """End the session, unless it has ended already. Warn if we can't."""
    logger.debug("Terminating session %s", session_id)
    try:
        ssm_client.terminate_session(SessionId=session_id)
    except botocore.exceptions.ClientError as e:
        if e.response["Error"]["Code"] == "ValidationException":  # The plugin has ended it already
            logger.debug("Session %s has ended: %s", session_id, e)
        else:
            logger.warning("Session %s may still be active, can't terminate it: %s", session_id, e)
    # Ctrl-C or EOF: refreshing expired credentials may prompt for an MFA code
    except (botocore.exceptions.BotoCoreError, KeyboardInterrupt, EOFError) as e:
        logger.warning("Session %s may still be active, can't terminate it: %r", session_id, e)
