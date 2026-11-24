# 0013. ssm-tunnel-agent cleanup, version check, and no shell for commands

## Status
Accepted (2026-11-24)

## Context
A server-side TerminateSession or a `pkill` left the remote tun device and NAT rule behind. The agent turned on `ip_forward` and never turned it off. A closed stdin crashed it with IndexError. `--route` values went unquoted into `os.system()`, so `--route '10.0.0.0/8; cmd'` ran `cmd`. A bad `--tunnel-cidr` crashed or handed out the network address. Users had no way to tell that an instance's agent was older than their `ssm-tunnel`.

## Decision
- **Cleanup.** The agent exits through its `finally` on SIGTERM, SIGHUP, or EOF on stdin. It then ignores these signals and SIGINT, because bash sends SIGHUP twice when the session goes. It stops its tun reader thread through a pipe and waits for it before deleting the device. A device that a `select()` still holds open is busy, and `ip tuntap del` fails. The reader also stops on any read or write error.
- **Forwarding.** The agent turns `ip_forward` on only if it was off. On exit it turns it back off, but only when no other `tunSSM.*` device remains.
- **No shell.** The client and agent run `ip`, `iptables`, `sysctl` and the `--up-down` script from argv lists. `--route` must be an IPv4 network, and `--tunnel-cidr` must be IPv4, /29 or bigger.
- **Version.** The agent's ready line ends in `version N` (`AGENT_VERSION`, now 2). A line without one means version 1. The client warns when the agent is older than its bundled copy, naming that file, and carries on.

## Consequences
- Breaking: `--up-down` is now just a path, so arguments or shell syntax in it don't work any more.
- If two agents overlap and the one that turned forwarding on exits first, forwarding stays on, even after the other one exits. That agent found it on, so it leaves it alone. This is the safe side for a tunnel that's still running, and no worse than before.
- An old agent still works with a new client. It just gets the warning and lacks these fixes.
- Bump `AGENT_VERSION` whenever the agent changes in a way that users should pick up.
- **Alternatives rejected.**
  - Refuse to run with an old agent: version 1 still tunnels, so a warning is enough.
  - A separate `--version` round-trip to the agent: the ready line already gives us the version, at no cost.
  - Unique tun names per process: with the default /16, two tunnels pick the same IPs with odds of about 1 in 32,000. The second one already fails with a hint to retry. Unique names would change the agent's arguments.
