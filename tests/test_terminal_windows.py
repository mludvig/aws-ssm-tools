import os
import shutil
from pathlib import Path

import pytest

from ssm_tools.terminal import run_session

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows program lookup")


def test_program_in_cwd_is_not_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A program planted in the cwd doesn't run, see docs/adr/0026-windows-program-lookup.md"""
    shutil.copy(Path(os.environ["SYSTEMROOT"], "System32", "whoami.exe"), tmp_path / "ssm-tools-planted.exe")
    monkeypatch.chdir(tmp_path)
    assert run_session(["ssm-tools-planted"]) == 127
