# Changelog

## 3.0.0 (unreleased)

A reliability, security and consistency pass over all the tools. The decisions behind each change are in [docs/adr](docs/adr).

### Breaking changes
- **Python 3.11 or newer is required.** Python 3.10 reached end of life in October 2026; on 3.10, pip keeps installing 2.2.1.
- **`-l` is no longer `--list`** in ec2-session, ecs-session, ssm-port-forward or ssm-tunnel. `-u`, `-l`, `--user` and `USER@INSTANCE` are now the same login option everywhere. `--list` is long-only.
- **`--reason` has no short form** any more: `-r` is gone from ec2-session and ssm-port-forward. ssm-tunnel keeps `-r` for `--route`.
- **ssm-tunnel: `--up-down` is just a script path.** It runs without a shell as `SCRIPT up|down DEVICE LOCAL REMOTE [ROUTE ...]`. Update `ssm-tunnel-agent` on your instances; ssm-tunnel warns when the agent is too old.
- **ecs-session `-c` runs through `/bin/sh -c`**, so pipes and quotes work. For containers without a shell (distroless), use `ecs-session CONTAINER -- COMMAND [ARG ...]`.
- **ec2-session `-c '"…"'`**: the outer-quote workaround from #43 is no longer needed, and the quotes now reach the shell.
- An empty `--reason ""` is now an error, and so is anything StartSession would reject (over 256 characters, more than one line).

### Changes
- Sessions killed or cut off no longer hang or leave the terminal broken. ec2-session and ecs-session run session-manager-plugin themselves and restore the terminal. All tools warn when the plugin is too old to support `Enter ~.`.
- ec2-ssh parses its command line the way ssh does, logs in as the user ssh would use, quotes its ProxyCommand, sends the key that ssh will offer, and gets a new `--gen-key`.
- Hybrid managed instances (`mi-…`) work by name and with ec2-ssh. A self-reported host name can no longer take over another instance's Name.
- The target picker selects the row it shows, falls back to a numbered prompt, and handles ambiguous names. Status output stays off stdout.
- ecs-session can run as a user other than root (`-u`), handles tasks without an ENI, matches case-insensitively, explains skipped containers and gives hints for ECS Exec errors.
- ssm-tunnel gets its own session and detects a dead agent. It exits and cleans up when killed, and runs nothing through a shell.
- Ports, the local port and `--reason` are checked before a session starts.
- The `aws sso login` hint only appears for SSO profiles. botocore's tracebacks are hidden unless `--debug` is given (#53).
- CI tests Python 3.11–3.15 and smoke-tests the Windows exes. Release files are published as they were tested, with SHA256SUMS and a build provenance attestation.

### Known issues
- ec2-session and ecs-session don't return the remote command's exit code (aws/session-manager-plugin#59). In scripts, use `ec2-ssh INSTANCE COMMAND`.

## Older releases
See [GitHub releases](https://github.com/mludvig/aws-ssm-tools/releases).
