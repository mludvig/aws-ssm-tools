# 0019. Windows and macOS fixes for 3.0.0

## Status
Accepted (2026-11-25)

## Context
Tested on a Windows Server 2022 client (Python 3.14, Win32-OpenSSH 9.5, PowerShell 5.1) and a macOS client (Python 3.13). Everything worked on both, except:
- **Windows, ec2-ssh:** `os.execvp()` doesn't replace the process. It starts ssh and returns at once, so the exit code is lost and ssh's output comes after the prompt.
- **Windows, ProxyCommand:** Win32-OpenSSH starts it with `CreateProcessW()`, with no shell. aws.exe parses its command line by the MSVC rules, where single quotes are ordinary characters. So `shlex.quote()` breaks any value it quotes.
- **Windows, Ctrl-C:** the console sends it to the plugin as well as to us. The plugin passes it to the remote, but we got a KeyboardInterrupt when the plugin exited. We returned 130 and called TerminateSession for no reason.
- **Windows, ssm-tunnel:** `import fcntl` crashed before the "only works on Linux" message.
- **macOS, Ctrl-Y:** this is DSUSP. It breaks session-manager-plugin's stdin read: the session ends with `read /dev/stdin: resource temporarily unavailable`.

## Decision
- **ec2-ssh** runs ssh with `run_session()` on Windows, as it already does for `--gen-key`.
- **The ProxyCommand** is quoted with `subprocess.list2cmdline()` on Windows, and `%` is still escaped as `%%`.
- **On Windows**, `run_session()` ignores Ctrl-C while the child runs, and returns the child's exit code.
- **ssm-tunnel** imports `fcntl` only where it opens the TUN device, so it prints its "Linux only" error on Windows too. pip still installs it; entry points can't be per-platform.
- **On macOS**, `run_session()` disables DSUSP while the session runs. It restores the setting with the rest of the terminal settings.
- **CI** runs the tests on windows-latest and macos-latest too, with one Python version each.

## Consequences
- Each of these code paths is labelled with a pointer to this ADR.
- PowerShell 5.1 still mangles `"` inside native-command arguments. That's PowerShell's issue; PowerShell 7.3+ doesn't do it.
- **Alternative rejected:** dropping Windows support. The fixes are small, and the Windows exe build already exists.
