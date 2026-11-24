import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from botocore.exceptions import (
    ClientError,
    NoCredentialsError,
    SSOTokenLoadError,
    TokenRetrievalError,
    UnauthorizedSSOTokenError,
)

from ssm_tools.common import configure_logging, handle_boto_error, is_sso_profile

AWS_CONFIG = """
[default]
sso_session = corp
sso_account_id = 111111111111
sso_role_name = Admin
[profile sso]
sso_session = corp
sso_account_id = 111111111111
sso_role_name = Admin
[profile legacy]
sso_start_url = https://corp.awsapps.com/start
sso_region = ap-southeast-2
sso_account_id = 111111111111
sso_role_name = Admin
[profile keys]
aws_access_key_id = AKIAIOSFODNN7EXAMPLE
aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
[sso-session corp]
sso_start_url = https://corp.awsapps.com/start
sso_region = ap-southeast-2
"""


@pytest.fixture
def aws_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "config").write_text(AWS_CONFIG)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_PROFILE", raising=False)


@pytest.mark.usefixtures("aws_config")
@pytest.mark.parametrize(
    ("profile", "sso"),
    [("sso", True), ("legacy", True), (None, True), ("keys", False), ("nosuch", False)],
)
def test_is_sso_profile(profile: str | None, sso: bool) -> None:
    assert is_sso_profile(profile) == sso


def client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "nope"}}, "StartSession")


@pytest.mark.usefixtures("aws_config")
@pytest.mark.parametrize(
    ("error", "profile", "hint"),
    [
        (SSOTokenLoadError(error_msg="Token for corp does not exist"), "sso", " --profile sso"),
        (UnauthorizedSSOTokenError(), "legacy", " --profile legacy"),
        (TokenRetrievalError(provider="sso", error_msg="expired"), None, ""),
        (client_error("ExpiredToken"), "sso", " --profile sso"),
        (client_error("ExpiredTokenException"), "sso", " --profile sso"),
        (client_error("ExpiredToken"), "keys", None),  # Not an SSO profile: 'aws sso login' won't help
        (client_error("AccessDenied"), "sso", None),  # Nor for permissions
        (client_error("AccessDeniedException"), "sso", None),
        (client_error("AuthFailure"), "sso", None),
        (NoCredentialsError(), "sso", None),
    ],
)
def test_handle_boto_error(
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    profile: str | None,
    hint: str | None,
) -> None:
    handle_boto_error(error, logging.getLogger("ssm-tools.test"), profile)
    assert str(error) in caplog.text
    if hint is None:
        assert "aws sso login" not in caplog.text
    else:
        assert f"Hint: log in again with: aws sso login{hint}\n" in caplog.text


@pytest.fixture
def log_levels() -> Iterator[None]:
    """configure_logging() sets these, put them back."""
    loggers = [logging.getLogger(name) for name in ("botocore", "ssm-tools")]
    levels = [logger.level for logger in loggers]
    yield
    for logger, level in zip(loggers, levels):
        logger.setLevel(level)


@pytest.mark.usefixtures("log_levels")
@pytest.mark.parametrize(("level", "shown"), [(logging.DEBUG, True), (logging.INFO, False), (logging.WARNING, False)])
def test_botocore_warnings(caplog: pytest.LogCaptureFixture, level: int, shown: bool) -> None:
    """botocore warns with a traceback before it raises what we report."""
    configure_logging(level)
    logging.getLogger("botocore.tokens").warning("Refreshing token failed during the mandatory refresh period.")
    logging.getLogger("botocore.tokens").error("Errors still show")
    assert ("Refreshing token failed" in caplog.text) == shown
    assert "Errors still show" in caplog.text
