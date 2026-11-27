# 0026. Windows doesn't run programs from the current directory

## Status
Accepted (2026-11-27)

## Context
Windows looks for a program in the current directory before PATH, so an `ssh.exe`, `aws.exe` or `session-manager-plugin.exe` planted in the directory the user runs a tool from ran instead of the real one. On winclient that went for `shutil.which()`, `subprocess` and Win32-OpenSSH's ProxyCommand alike. Setting `NoDefaultCurrentDirectoryInExePath` stops it in all three, except that Python 3.11's `shutil.which()` ignores it.

## Decision
- On Windows, importing `ssm_tools` sets `NoDefaultCurrentDirectoryInExePath=1` unless it's set already. The children inherit it, so it covers ssh's ProxyCommand and the plugin as well.
- On Windows, `run_session()` passes the bare name to `Popen()` (CreateProcess), and returns 127 when it raises FileNotFoundError, instead of asking `shutil.which()`. POSIX is unchanged.

## Consequences
- With a planted copy in the cwd, the real programs run, on Python 3.11 too (checked on winclient).
- CreateProcess appends only `.exe`, not PATHEXT, so a `.bat` or `.cmd` wrapper named `ssh` or `session-manager-plugin` isn't found. It still searches the directory of the Python or PyInstaller exe first.
- **Alternatives rejected:**
  - `shutil.which()` with an explicit PATH: 3.11 prepends the cwd anyway.
  - Resolving each program ourselves: more code, and it wouldn't cover ssh's ProxyCommand or what aws and the plugin run.
