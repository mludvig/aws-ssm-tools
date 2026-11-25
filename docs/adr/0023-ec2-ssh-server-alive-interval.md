# 0023. ec2-ssh sets ServerAliveInterval

## Status
Accepted (2026-11-25)

## Context
When the SSM connection dies, session-manager-plugin can keep the ProxyCommand pipe open (the upstream fix was reverted in 1.2.896.0), and ssh's default `ServerAliveInterval 0` waits forever. Users had to type `~.`.

## Decision
ec2-ssh adds `-o ServerAliveInterval=15` when `ssh -G`, for the final options and destination, reports 0. ServerAliveCountMax stays at ssh's default of 3, so ssh exits about 60 s (4 intervals) after the tunnel dies.

## Consequences
- A dead session ends by itself. A value from the ssh_config or `-o` wins, because `ssh -G` sees it.
- An explicit `ServerAliveInterval 0` looks the same as unset and is overridden. To turn keepalives off, set a very large value.
- One more local `ssh -G` run per connection.
- **Alternatives rejected:** 5 s (aws-cli's `ec2-instance-connect ssh`) sends traffic more often than needed, and 30 s (ssh-over-ssm) means a 2 minute wait. Always adding the option would silently override the user's own setting with `-o` precedence.
