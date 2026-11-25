# 0022. ssm-port-forward: ssh-like options, a free port, and a relay

## Status
Accepted (2026-11-25)

## Context
Users asked for `--remote host:port`, a free local port by default, and listening on other addresses than 127.0.0.1 (e.g. 0.0.0.0 for other hosts), and know ssh's `-L`. session-manager-plugin 1.2.896.0 only ever listens on 127.0.0.1. `-g` is already `--region` in every tool.

## Decision
- `--remote [HOST:]PORT`, `--local [HOST:]PORT` and `-L PORT` or `-L [BIND:]PORT:HOST:HOSTPORT` (an empty BIND or `*` is all addresses). The old one-part options stay. Setting a part twice is a usage error. HOST `localhost` is the instance itself, with the plain document.
- The local port defaults to 0, any free port, which the plugin prints.
- For a local address other than 127.0.0.1, ssm-port-forward listens on it itself and relays each connection to the plugin on a free 127.0.0.1 port, with a thread each way. It warns when the address is reachable from the network.

## Consequences
- ssh users can reuse their `-L`, and the default never collides with a local service. Breaking: scripts relying on the local port matching the remote one need `--local`.
- The relay picks the plugin's port and frees it just before the plugin binds it: something else could take it in between. Connections that arrive before the plugin listens are retried for 20 s.
- **Alternatives rejected:** ssh's `-g` (taken by `--region`); waiting for the plugin to bind other addresses (no sign it will); `socat` (not on Windows, one more tool to install).
