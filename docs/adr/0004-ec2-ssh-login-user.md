# 0004. ec2-ssh logs in as the user ssh would use

## Status
Accepted (2026-11-23)

## Context
EC2 Instance Connect needs the login user before ssh runs. `ec2-ssh` only knew `-l` and `user@`, so plain `ec2-ssh name` exited with "Unable to figure out the EC2 login name". It ignored ssh_config `User`, `-o User=` and its own `--user`, and the picker path never had a user. ssh itself only ever sees the instance ID, so `Host name` stanzas in ssh_config never match.

## Decision
- `-u USER`, `--user USER`, `-l USER` and `USER@INSTANCE` all set the login. If they disagree, that's an error. ssh has no `-u`, so claiming it is safe.
- Without any of these, `ssh -G OPTIONS -- NAME` gives the user that ssh would use for the name that was typed (or the ID after the picker). Precedence follows ssh: `-l` or `user@`, then `-o User=`, then the ssh_config.
- ssh still connects to the instance ID, and the user is always passed explicitly as `USER@ID`. The key is pushed for that same user.

## Consequences
- `ec2-ssh name` works the way `ssh name` would, including `User` from a `Host name` stanza.
- **Accepted gaps:**
  - When connecting by name, the user comes from the name, not from a `Host i-*` stanza. Every other `Host i-*` setting still applies.
  - From a `Host name` stanza only `User` is used. `IdentityFile`, `Port` etc. don't apply, because ssh connects to the ID.
- Without any configured user it's the local username, as with plain ssh. That usually fails on EC2, with ssh's own error.
- **Alternatives rejected:**
  - `ssh -o HostName=ID name` would apply `Host name` stanzas in full, but it silently breaks existing `Host i-*` setups (Q2).
  - Probing both the name and the ID can't tell which `User` was configured on purpose.
