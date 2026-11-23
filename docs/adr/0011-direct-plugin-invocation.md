# 0011. Start sessions through boto3 and run session-manager-plugin directly

## Status
Accepted (2026-11-23)

## Context
`ec2-session`, `ecs-session` and `ssm-port-forward` ran `aws ssm start-session` or `aws ecs execute-command`. The tool never learnt the SessionId, so a killed plugin left the session Active until it timed out. The tools also needed the AWS CLI.

## Decision
- **Starting the session.** The tools call `ssm.start_session()` or `ecs.execute_command()` themselves. `run_plugin()` in `ssm_tools/plugin.py` then runs `session-manager-plugin` the way aws-cli does: `session-manager-plugin AWS_SSM_START_SESSION_RESPONSE <region> StartSession <profile> <parameters> <ssm endpoint>`. The session itself, including its token, is passed in that environment variable.
- **Ending the session.** If the plugin exits non-zero, is killed, can't be found or the tool is interrupted, `run_plugin()` calls `TerminateSession`. A clean exit has already ended the session. A failure only warns that the session may still be active, unless the session has ended already (`ValidationException`). So does Ctrl-C or EOF, e.g. at an MFA prompt for expired credentials.
- **ECS.** The target is `ecs:<cluster>_<task id>_<runtime id>`, as aws-cli sends it. The endpoint is the SSM one, because the plugin talks to SSM and `TerminateSession` works on ECS Exec sessions.
- **Interface.** `--parameters` takes JSON only. The plugin must be 1.2.497.0 or newer, the first version that reads the environment variable.

## Consequences
- Sessions no longer outlive a killed plugin, TERM or HUP. Only SIGKILL of the tool itself still leaks one.
- These three tools no longer need the AWS CLI. `ec2-ssh` and `ssm-tunnel` still do.
- Breaking changes: the `key=value` shorthand for `--parameters`, as boto3 takes structured parameters, and plugins older than 1.2.497.0.
- Cleanup needs `ssm:TerminateSession`, which ECS Exec policies often lack. Without it the user gets the warning.
- On Windows, Ctrl-C ends `ssm-port-forward` with 130 and an extra `TerminateSession`, where it used to exit 0.
- **Alternatives rejected.**
  - Keep exec'ing `aws`: there is no SessionId to terminate.
  - Always call `TerminateSession`: after a clean exit it fails with `ValidationException`, which is noise in CloudTrail.
  - Pass the session in argv for older plugins: the token would show in `ps`.
  - Use the ECS endpoint, as aws-cli does: the plugin sends its own SSM calls (e.g. `ResumeSession` on reconnect) to that endpoint.
