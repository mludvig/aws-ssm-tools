# 0015. ecs-session runs -c through a shell, and '--' runs an exact argv

## Status
Accepted (2026-11-24)

## Context
ECS Exec's agent splits the `command` string like a shell, but it runs the result without one. So `-c 'id | cat'` passed `|` and `cat` to `id` as arguments, and `$VAR` came through unexpanded. Users expect `-c` to work like `ssh host 'cmd'` and `ec2-session -c`. Distroless images have no `/bin/sh` at all.

## Decision
- `-c COMMAND` runs `/bin/sh -c COMMAND`, so pipes, `$VARS` and `;` work.
- `ecs-session CONTAINER -- COMMAND [ARG ...]` runs that argv with no shell. We `shlex.join()` it, and the agent's splitting gives back the same argv. The split happens at the first `--`, before argparse sees it. `--` with nothing after it, or together with `-c`, is a usage error.
- With `-u` the user switch already runs a shell (ADR 0007). It gets `-c` as is, or the `--` argv quoted with `shlex.join()`.
- No command still runs `/bin/sh`.

## Consequences
- Breaking: `-c` now needs `/bin/sh` in the container. Distroless users switch to `--`.
- Behaviour change: a `-c` that relied on the agent's no-shell splitting now gets shell expansion. Quote it as for `sh -c`.
- **Alternatives rejected.**
  - Keep `-c` as is and only document `-c "sh -c '…'"`: that's nested quoting for the common case.
  - Detect shell syntax in `-c` and add `sh -c` only then: magic, and it guesses wrong on quotes.
  - Only `--`, with no shell for `-c`: `-c` would still differ from `ec2-session -c`.
