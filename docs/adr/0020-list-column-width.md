# 0020. Cap the column width in --list and the picker

## Status
Accepted (2026-11-25)

## Context
One instance with a very long Name tag made the InstanceName column hundreds of characters wide. HostName and Addresses ended up off the screen in `--list`, and the picker cut them off.

## Decision
`print_targets()` cuts every cell wider than 50 terminal columns to 49 plus `…`. It counts display width, so wide characters never stick out. This applies to all tools and ECS containers too.

## Consequences
- The table stays readable whatever the names are. Lookups still use the full values, but the picker's `/` search only sees the cut text.
- **Alternative rejected:** shrinking only the widest columns to fit the terminal. It needs the terminal width, even when piped, and more code, just to show a few more characters.
