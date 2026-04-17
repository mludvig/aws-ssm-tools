"""
Startup spinner for the Windows PyInstaller builds.

Usage in an entry-point stub:
    from ssm_tools.startup import Spinner
    with Spinner():
        from ssm_tools.some_cli import main
    main()
"""

import itertools
import sys
import threading


class Spinner:
    """
    Displays a spinner on stderr while imports are loading.
    Only active when stderr is a real terminal (not redirected).
    """

    _FRAMES = r"/-\|"

    def __init__(self, message: str = "Loading") -> None:
        self._message = message
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)

    def _spin(self) -> None:
        if not getattr(sys, "frozen", False) or not sys.stderr.isatty():
            return
        for frame in itertools.cycle(self._FRAMES):
            sys.stderr.write(f"\r{self._message}... {frame} ")
            sys.stderr.flush()
            if self._stop.wait(timeout=0.1):
                break
        sys.stderr.write("\r" + " " * (len(self._message) + 6) + "\r")
        sys.stderr.flush()

    def __enter__(self) -> "Spinner":
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join()
