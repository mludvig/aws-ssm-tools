# 0024. ec2-ssh as ssh's ProxyCommand

## Status
Accepted (2026-11-26)

## Context
Only `ec2-ssh` itself knew instance names and EC2 Instance Connect keys. Tools that run plain `ssh` (scp, VS Code Remote-SSH, Ansible, git) each needed their own setup, and AWS's documented `aws ssm start-session` stanza handles instance IDs only, with no key, `--reason` or session cleanup.

## Decision
- `ec2-ssh --proxy %r@%h:%p` resolves the host, sends the key for the user ssh settled on, and runs session-manager-plugin on a boto3 StartSession (ADR 0011), with stdin and stdout as the connection. It's one value because ec2-ssh's ssh-style parsing gives its own options one value each.
- `--print-ssh-config` prints `Host i-* mi-* *.ssm` with that ProxyCommand (the absolute ec2-ssh path, for VS Code and the like) and `ServerAliveInterval 15` (ADR 0023). The ProxyCommand has `--quiet`, so scp and rsync don't print the instance table every time, plus the `--profile`, `--region`, `--reason` and `--no-send-key` it was given. A PyInstaller exe uses `sys.executable`, as its `argv[0]` is the name typed. The stanza is written as UTF-8, which ssh expects.
- `NAME.REGION.ssm` selects the region when REGION looks like one (`xx-word-N`, or `eusc-word-N`), overriding `--region`; otherwise the dots are part of the name.
- Plain `ec2-ssh HOST` stays as it was.

## Consequences
- Any ssh-based tool reaches instances by ID or name after one setup step, without the AWS CLI.
- The key is chosen from `ssh -G` for `%r@%h`: a `-i` given to ssh itself is invisible to a ProxyCommand, and so are an `IdentityFile` under a `Host` alias whose `HostName` is the instance, and an `ssh -F` config. There's no picker, and `--gen-key` and `--use-endpoint` don't apply.
- Warnings, e.g. an old plugin, still go to stderr on every connection.
- **Alternatives rejected:**
  - Making plain `ec2-ssh` call itself as the ProxyCommand: it needs our own path inside the Windows exe too, and the current mode works.
  - `--proxy %h %p %r` as three words: ec2-ssh would take the last two for ssh's remote command.
  - A `NAME.PROFILE.ssm` form: not asked for, and profile names can contain dots.
