import time
import logging
import pexpect

logger = logging.getLogger("ssm-tools.talker")


class SsmTalker:
    def __init__(self, instance_id: str, profile: str, region: str) -> None:
        self._instance_id = instance_id
        self.connect(instance_id, profile, region)

    def connect(self, instance_id: str, profile: str, region: str) -> None:
        extra_args = ""
        if profile:
            extra_args += f"--profile {profile} "
        if region:
            extra_args += f"--region {region} "
        command = f"aws {extra_args} ssm start-session --target {instance_id}"
        logger.debug("Spawning: %s", command)
        self._child = pexpect.spawn(command, echo=False, encoding="utf-8", timeout=10)
        logger.debug("PID: %s", self._child.pid)

        self.wait_for_prompt()
        logger.debug(self._child.before.strip())
        self.shell_prompt = self._child.after

        # Turn off input echo
        self._child.sendline("stty -echo")
        self.wait_for_prompt()

        # Change to home directory (SSM session starts in '/')
        self._child.sendline("cd")
        self.wait_for_prompt()

    def exit(self) -> None:
        logger.debug("Closing session")
        self._child.sendcontrol("c")
        time.sleep(0.5)
        self._child.sendline("exit")
        try:
            self._child.expect(["Exiting session", pexpect.EOF])
        except (OSError, pexpect.exceptions.EOF):
            pass

    def wait_for_prompt(self) -> None:
        """
        As of now a typical SSM prompt is 'sh-4.2$ '
        """
        self._child.expect("sh.*\$ $")
