"""
Let the user pick a target when none was given. See docs/adr/0009-target-picker.md
"""

import logging
import os
import shutil
import signal
import subprocess
import sys
from typing import Any

from wcwidth import wcwidth

from .common import exit_on_signal, printable

__all__ = ["has_terminal", "target_selector"]

logger = logging.getLogger("ssm-tools.picker")


def has_terminal() -> bool:
    """
    Is there a user at a terminal to pick a target?
    """
    return sys.stdin.isatty() and sys.stdout.isatty()


def target_selector(headers: str, targets: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Show the targets' summaries under the table headers and return the target the user picked.
    """
    if not has_terminal():
        logger.error("No target given and no terminal to pick one on. Name it on the command line, see --list.")
        sys.exit(2)

    summaries = [printable(target["summary"]) for target in targets]
    try:
        index = _pick(headers, summaries)
    except KeyboardInterrupt:
        index = None
    if index is None:
        logger.warning("No target selected")
        sys.exit(130)

    print(headers, file=sys.stderr)
    print(f"  {summaries[index]}", file=sys.stderr)
    return targets[index]


def _pick(headers: str, summaries: list[str]) -> int | None:
    """
    Arrow key menu if the terminal can do it, else a numbered list.
    """
    if os.environ.get("TERM", "dumb") != "dumb":
        try:
            return _menu(headers, summaries)
        except (ImportError, NotImplementedError, OSError, subprocess.CalledProcessError) as e:
            logger.debug("Can't show the menu, asking for a number instead: %r", e)
    return _numbered(headers, summaries)


def _menu(headers: str, summaries: list[str]) -> int | None:
    from simple_term_menu import TerminalMenu  # Not on Windows

    # The menu cuts long rows by characters, not columns, and a wrapped row puts the cursor
    # on the wrong one. Room for its "> " cursor and the last column.
    width = shutil.get_terminal_size().columns - 3
    menu = TerminalMenu(
        [_fit(summary, width).replace("|", "\\|") for summary in summaries],
        title="\n".join(_fit(line, width + 2) for line in headers.split("\n")),
        show_search_hint=True,
        show_search_hint_text="Select a connection. Press 'q' to quit, or '/' to search.",
    )

    # Ctrl-Z would leave the menu stuck after 'fg', the others would skip show()'s terminal restore
    handlers: dict[signal.Signals, Any] = {
        signal.SIGTSTP: signal.SIG_IGN,
        signal.SIGQUIT: exit_on_signal,
        signal.SIGTERM: exit_on_signal,
        signal.SIGHUP: exit_on_signal,
    }
    old_handlers = {signum: signal.signal(signum, handler) for signum, handler in handlers.items()}
    try:
        index = menu.show()
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
    return index if isinstance(index, int) else None


def _numbered(headers: str, summaries: list[str]) -> int | None:
    digits = len(str(len(summaries)))
    lines = [" " * (digits + 1) + line for line in headers.split("\n")]
    lines += [f"{number:>{digits}})  {summary}" for number, summary in enumerate(summaries, 1)]
    print("\n".join(lines), file=sys.stderr)
    while True:
        print(f"Select 1-{len(summaries)}, or press Enter to quit: ", end="", file=sys.stderr, flush=True)
        answer = sys.stdin.readline().strip()
        if not answer:
            return None
        if answer.isdecimal() and 1 <= int(answer) <= len(summaries):
            return int(answer) - 1


def _fit(text: str, width: int) -> str:
    """
    Cut text to at most 'width' terminal columns.
    """
    used = 0
    for position, char in enumerate(text):
        used += max(wcwidth(char), 0)
        if used > width:
            return text[:position]
    return text
