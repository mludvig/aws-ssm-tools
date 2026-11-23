# 0003. ec2-ssh reads its command line the way ssh does

## Status
Accepted (2026-11-23)

## Context
`ec2-ssh` passed `parse_known_args()` leftovers to a loop that looked for `-l`, `-i` and an instance name anywhere in argv. As a result:
- Options in the remote command were taken as ssh's. `ls -la` pushed the key for user `a`, and `rsync` broke.
- Our own options, abbreviations included, were pulled out of the remote command.
- `-l` was appended after the destination, which broke `scp` and `--`.
- An unresolved name opened the picker.

## Decision
`split_ssh_argv()` is a getopt-style walker over OpenSSH's flag tables:
- It reads options before and after the destination, up to the first word after it, which starts the remote command. `--` ends the options, as in ssh.
- Long options, and any short ones `ec2-ssh` claims, are ours. Abbreviations are off.
- The remote command is never inspected.

`ec2-ssh` emits `ssh PROXY OPTIONS -- [user@]INSTANCE COMMAND`. A name that doesn't resolve is passed to ssh untouched, so `~/.ssh/config` aliases work. The picker opens only when no destination is given.

## Consequences
- `rsync -e ec2-ssh`, `scp` (sftp and `-O`) and remote commands with options work. Verified in the lab and in `tests/test_ssh_argv.py`.
- **Breaking:**
  - `--prof` style abbreviations are rejected.
  - Our options after the remote command starts go to the remote command.
  - A typo now reaches ssh instead of opening the picker.
- **Alternatives rejected:**
  - Requiring `--` before the ssh part breaks every existing `rsync -e`/`scp -S` use.
  - Asking `ssh -G` to split argv: it doesn't report where the command starts.
