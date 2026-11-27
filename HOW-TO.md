# aws-ssm-tools HOW-TO

Short recipes for `ec2-session`, `ec2-ssh`, `ecs-session`, `ssm-port-forward` and `ssm-tunnel`.
For installation, `session-manager-plugin` versions and the IAM and SSM agent set-up see [README.md](README.md).

* [Common options](#common-options)
* [ec2-session](#ec2-session) - shell or command on an EC2 or hybrid instance
* [ec2-ssh](#ec2-ssh) - ssh, scp and rsync over SSM
* [ec2-ssh in ssh_config](#ec2-ssh-in-ssh_config) - plain `ssh`, `scp`, VS Code, etc. to `i-*`, `mi-*` and `NAME.ssm`
* [ecs-session](#ecs-session) - shell or command in an ECS container
* [ssm-port-forward](#ssm-port-forward) - local port to the instance or a host behind it
* [ssm-tunnel](#ssm-tunnel) - IP tunnel into the VPC (Linux only)

## Common options

### To list the instances you can connect to

```
ec2-session --list
ec2-session --profile prod --region eu-west-1 --list
```

`--list` is long-only in every tool: `-l` is the login user. `ec2-ssh`, `ssm-port-forward` and `ssm-tunnel` list the same instances.

### To use a profile and region from the environment

```
export AWS_PROFILE=prod AWS_DEFAULT_REGION=eu-west-1
ec2-session web1
```

`-p` / `-g` are short for `--profile` / `--region`, except in `ec2-ssh`, which takes only long options.
The tools use `AWS_DEFAULT_REGION`, not `AWS_REGION`, as boto3 does.

### To name the target instance

```
ec2-session i-0123456789abcdef0                                  # Instance ID
ec2-session mi-0123456789abcdef0                                 # Hybrid managed instance
ec2-session web1                                                 # Name tag
ec2-session ip-10-0-1-23.eu-west-1.compute.internal              # Host name
ec2-session 10.0.1.23                                            # IP address
```

A *Name* tag match wins over host names and IPs, which the instances report themselves.
A name that matches several instances opens the menu with just those: use the instance ID to skip it.

### To pick the instance from a menu

```
ec2-session
ssm-port-forward --remote 3389
```

Without a terminal (scripts, cron) the tools exit 2 instead of waiting: name the target.

### To record why you connected

```
ec2-session web1 --reason "INC-1234 disk full"
```

It's stored with the session, e.g. in `aws ssm describe-sessions`. 1-256 characters on one line, no short option.

### To get less or more output

```
ec2-session --quiet web1         # Only warnings and errors, no instance table
ec2-session --debug web1         # Debug log; ec2-ssh also adds ssh -v
ec2-session --version
```

`ssm-session` and `ssm-ssh` are the old names of `ec2-session` and `ec2-ssh`, and still work.

## ec2-session

### To open a shell as ssm-user

```
ec2-session web1
```

### To open a shell as another user

```
ec2-session ec2-user@web1
ec2-session -u ec2-user web1      # Same as -l ec2-user, --user ec2-user and --sudo ec2-user
```

Runs `sudo -i -u USER`, so it's Linux only and refused on Windows instances.

### To run one command

```
ec2-session web1 -c 'df -h /; uptime'
ec2-session web1 -c 'sudo -i -u postgres psql -c "select 1"'     # -c and --user exclude each other
```

The command reaches the instance's shell as typed. The exit code is always 0
(aws/session-manager-plugin#59): in scripts use `ec2-ssh web1 COMMAND`.

### To run a command that reads input

```
ec2-session web1 -c "printf '\r'; read -p 'Continue? ' x; echo \$x"
```

The plugin sends keystrokes only after the first output, so print something first.

### To open PowerShell on a Windows instance

```
ec2-session win1
ec2-session win1 -c 'Get-Service WinRM'
```

The SSM agent strips double quotes on Windows: use single quotes in `-c`.

### To start a session with another SSM document

```
ec2-session web1 --document-name AWS-StartInteractiveCommand --parameters '{"command": ["top"]}'
```

`--user`, `--command` and `--document-name` exclude each other.

### To end a session that stopped responding

Press `Enter` then `~.` (needs `session-manager-plugin` 1.2.835.0 or newer), or kill `ec2-session` from another terminal.

## ec2-ssh

`ec2-ssh` runs `ssh` with an SSM *ProxyCommand* and first pushes your public key to the instance
with *EC2 Instance Connect* (valid for 60 s). It needs the AWS CLI.

### To ssh to an instance by name

```
ec2-ssh ec2-user@web1
ec2-ssh -l ec2-user web1          # Same as -u ec2-user and --user ec2-user
ec2-ssh web1                      # The user ssh would use, e.g. User from a "Host web1" stanza
```

ssh connects to the instance ID, so `Host i-*` stanzas in `~/.ssh/config` apply. From a `Host web1` stanza only `User` is used.

### To run a remote command and get its exit code

```
ec2-ssh ec2-user@web1 systemctl is-active nginx; echo $?
ec2-ssh ec2-user@web1 ls -la /var/log
```

Options after the instance name belong to the remote command, as in `ssh`.

### To use ssh options

```
ec2-ssh -i ~/.ssh/work_ed25519 ec2-user@web1
ec2-ssh -A -t ec2-user@web1 sudo -i
ec2-ssh -N -L 3306:db.cluster-xyz.eu-west-1.rds.amazonaws.com:3306 ec2-user@web1
ec2-ssh -o ConnectTimeout=30 ec2-user@web1
```

All short options are passed to `ssh` (`-u` is the only one `ec2-ssh` takes). Its own options are long-only and can't be abbreviated.

### To copy files with rsync, scp or sftp

```
rsync -e ec2-ssh -av ./site/ ec2-user@web1:site/
rsync -e "ec2-ssh --profile prod --region eu-west-1" -av ec2-user@web1:/var/log/app/ ./logs/
scp -S ec2-ssh ./app.tar.gz ec2-user@web1:/tmp/
sftp -S ec2-ssh ec2-user@web1
```

`-S` takes no arguments: set `AWS_PROFILE` / `AWS_DEFAULT_REGION`, or use [ec2-ssh in ssh_config](#ec2-ssh-in-ssh_config).

### To connect with a one-off key

```
ec2-ssh --gen-key ec2-user@web1
```

Pushes a new Ed25519 key made for this connection, offers only that one, and deletes it when ssh exits.
Useful when you have no RSA or Ed25519 key, or yours is encrypted and not in the agent.

### To connect without pushing a key

```
ec2-ssh --no-send-key -i ~/.ssh/web1.pem ec2-user@web1
ec2-ssh ec2-user@mi-0123456789abcdef0
```

For instances that already have your key, or have no EC2 Instance Connect.
Hybrid `mi-` instances never get a key pushed, so theirs must be in `authorized_keys`.

### To connect through an EC2 Instance Connect Endpoint

```
ec2-ssh --use-endpoint ec2-user@i-0123456789abcdef0
```

Uses `aws ec2-instance-connect open-tunnel` (AWS CLI 2.12.0+) instead of SSM, so the instance needs no SSM agent.
Names are still looked up in SSM: give instances without the agent by ID. EC2 only, no `mi-`, no `--reason`.

### To ssh to a host that isn't in SSM

```
ec2-ssh my-alias
```

A name that doesn't resolve to an SSM instance is passed to `ssh` as it is, e.g. a `Host` alias from `~/.ssh/config`.

### To keep or change the keepalive

`ec2-ssh` adds `-o ServerAliveInterval=15` unless the ssh_config or `-o` sets one, so ssh exits about a minute after the SSM connection dies.
To turn keepalives off, set a very large value, e.g. `-o ServerAliveInterval=86400`.

## ec2-ssh in ssh_config

### To let plain ssh, scp, rsync and sftp reach instances

```
ec2-ssh --print-ssh-config >> ~/.ssh/config
ssh ec2-user@i-0123456789abcdef0
ssh ec2-user@web1.ssm
scp ./app.tar.gz ec2-user@web1.ssm:/tmp/
rsync -av ./site/ ec2-user@web1.ssm:site/
sftp ec2-user@web1.ssm
```

It prints a `Host i-* mi-* *.ssm` stanza whose *ProxyCommand* is `ec2-ssh --proxy`, which pushes the key
and starts the session without the AWS CLI. No picker, `--gen-key` or `--use-endpoint` there.
In Windows PowerShell 5.1 `>>` writes UTF-16, which ssh can't read: paste the output into the file instead.

### To bake a profile, region, reason or --no-send-key into it

```
ec2-ssh --profile prod --region eu-west-1 --reason "Ops access" --no-send-key --print-ssh-config >> ~/.ssh/config
```

Those four are the only options that go with `--print-ssh-config`. One stanza, so one profile.

### To reach an instance in another region

```
ssh ec2-user@web1.us-east-1.ssm
```

The region in `NAME.REGION.ssm` wins over the `--region` in the stanza.
If the part before `.ssm` doesn't look like a region, the dots are part of the name, e.g. `api.prod.ssm` is the instance *Name* `api.prod`.

### To choose the key it pushes

```
Host i-* mi-* *.ssm
    ProxyCommand /usr/local/bin/ec2-ssh --quiet --proxy %r@%h:%p --ssh-host %n
    ServerAliveInterval 15
    IdentityFile ~/.ssh/work_ed25519
```

The proxy pushes the first RSA or Ed25519 key that ssh will offer for the host as typed, from the ssh_config and agent.
A `-i` or an `ssh -F` config doesn't reach the proxy, so put the key in `~/.ssh/config` or the agent.

### To give an instance a short alias with its own key

```
Host web1
    HostName web1.ssm
    User ec2-user
    IdentityFile ~/.ssh/web1_ed25519
    IdentitiesOnly yes
    ProxyCommand /usr/local/bin/ec2-ssh --quiet --proxy %r@%h:%p --ssh-host %n
```

ssh doesn't match `Host i-* mi-* *.ssm` again after `HostName`, so the alias needs its own `ProxyCommand` line.

### To use it with git, VS Code Remote-SSH and Ansible

```
git clone ec2-user@web1.ssm:repos/app.git
ansible all -i 'web1.ssm,' -u ec2-user -m ping
```

In VS Code *Remote-SSH: Connect to Host* enter `ec2-user@web1.ssm`.
The stanza has the full path to `ec2-ssh`, for programs that don't use your `PATH`.

## ecs-session

### To list the containers you can exec into

```
ecs-session --list
ecs-session --cluster my-cluster --list
```

Only containers with ECS Exec enabled and its agent running are listed. The skipped ones are explained.

### To open a shell in a container

```
ecs-session --cluster my-cluster web                      # Service name, also service:web
ecs-session 0123456789abcdef0123456789abcdef              # Task ID
ecs-session web sidecar                                   # Service AND container name
ecs-session 10.0.1.23                                     # Container IP
```

Keywords match the service or task family, task ID, container name or IP, in any case, and all must match.
Several matches open the menu with just those.

### To run a shell command

```
ecs-session web -c 'ps aux | grep -c node; echo $HOSTNAME'
```

`-c` runs through `/bin/sh -c`, so pipes, `;` and `$VARS` work. The exit code is always 0.

### To run a command without a shell (distroless)

```
ecs-session web -- /app/healthcheck --verbose
```

Everything after `--` is the exact argv, run without a shell.

### To run as a user other than root

```
ecs-session nobody@web
ecs-session -u 65534 web -c id                # Same as -l 65534 and --user 65534
```

ECS Exec always runs as root. The switch needs `/bin/sh` and `runuser`, `su` or `chroot --userspec` in the container.
A numeric uid that isn't in the container's `/etc/passwd` may fail.

## ssm-port-forward

### To forward a port on the instance

```
ssm-port-forward web1 --remote 8080
```

Listens on `127.0.0.1` on a free port, which the plugin prints. Use `--local PORT` for a fixed one.

### To forward RDP from a fixed local port

```
ssm-port-forward win1 --remote 3389 --local 13389
```

Then connect the RDP client to `localhost:13389`.

### To reach a host behind the instance, e.g. RDS

```
ssm-port-forward bastion --remote db.cluster-xyz.eu-west-1.rds.amazonaws.com:5432 --local 5432
ssm-port-forward bastion -L 5432:db.cluster-xyz.eu-west-1.rds.amazonaws.com:5432
```

`-L` takes ssh's `[BIND:]PORT:HOST:HOSTPORT`, where HOST `localhost` is the instance itself. `-L PORT` alone is the local port.

### To let other hosts connect

```
ssm-port-forward bastion --remote db.cluster-xyz.eu-west-1.rds.amazonaws.com:5432 --local 0.0.0.0:5432
ssm-port-forward bastion -L '*:5432:db.cluster-xyz.eu-west-1.rds.amazonaws.com:5432'
ssm-port-forward bastion --remote 8080 --local '[::]:8080'
```

`0.0.0.0`, `*` and `[::]` are reachable from the network. The plugin only listens on 127.0.0.1, so `ssm-port-forward` relays these itself.

### To set one part at a time

```
ssm-port-forward bastion --remote-host db.cluster-xyz.eu-west-1.rds.amazonaws.com --remote-port 5432 --local-port 5432
ssm-port-forward bastion -H db.cluster-xyz.eu-west-1.rds.amazonaws.com --remote-port 5432 --local-host 0.0.0.0
```

Setting the same part twice, e.g. `--remote 5432 --remote-port 5433`, is an error.

### To debug a forward that doesn't work

The SSM agent on the instance connects to the remote host. If it's refused or doesn't resolve,
the session ends with exit code 0. If a security group drops it, your connection hangs.
Test from the instance itself:

```
ec2-session bastion -c 'timeout 5 bash -c "</dev/tcp/db.cluster-xyz.eu-west-1.rds.amazonaws.com/5432" && echo open'
```

## ssm-tunnel

`ssm-tunnel` is Linux only, needs `sudo` locally for the tun device and routes,
and needs `ssm-tunnel-agent` on the instance.

### To install ssm-tunnel-agent on the instance

```
python3 -c 'import ssm_tools.ssm_tunnel_agent as a; print(a.__file__)'
scp -S ec2-ssh "$(python3 -c 'import ssm_tools.ssm_tunnel_agent as a; print(a.__file__)')" ec2-user@web1:/tmp/ssm-tunnel-agent
ec2-ssh ec2-user@web1 sudo install -m 755 /tmp/ssm-tunnel-agent /usr/local/bin/ssm-tunnel-agent
```

Use the `python3` that has aws-ssm-tools installed. The instance needs Python 3.8+, `ip`, `iptables`, and `sudo` for `ssm-user`.
`ssm-tunnel` says where the file is if the agent is missing, and warns if it's older than yours: copy it again to update.

### To route VPC traffic through the instance

```
ssm-tunnel web1 --route 10.0.0.0/16
ssm-tunnel web1 -r 10.0.0.0/16 -r 172.16.0.0/12
```

Traffic leaves from the instance's IP, so the targets' security groups must allow it. `-r` is `--route`, not `--reason`.

### To use other tunnel endpoint IPs

```
ssm-tunnel web1 --route 10.0.0.0/16 --tunnel-cidr 192.168.250.0/24
```

The endpoints are random IPs in `100.64.0.0/16` by default. The CIDR must be /29 or bigger.

### To run a script when the tunnel goes up and down

```
ssm-tunnel web1 --route 10.0.0.0/16 --up-down ~/bin/ssm-tunnel-updown
```

Called as `SCRIPT up|down DEVICE LOCAL_IP REMOTE_IP [ROUTE ...]`, without a shell, so it's a path with no arguments.
[ssm-tunnel-updown.dns-example](ssm-tunnel-updown.dns-example) sets DNS servers for private Route 53 zones.
