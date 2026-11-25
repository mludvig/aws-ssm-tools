# 0021. Warn below the plugin version AWS supports

## Status
Accepted (2026-11-25). Extends 0002.

## Context
AWS's plugin install docs say: update to session-manager-plugin 1.2.764.0 or later, because Session Manager will soon stop supporting earlier versions.

## Decision
Every tool that runs the plugin warns below 1.2.764.0, with the install link. For plugins that old this replaces the `Enter ~.` warning, so there's only ever one. The hard minimums stay where a feature needs them (1.2.497.0, and 1.1.23 for ec2-ssh).

## Consequences
- Users see the reason before AWS starts rejecting their sessions, and working setups keep working until then.
- **Alternative rejected:** requiring 1.2.764.0. That would break setups that still work, before AWS does.
