# 0001. Run interactive sessions as a child job instead of exec

## Status
Accepted (2026-11-23)

## Context
`ec2-session` and `ecs-session` used `os.execvp("aws", ...)`. That left nothing around to restore the terminal when the session-manager-plugin exited.

The terminal was left `-icanon`, even on a clean exit, because plugin 1.2.707's own `stty` restore fails. After a kill it was also `-echo`, with vim/tmux modes still on, and the user needed `reset`.

Killing `ec2-session` killed only `aws`. The orphaned plugin then read the user's next local line and ran it on the instance.

## Decision
`ssm_tools.terminal.run_session()` runs `aws` as the terminal's foreground job, in its own process group, the way a shell runs a job.

It does three things:
- **Forwards signals.** TERM and HUP go to the whole group with `killpg`. INT, QUIT and TSTP are ignored in the parent; the tty delivers them to the child group anyway.
- **Kills any orphan.** After the child exits, the group gets a SIGTERM to catch a plugin that outlived `aws`.
- **Restores the terminal.** It takes the terminal back, restores the saved termios with `TCSADRAIN`, and writes a soft reset (`?1047l`, not `?1049l`, so the cursor isn't moved).

It returns the child's exit code, or 128+N for a signal. If stdin isn't our foreground tty, or on Windows, it falls back to a plain `subprocess.call()`.

## Consequences
- After a clean exit, a killed plugin, or a TERM/HUP to the tool, the terminal is sane and no plugin is left behind. Verified in the lab and covered by `tests/test_terminal.py`.
- **Accepted gaps:**
  - SIGKILL of the tool still orphans the plugin. `PR_SET_PDEATHSIG` can't reach the grandchild. In the lab the orphan then hit EIO on stdin and exited, so the next typed line was lost, not run remotely.
  - A dead network still needs `kill <pid>`, because the plugin never gives up on its own.
  - Ctrl-Z can't suspend the whole tool.
  - A parent-side escape key is impossible, since the parent never reads the tty. `Enter ~.` comes from plugin ≥ 1.2.835.0.
- **Alternatives rejected:**
  - Keeping the child in the same process group: in the lab this still leaked the orphan.
  - A pty proxy: too complex.
  - Running the plugin directly: done later, in ADR 0011, through this wrapper.
