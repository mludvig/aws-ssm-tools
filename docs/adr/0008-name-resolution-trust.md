# 0008. Names beat what instances report about themselves

## Status
Accepted (2026-11-23)

## Context
Instances resolve by Name, host name or IP, all matched with equal weight. The host name, and for hybrid (`mi-`) nodes also the IP, is reported by the instance's own SSM agent (`ssm:UpdateInstanceInformation`). In the lab a hybrid node renamed itself to an existing Name and blocked it ("Found 2 instances"). It also renamed itself to a stopped instance's Name and got that instance's sessions.

## Decision
`InstanceResolver.resolve_instance` matches in this order, and the first tier with a match wins:
1. the exact instance ID;
2. the Name (EC2 Name tag, or the SSM name of a hybrid node), which the instance can't change;
3. the host name or IP.

A Name match that beats another instance's host name or IP logs a warning naming the ignored instance. A match only on host name or IP logs which field matched, as a warning for `mi-` and info for `i-`. Two matches in the same tier are still an error.

## Consequences
- A node can no longer block or take over a Name. It can still claim host names and IPs that no Name matches, but the user is told.
- A hybrid node's SSM name comes from the activation, and every node registered with that activation code gets it. Anyone who holds the code can mint nodes with that Name.
- **Alternatives rejected:**
  - Taking i- host names from EC2's PrivateDnsName would change the names users see and type, e.g. Windows `EC2AMAZ-…`.
  - Refusing self-reported matches would break the common "connect by IP" use.
  - Typed selectors (`name:`, `ip:`) stay on the idea list.
