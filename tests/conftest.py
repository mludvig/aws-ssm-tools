import argparse
import os
import signal
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from botocore.stub import Stubber


@pytest.fixture(autouse=True)
def aws_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Isolate every test from the real AWS config, credentials and caches."""
    for var in list(os.environ):
        if var.startswith("AWS_"):
            monkeypatch.delenv(var)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows' ~
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "aws-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "aws-credentials"))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-southeast-2")


@pytest.fixture(autouse=True)
def signal_handlers() -> Iterator[None]:
    """Undo the handlers that the code under test installs."""
    signums = [signal.SIGINT, signal.SIGTERM] + ([signal.SIGHUP] if os.name == "posix" else [])
    handlers = {signum: signal.getsignal(signum) for signum in signums}
    yield
    for signum, handler in handlers.items():
        signal.signal(signum, handler)


@pytest.fixture
def cli_args() -> Callable[..., argparse.Namespace]:
    """Build the minimal argparse.Namespace the resolvers need."""

    def _make(**kwargs: Any) -> argparse.Namespace:
        return argparse.Namespace(**{"profile": None, "region": None, "cluster": None, **kwargs})

    return _make


@pytest.fixture
def stub() -> Iterator[Callable[[Any], Stubber]]:
    """Activate a botocore Stubber on a client and assert all queued responses were consumed."""
    stubbers: list[Stubber] = []

    def _stub(client: Any) -> Stubber:
        stubber = Stubber(client)
        stubber.activate()
        stubbers.append(stubber)
        return stubber

    yield _stub

    for stubber in stubbers:
        stubber.assert_no_pending_responses()
        stubber.deactivate()
