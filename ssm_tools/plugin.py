"""
Run session-manager-plugin on a session we started through boto3, and end the session if the
plugin didn't. See docs/adr/0011-direct-plugin-invocation.md
"""

import json
import logging
import subprocess
import threading
from typing import Any

import botocore.exceptions
import botocore.session

from .terminal import run_session

__all__ = ["PLUGIN_ENV_VERSION", "Watchdog", "plugin_command", "run_plugin", "terminate_session"]

logger = logging.getLogger("ssm-tools.plugin")

PLUGIN_ENV_VERSION = "1.2.497.0"  # The first that reads the session from RESPONSE_ENV
RESPONSE_ENV = "AWS_SSM_START_SESSION_RESPONSE"
WATCH_INTERVAL = 10  # Seconds between the watchdog's checks
KILL_AFTER = 3  # Seconds the plugin gets to exit after SIGTERM
NOT_CONNECTED_LIMIT = 2  # Checks in a row, so an SSM agent that reconnects doesn't end the session


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
    watchdog = Watchdog(ssm_client, parameters["Target"], profile)
    code = 1
    try:
        code = run_session(command, env=env, on_start=watchdog.watch)
    finally:
        watchdog.stop()
        if code != 0 or watchdog.fired:  # Killed, failed or never started: don't leave the session Active
            terminate_session(ssm_client, response["SessionId"])
    return code


class Watchdog:
    """
    End the plugin when its target is no longer connected to SSM, because the plugin
    would wait for it forever. See docs/adr/0025-session-watchdog.md
    """

    def __init__(self, ssm_client: Any, target: str, profile: str | None) -> None:
        self.ssm_client = ssm_client
        self.target = target
        self.profile = profile
        self.fired = False
        self._stopped = threading.Event()

    def watch(self, proc: subprocess.Popen[bytes]) -> None:
        """Start watching for 'proc', the plugin"""
        session = botocore.session.Session(profile=self.profile)
        if session.full_config["profiles"].get(session.profile or "default", {}).get("mfa_serial"):
            # Refreshing the credentials would prompt for a code in the middle of the session
            logger.debug("Not watching %s with an MFA profile", self.target)
            return
        threading.Thread(target=self._run, args=(proc,), daemon=True).start()

    def stop(self) -> None:
        """The plugin has exited"""
        self._stopped.set()

    def _run(self, proc: subprocess.Popen[bytes]) -> None:
        not_connected = 0
        while not self._stopped.wait(WATCH_INTERVAL):
            try:
                status = self.ssm_client.get_connection_status(Target=self.target)["Status"]
            except botocore.exceptions.ClientError as e:  # E.g. no ssm:GetConnectionStatus: don't ask again
                logger.debug("Not watching %s: %s", self.target, e)
                return
            except botocore.exceptions.BotoCoreError as e:  # E.g. our own network is down: ask again
                logger.debug("Can't check %s: %r", self.target, e)
                continue
            not_connected = not_connected + 1 if status != "connected" else 0
            if not_connected == NOT_CONNECTED_LIMIT:
                break
        else:
            return
        logger.warning("%s is no longer connected to SSM (%s), ending the session", self.target, status)
        self.fired = True
        proc.terminate()
        if not self._stopped.wait(KILL_AFTER):
            proc.kill()


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
