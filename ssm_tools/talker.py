"""
A shell on an instance, through session-manager-plugin under pexpect.
See docs/adr/0012-ssm-tunnel-session.md
"""

import contextlib
import logging
import os
import re
import time
from typing import Any

import pexpect

from .plugin import plugin_command, terminate_session

logger = logging.getLogger("ssm-tools.talker")

PROMPT = "ssm-tunnel> "
ANY_PROMPT = r"[$#%>] $"  # The instance's own prompt, e.g. 'sh-5.2$ ', before we set ours
CONTROLS = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|[\x00-\x08\x0b-\x1f\x7f]")  # e.g. bash's bracketed paste on/off


class SessionError(Exception):
    """The session, or what we ran in it, didn't do what we expected."""


class SsmTalker:
    def __init__(self, ssm_client: Any, instance_id: str, profile: str | None, reason: str | None = None) -> None:
        self._ssm_client = ssm_client
        self._instance_id = instance_id
        self.connect(profile, reason)

    def connect(self, profile: str | None, reason: str | None) -> None:
        parameters = {"Target": self._instance_id}
        if reason:
            parameters["Reason"] = reason
        response = self._ssm_client.start_session(**parameters)
        self._session_id = response["SessionId"]
        command, env = plugin_command(self._ssm_client, response, parameters, profile)
        logger.debug("Spawning: %s", command)
        try:
            self._child = pexpect.spawn(
                command[0],
                command[1:],
                env={**os.environ, **env},
                echo=False,
                encoding="utf-8",
                timeout=10,
            )
        except pexpect.ExceptionPexpect as e:
            terminate_session(self._ssm_client, self._session_id)
            raise SessionError(f"Can't run {command[0]}: {e}") from None
        logger.debug("PID: %s", self._child.pid)

        try:
            self.expect([ANY_PROMPT], "the shell prompt")
            # Quoted apart so that the echo of this line doesn't look like the new prompt.
            # Turn off input echo, and change to home directory (SSM session starts in '/').
            self._child.sendline(f"PS1='{PROMPT[:4]}''{PROMPT[4:]}'; stty -echo; cd")
            self.wait_for_prompt()
        except BaseException:  # Including Ctrl-C and signals: don't leave the session Active
            self.exit()
            raise

    def exit(self) -> None:
        logger.debug("Closing session")
        try:
            with contextlib.suppress(OSError, pexpect.ExceptionPexpect):
                self._child.sendcontrol("c")
                time.sleep(0.5)
                self._child.sendline("exit")
                self._child.expect(pexpect.EOF)
        finally:  # Even after another Ctrl-C
            self._child.close(force=True)
            if self._child.exitstatus != 0:  # Killed, failed, or didn't end in time
                terminate_session(self._ssm_client, self._session_id)

    def expect(self, patterns: list[str], waiting_for: str) -> int:
        """Like pexpect's expect(), but raise SessionError with what we got instead."""
        try:
            return int(self._child.expect(patterns))
        except (pexpect.TIMEOUT, pexpect.EOF) as e:
            ended = "the session ended" if isinstance(e, pexpect.EOF) else "timed out"
            raise SessionError(f"Waiting for {waiting_for}, {ended}. Got: {self.output()!r}") from None

    def output(self) -> str:
        """What the session printed before the last match, safe to show in our terminal."""
        return CONTROLS.sub("", self._child.before).strip()

    def wait_for_prompt(self) -> None:
        self.expect([re.escape(PROMPT)], "the shell prompt")
