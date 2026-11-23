# 0009. Target picker: a gated menu with a numbered fallback

## Status
Accepted (2026-11-23)

## Context
With no target given, every tool opens simple-term-menu. The lab found these problems:
- It crashes with TERM unset or unknown, with no `/dev/tty`, and on Windows.
- It blocks scripts, because it reads `/dev/tty` even with `</dev/null`.
- Esc or `q` exits 0.
- On a narrow terminal, a row with wide characters (CJK, emoji) puts the cursor one row off, so Enter connects to the wrong instance.
- Ctrl-\ and SIGTERM skip its terminal restore, and Ctrl-Z jams it.

## Decision
`picker.target_selector()`:
- needs stdin and stdout to be terminals, or it exits 2 with "No target given";
- uses the menu when TERM is set and not `dumb`, with every row cut to the terminal width in display columns (wcwidth), control characters shown as `?`, and `|` escaped;
- falls back to a numbered list on stderr, read from stdin, when the menu can't start (ImportError, NotImplementedError, OSError, CalledProcessError). Thanks to PR #56 for the idea;
- while the menu is up, ignores SIGTSTP, and turns SIGQUIT, SIGTERM and SIGHUP into an exit of 128+N, so that the menu's own `finally` restores the terminal;
- exits 130 with "No target selected" on cancel, and echoes the choice to stderr.

A name that matches several targets opens the same picker with only those targets (`resolver.choose_target`). Without a terminal, they are listed on stderr and the tool exits 1, as gcloud does.

## Consequences
- Scripts get an error instead of a hang. Windows and dumb terminals get a usable picker.
- Adds `wcwidth` as a dependency. simple-term-menu is no longer installed on Windows.
- **Alternatives rejected:**
  - Replacing the menu with our own code or `questionary` is more code or a heavier dependency, for the same result.
  - fzf (or `iterfzf`) is better at long lists. It's on the idea list as an optional extra when installed.
