# 0025. A watchdog ends sessions whose target is gone

## Status
Accepted (2026-11-27)

## Context
When the target of a session dies, session-manager-plugin 1.2.896.0 waits forever (the upstream fix was reverted), and the session stays Active. In the lab, ec2-session, ecs-session and ssm-port-forward were all still running 10 minutes after their EC2 instance or ECS task was stopped.

## Decision
`run_plugin()` runs a `Watchdog` thread that calls `ssm:GetConnectionStatus` on the session's target every 10 s. The same call works for `i-`, `mi-` and `ecs:` targets. When the status isn't `connected` twice in a row, so that an SSM agent that restarts or reconnects doesn't end the session, it logs a warning, sends SIGTERM to the plugin, sends SIGKILL 3 s later if the plugin is still running, and then terminates the session. A ClientError (e.g. no permission for the call) stops the watchdog. Other errors are retried at the next interval. Profiles with `mfa_serial` aren't watched, because refreshing their credentials from the thread would prompt for a code in the middle of the session.

## Consequences
- A dead target now ends the session within about 20-30 s. The tool exits with 143 and the session ends up Terminated.
- One API call every 10 s for each session. Users without `ssm:GetConnectionStatus` get one AccessDenied in CloudTrail per session and no watchdog.
- ec2-ssh's ProxyCommand session gets the watchdog too, so it no longer waits the 60 s of ServerAliveInterval (ADR 0023).
- **Alternatives rejected:**
  - DescribeTasks (lastStatus STOPPED) for ECS together with PingStatus or the instance state for EC2: two or three APIs and permissions, where one call covers every target type. A stopped instance also drops out of DescribeInstanceInformation altogether, rather than showing ConnectionLost.
  - SIGINT first: the plugin passes it on to the remote shell as Ctrl-C.
  - A new option to turn the watchdog off: none is needed until a false alarm shows up.
