# 0012. ssm-tunnel: its own session, a known prompt, and dead-agent detection

## Status
Accepted (2026-11-24), amended 2026-11-25

## Context
`ssm-tunnel` ran `aws ssm start-session` under pexpect from a space-split string, so a profile with spaces broke it and the session stayed Active after a kill. It matched only a `$ ` prompt and let a `pexpect.TIMEOUT` traceback escape when the agent failed. A silent agent was never noticed. On a tun-name collision, both the client and the agent deleted the other tunnel's device.

## Decision
- **Session.** `SsmTalker` (`ssm_tools/talker.py`) starts the session through boto3 and spawns the plugin from the `plugin_command()` argv that `run_plugin()` uses (ADR 0011). It calls `TerminateSession` if the plugin can't be spawned or exits non-zero.
- **Prompt.** It waits for any `$ `, `# `, `% ` or `> ` prompt, then sets `PS1='ssm-tunnel> '`. Every wait raises `SessionError` with the output received, and `main()` reports that without a traceback. The agent's start-up waits for "ready", "not found" or our prompt, which means the agent has exited.
- **Dead agent.** If our prompt comes back, the agent has exited and the client exits 1 at once. The agent also sends `#` every 10 s, so the client exits 1 after 30 s with nothing received, e.g. when the agent hangs.
- **Collisions.** The client and the agent delete a tun device only after their own `ip tuntap add` succeeded. Otherwise they exit with an error and leave the device alone.

## Consequences
- `ssm-tunnel` no longer needs the AWS CLI. It needs plugin 1.2.497.0 or newer, and `ssm:TerminateSession` for the cleanup.
- `-r` stays `--route`, as in 2.2.1. `--reason` is long-only in every tool (amended 2026-11-25: it's rarely used, and freeing `-r` for it broke `ssm-tunnel -r CIDR` silently). The 3.0.0b1 beta had `-R` for `--route` and `-r` for `--reason`.
- A tunnel whose agent stops answering ends within about 30 s rather than hanging.
- The tun name still comes from the IPs, so two tunnels with the same random IPs still collide. The second one now fails with a hint to retry, and the first keeps working.
- **Alternatives rejected.**
  - Run the tunnel over `run_plugin()`: the tunnel needs to talk to the shell, which `run_session` hands to the user's terminal.
  - Make the tun name unique per process: the agent derives the same name, so this would change the agent protocol. That can wait for the C19 agent work.
