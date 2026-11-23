# 0005. ec2-ssh quotes its ProxyCommand values

## Status
Accepted (2026-11-23)

## Context
`ec2-ssh` built its ProxyCommand by pasting `--profile`, `--region` and `--reason` into a string. ssh expands `%` tokens in that string and then runs it with `/bin/sh -c`. As a result, a quote or `%` in `--reason` broke the command, `%h` was expanded, and `$(…)` ran locally. `--reason` also sat before `ssm`, so it never worked at all.

## Decision
`proxy_command()` passes each value through `shlex.quote()` and then escapes `%` as `%%`, so the shell and ssh's token expansion both give back the original value. `--reason` goes after `start-session`. With `--use-endpoint` (no reason support) it's dropped with a warning. An aws CLI too old for `--use-endpoint` is an error, not a silent fallback to SSM.

## Consequences
- Any reason, profile or region reaches aws verbatim. The tests round-trip the expansion and `shlex.split`.
- This assumes a POSIX shell. Windows' ProxyCommand runs without one, which is covered by D6/C29.
- **Alternative rejected:** passing the profile and region as `AWS_PROFILE`/`AWS_REGION` in the environment. Credentials in env vars like `AWS_ACCESS_KEY_ID` override `AWS_PROFILE`, so the tunnel could run in a different account than the lookup boto3 just did with `--profile`.
