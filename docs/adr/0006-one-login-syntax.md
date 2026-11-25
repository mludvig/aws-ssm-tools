# 0006. One login syntax for all session tools

## Status
Accepted (2026-11-23)

## Context
Each tool named the login user differently. ec2-ssh took `-l` and `user@` from ssh. ec2-session took `-u`/`--sudo`, used `-l` for `--list` (so `ec2-session -l host` listed everything and ignored the host) and rejected `user@`. The user was also pasted unquoted into the remote `sudo -i -u`, so `-u 'x;id'` ran `id`.

## Decision
- ec2-session and ec2-ssh (and ecs-session, C09) accept `-u USER`, `-l USER`, `--user USER` and `USER@TARGET` as aliases. `common.unique_login()` merges them, and conflicting values are a usage error. `-l` is no longer `--list`.
- `USER@TARGET` splits on the last `@`, like ssh. The target is not first looked up as a whole.
- 3.1.0: ssm-port-forward and ssm-tunnel have no login option, but drop `-l` for `--list` too, so that `--list` is long-only everywhere. It's interactive, so scripts are unlikely to use it.
- ec2-session quotes the user with `shlex.quote` and refuses `--user` on Windows instances, which have no `sudo`.

## Consequences
- The same command line works across the tools. Breaking: `-l` for `--list`, and ec2-ssh conflicts now exit 2 instead of 1.
- A Name tag containing `@` is only reachable by instance ID.
- `--user` costs one `DescribeInstanceInformation` call, for the platform check.
- **Alternatives rejected:**
  - Resolving the whole `a@b` target first and splitting only on a miss would fetch the inventory twice for every `user@name`, and would behave differently from ec2-ssh.
  - Validating USER locally isn't needed: once it's quoted, sudo rejects unknown users itself.
