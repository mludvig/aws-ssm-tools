# 0010. ec2-ssh sends the key that ssh will offer, or a throwaway one with --gen-key

## Status
Accepted (2026-11-23)

## Context
EC2 Instance Connect accepts only RSA and Ed25519 keys, valid for 60 seconds. `ec2-ssh` picked the first agent key, then `~/.ssh/id_rsa.pub` or `id_dsa.pub`. That pushed ECDSA keys, which EIC rejects; it never found `id_ed25519`; and it ignored `IdentitiesOnly` and `IdentityFile`, so ssh offered a different key from the one pushed. It also matched `-i` against agent keys by their comment, and prompted for a passphrase. A key generated per connection was tried before, but deleting it wasn't reliable once `ec2-ssh` had exec'd ssh, so keys piled up in /tmp.

## Decision
- **Default.**
  - `ssh -G` for `USER@INSTANCE_ID` gives `identityfile`, `identitiesonly` and `identityagent`.
  - Keys are ordered like OpenSSH's `pubkey_prepare()`:
    1. The agent's copies of the files.
    2. Other agent keys, unless `IdentitiesOnly`.
    3. The remaining files.
  - The first RSA or Ed25519 key is sent. Keys are compared by SHA256 fingerprint.
  - A file without a `.pub` is read with `ssh-keygen -y -P ""`, or, if encrypted, fingerprinted with `ssh-keygen -l` to find its agent copy. There is no passphrase prompt.
- **`--gen-key`.**
  - Creates an Ed25519 key in a `TemporaryDirectory`.
  - Prepends `-i KEY -o IdentitiesOnly=yes` to the ssh options, so the same selection sends it and ssh offers it alone.
  - Runs ssh as a child through `run_session()`, and deletes the directory when ssh exits. Without `--gen-key`, ssh is still exec'd.

## Consequences
- **Default.** The pushed key is the one ssh authenticates with. An encrypted key without a `.pub` that isn't in the agent can't be sent, and the error suggests `-i`, `--gen-key` or `--no-send-key`.
- **`--gen-key`.**
  - Works without any key of the user's, and leaves nothing behind on a normal exit, Ctrl-C, TERM or HUP. Only SIGKILL leaves the directory behind, holding a key that is useless after 60 seconds.
  - Its `-o IdentitiesOnly=yes` comes first, so it wins over the user's own setting.
  - It keeps a Python process alive for the length of the session.
- **Alternatives rejected.**
  - An agent-only key (`ssh-add -t 60`) needs an agent, and is offered after the agent's other keys, which risks MaxAuthTries.
  - Running every connection as a child would also allow a hint when auth fails on an instance without EIC. That hint is left to the ProxyCommand mode, so that `rsync -e ec2-ssh` keeps its plain exec.
