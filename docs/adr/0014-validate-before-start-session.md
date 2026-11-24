# 0014. Validate ports, the local port and --reason before StartSession

## Status
Accepted (2026-11-24)

## Context
`ssm-port-forward` passed ports through unchecked, so 0, 70000 or `abc` failed only at StartSession, after the instance lookup. A local port already in use made the plugin wait silently, with the session Active. A `--reason` over 256 characters failed at StartSession in every tool.

## Decision
- Ports are argparse types: `--remote-port` 1-65535, `--local-port` 0-65535 (0: the plugin picks a free one).
- Before StartSession, `ssm-port-forward` binds `127.0.0.1:PORT` for a moment and exits 1 if it can't. On POSIX it sets `SO_REUSEADDR` like the plugin's listener, so a TIME_WAIT left by an earlier session doesn't count. On Windows it doesn't, because there that option lets a socket share a port in use.
- `--reason` is one shared argparse type (`session_reason`) in all four tools that take it: 1-256 characters on one line, StartSession's own rule.

## Consequences
- Bad values fail at once with exit 2, and a busy port with exit 1, before any AWS call that starts a session.
- Something can still take the port between our check and the plugin's bind. That's rare, and the plugin then waits as before.
- Only IPv4 localhost is checked, which is what the plugin listens on.
- An explicit empty `--reason ""` is now an error instead of being dropped.
- **Alternative rejected:** watching the plugin's output for its bind error. The plugin runs on our terminal (ADR 0001), so we'd need a pipe and lose the tty.
