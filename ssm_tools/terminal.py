"""
Run an interactive session child (session-manager-plugin, or ssh) and keep the terminal sane.

See docs/adr/0001-run-session-wrapper.md for why this replaces os.execvp().
"""

import contextlib
import logging
import os
import shutil
import signal
import subprocess
import sys
from collections.abc import Callable

__all__ = ["run_session"]

logger = logging.getLogger("ssm-tools.terminal")

# Undo modes a remote full-screen app (vim / less / tmux) may leave on when the session dies:
# SGR reset, show cursor, normal cursor keys, normal keypad, mouse / focus reporting off,
# bracketed paste off, leave alternate screen (1047 rather than 1049 doesn't restore the cursor).
_SOFT_RESET = (
    b"\x1b[0m\x1b[?25h\x1b[?1l\x1b>"
    b"\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1004l\x1b[?1005l\x1b[?1006l\x1b[?1015l"
    b"\x1b[?2004l\x1b[?1047l"
)


OnStart = Callable[[subprocess.Popen[bytes]], None] | None


def run_session(exec_args: list[str], env: dict[str, str] | None = None, on_start: OnStart = None) -> int:
    """
    Run exec_args, with 'env' added to the environment, and return its exit code
    (128+N if it, or we, got signal N). Call on_start() with the child once it runs.
    """
    # which() on Windows may pick one in the cwd (3.11 always does). See docs/adr/0026-windows-program-lookup.md
    executable = exec_args[0] if os.name == "nt" else shutil.which(exec_args[0])
    if not executable:
        logger.error("Command not found: %s", exec_args[0])
        return 127
    command = [executable, *exec_args[1:]]
    child_env = {**os.environ, **env} if env else None

    # Nothing of ours must end up after the child's output
    sys.stdout.flush()
    sys.stderr.flush()

    if os.name == "posix" and os.isatty(0) and os.tcgetpgrp(0) == os.getpgrp():
        return _run_foreground_job(command, child_env, on_start)

    return _run_child(command, child_env, on_start)


def _exit_code(received: list[int], returncode: int) -> int:
    if received:
        return 128 + received[0]
    return returncode if returncode >= 0 else 128 - returncode


def _run_child(command: list[str], env: dict[str, str] | None, on_start: OnStart = None) -> int:
    """
    No terminal of ours to manage (or Windows). Pass TERM and HUP on, and wait for the child,
    so that our caller still cleans up.
    """
    received: list[int] = []
    try:
        proc = subprocess.Popen(command, env=env)
    except FileNotFoundError:  # Windows, see run_session()
        logger.error("Command not found: %s", command[0])
        return 127
    if on_start:
        on_start(proc)

    def _forward(signum: int, _frame: object) -> None:
        received.append(signum)
        proc.send_signal(signum)

    forwarded = [signal.SIGTERM] + ([signal.SIGHUP] if os.name == "posix" else [])
    old_handlers = {signum: signal.signal(signum, _forward) for signum in forwarded}
    if os.name == "nt":
        # The console sends Ctrl-C to the child as well, leave it to the child. See docs/adr/0019-windows-and-macos.md
        old_handlers[signal.SIGINT] = signal.signal(signal.SIGINT, lambda _signum, _frame: None)
    try:
        returncode = proc.wait()
    except KeyboardInterrupt:  # As subprocess.call() did
        received.append(signal.SIGINT)
        proc.kill()
        returncode = proc.wait()
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
    return _exit_code(received, returncode)


def _killpg(pgid: int, signum: int) -> None:
    # The group may be gone already. macOS returns EPERM when only zombies are left in it.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signum)


def _run_foreground_job(command: list[str], env: dict[str, str] | None, on_start: OnStart = None) -> int:
    """
    Run the command as the terminal's foreground process group, like a shell job,
    so that we can kill all of it (e.g. ssh *and* its ProxyCommand) and then restore the terminal.
    """
    import termios  # POSIX only

    tty_fd = 0
    saved_attrs = termios.tcgetattr(tty_fd)
    received: list[int] = []
    if sys.platform == "darwin":
        # Ctrl-Y (DSUSP) breaks the plugin's stdin read, see docs/adr/0019-windows-and-macos.md
        attrs = termios.tcgetattr(tty_fd)
        attrs[6][termios.VDSUSP] = b"\xff"  # _POSIX_VDISABLE
        termios.tcsetattr(tty_fd, termios.TCSANOW, attrs)

    def _child_setup() -> None:
        os.setpgid(0, 0)
        os.tcsetpgrp(tty_fd, os.getpgrp())
        signal.signal(signal.SIGTTOU, signal.SIG_DFL)

    # We're in the background while the child runs; the child resets this in _child_setup()
    old_handlers = {signal.SIGTTOU: signal.signal(signal.SIGTTOU, signal.SIG_IGN)}
    proc = subprocess.Popen(command, env=env, preexec_fn=_child_setup)
    if on_start:
        on_start(proc)

    def _forward(signum: int, _frame: object) -> None:
        received.append(signum)
        _killpg(proc.pid, signum)  # The child may not pass TERM / HUP on to its own children

    # Set after Popen() so that the child doesn't inherit SIG_IGN
    for signum, new_handler in (
        (signal.SIGINT, signal.SIG_IGN),
        (signal.SIGQUIT, signal.SIG_IGN),
        (signal.SIGTSTP, signal.SIG_IGN),
        (signal.SIGTERM, _forward),
        (signal.SIGHUP, _forward),
    ):
        old_handlers[signum] = signal.signal(signum, new_handler)

    try:
        returncode = proc.wait()
    finally:
        # If the child died first its own children may still be in the group, reading the terminal.
        # Kill it before taking the terminal back, or it eats the user's next line.
        _killpg(proc.pid, signal.SIGTERM)
        with contextlib.suppress(OSError):  # The terminal may be gone (SIGHUP)
            os.tcsetpgrp(tty_fd, os.getpgrp())
            termios.tcsetattr(tty_fd, termios.TCSADRAIN, saved_attrs)
            if os.isatty(1):
                os.write(1, _SOFT_RESET)
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)

    return _exit_code(received, returncode)
