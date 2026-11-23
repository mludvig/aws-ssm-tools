# 0007. ecs-session switches user inside the container

## Status
Accepted (2026-11-23)

## Context
ECS Exec always runs as root, even when the task definition sets a container `user`, and the API has no user parameter. ADR 0006 gives every session tool `-u`/`-l`/`--user`/`USER@`, so ecs-session needs a way to honour it. Container images differ widely: al2023 has only `chroot --userspec`, alpine and busybox only `su`, and distroless images have no shell at all.

## Decision
With a user, ecs-session sends `/bin/sh -c SCRIPT USER COMMAND`, built with `shlex.join` (the agent splits it like a shell). `USER_SWITCH_SCRIPT` maps a numeric uid to its name and sets HOME from `/etc/passwd`. It then `exec`s the first of `runuser`, `su -s /bin/sh` or `chroot --userspec` that exists, and otherwise exits 126 with an error naming the missing tools. USER must match `[A-Za-z0-9_][A-Za-z0-9_.-]*`, because `su` and `runuser` would take a leading `-` as an option. There is no default user (Q7): without `-u` it's root, as before.

## Consequences
- `-u` works on al2023, alpine, busybox, EC2 and arm tasks, both interactively and with `-c`. Verified in the lab.
- It needs `/bin/sh` and one of the three tools, so it doesn't work on distroless images. The session's exit status is the plugin's, not the command's.
- **Alternatives rejected:**
  - Refusing `-u` for ecs-session leaves the one tool where switching user matters most without it.
  - Defaulting to the task definition's user would silently change what `ecs-session task` does today. It also needs an extra DescribeTaskDefinition call.
