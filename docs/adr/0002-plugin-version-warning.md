# 0002. Warn, don't fail, on a session-manager-plugin without `Enter ~.`

## Status
Accepted (2026-11-23)

## Context
session-manager-plugin 1.2.835.0 added `Enter ~.` to end a shell session that stopped responding. On older plugins `~.` is sent to the remote shell as text, and the only way out is to kill the tool. Only `ec2-ssh` checked the plugin version, and the other tools crashed with a traceback on unexpected `--version` output.

## Decision
`ec2-session` and `ecs-session` check the plugin before starting a session (after `--list`). They **warn** below 1.2.835.0 and link the install docs. A missing plugin is an error in every tool that needs it. Hard minimums stay only where a feature needs them (`ec2-ssh`: 1.1.23). Version output we can't parse is logged as a warning and doesn't block.

## Consequences
- Users on old plugins learn why `~.` doesn't work, and their sessions still run.
- **Alternatives rejected:**
  - Requiring 1.2.835.0 would break working setups for an escape key.
  - Fetching the latest version from S3 to show in the warning adds a network call to every start. The docs link is enough.
