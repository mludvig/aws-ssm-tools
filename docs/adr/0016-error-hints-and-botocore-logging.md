# 0016. The SSO login hint, and botocore's warnings

## Status
Accepted (2026-11-24)

## Context
`handle_boto_error()` suggested `aws sso login` for any error whose text held "token", "auth", "credential" and the like, and for AccessDenied and AuthFailure. That misled users whose profiles don't use SSO, and users who lacked a permission. Logging in again fixes neither. On an expired SSO token, botocore also logged two WARNINGs with full tracebacks before raising the error we report (#53).

## Decision
- The hint shows only for botocore's SSO errors (`SSOError`, `TokenRetrievalError`) or an `ExpiredToken` / `ExpiredTokenException` code. It also needs an SSO profile, one with `sso_session` or `sso_start_url` in its config.
- The `botocore` logger is at ERROR unless `--debug` is on, when it's back at WARNING.

## Consequences
- An expired token on a profile that isn't SSO (for example assumed-role keys) gets botocore's message and no hint.
- Without `--debug`, botocore's other warnings are hidden too. We report the exceptions it raises anyway.
- **Alternatives rejected.**
  - Run `aws sso login` for the user and retry: it needs the AWS CLI, a browser, and a tty. Dropped at the user's request.
  - Keep a hint for AccessDenied ("check your IAM permissions"): the error already says that.
  - Filter botocore's tracebacks with a logging filter: setting a level does the same with less code.
