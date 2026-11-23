import os
import re
import subprocess
import sys
from typing import Any

import pexpect
import pytest

from ssm_tools.terminal import _SOFT_RESET, run_session

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX terminal handling")

# Runs run_session(argv[1:]) and exits with its return code
DRIVER = [
    sys.executable,
    "-c",
    "import sys; from ssm_tools.terminal import run_session; sys.exit(run_session(sys.argv[1:]))",
]


def run_in_pty(child_script: str, redirect: str = "") -> dict[str, Any]:
    """
    Run DRIVER with a 'sh -c child_script' child inside a fresh pty.
    Return the terminal settings before / after, the exit code and the raw output.
    """
    driver = " ".join(f"'{arg}'" for arg in DRIVER)
    script = f"""echo "BEFORE=$(stty -g)"; {driver} sh -c '{child_script}' {redirect}; echo "RC=$?"; echo "AFTER=$(stty -g)" """
    shell = pexpect.spawn("sh", ["-c", script], timeout=15)
    shell.expect(pexpect.EOF)
    output = shell.before
    found = {key: re.search(rf"{key}=(\S+)".encode(), output) for key in ("BEFORE", "RC", "AFTER")}
    assert all(found.values()), output
    return {key: match.group(1).decode() for key, match in found.items() if match} | {"output": output}


def assert_no_process(marker: str) -> None:
    pgrep = subprocess.run(["pgrep", "-f", marker], capture_output=True, check=False)
    assert pgrep.returncode == 1, f"Left behind: {pgrep.stdout!r}"


def test_command_not_found(caplog: pytest.LogCaptureFixture) -> None:
    assert run_session(["ssm-tools-no-such-command"]) == 127
    assert "Command not found: ssm-tools-no-such-command" in caplog.text


def test_exit_code_without_terminal() -> None:
    result = subprocess.run([*DRIVER, "sh", "-c", "exit 7"], stdin=subprocess.DEVNULL, check=False)
    assert result.returncode == 7


@pytest.mark.parametrize(
    ("child_script", "returncode"),
    [
        ("stty raw -echo; exit 0", "0"),  # plugin <= 1.2.707 fails to restore even on a clean exit
        ("stty raw -echo; exit 3", "3"),
        ("stty raw -echo; kill -KILL $$", "137"),  # plugin killed
    ],
)
def test_terminal_restored(child_script: str, returncode: str) -> None:
    result = run_in_pty(child_script)
    assert result["RC"] == returncode
    assert result["AFTER"] == result["BEFORE"]
    assert _SOFT_RESET in result["output"]


def test_term_to_wrapper_kills_child_group() -> None:
    # The child signals its parent - the wrapper - as `kill <ec2-session pid>` would
    result = run_in_pty("stty raw -echo; sleep 31.1 & kill -TERM $PPID; sleep 31.2")
    assert result["RC"] == "143"
    assert result["AFTER"] == result["BEFORE"]
    assert_no_process("sleep 31.[12]")


def test_orphan_killed_when_child_exits() -> None:
    # aws exits but the plugin (here: sleep) stays behind in the process group
    result = run_in_pty("stty raw -echo; sleep 31.3 & exit 0")
    assert result["RC"] == "0"
    assert result["AFTER"] == result["BEFORE"]
    assert_no_process("sleep 31.3")


def test_no_soft_reset_when_stdout_is_not_a_terminal(tmp_path: Any) -> None:
    out_file = tmp_path / "out"
    result = run_in_pty("exit 0", redirect=f"> {out_file}")
    assert result["RC"] == "0"
    assert out_file.read_bytes() == b""
