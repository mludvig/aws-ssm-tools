# aws-ssm-tools - AWS System Manager Tools

[![CI/CD](https://github.com/mludvig/aws-ssm-tools/actions/workflows/ci.yml/badge.svg)](https://github.com/mludvig/aws-ssm-tools/actions)
[![PyPI](https://img.shields.io/pypi/v/aws-ssm-tools.svg)](https://pypi.org/project/aws-ssm-tools/)
[![Python Versions](https://img.shields.io/pypi/pyversions/aws-ssm-tools.svg)](https://pypi.org/project/aws-ssm-tools/)

Helper tools for AWS Systems Manager: `ec2-session`, `ec2-ssh`, `ssm-port-forward` and `ssm-tunnel`,
and for ECS Docker Exec: `ecs-session`

## Scripts included

* **ec2-session** (formerly _ssm-session_)

  Like `aws ssm start-session`, but opens an SSM Session to an
  instance specified by *Name* or *IP Address*.

  It doesn't need user credentials or even `sshd` running on the instance.

  Check out *[SSM Sessions the easy
  way](https://aws.nz/projects/ssm-session/)* for an example use.

  Works with any Linux or Windows EC2 instance registered in SSM.

* **ecs-session**

  Like `aws ecs execute-command`, but can run a command
  or open an interactive session to an Exec-enabled ECS container
  specified by the service, name, IP address, etc.

  It doesn't need user credentials or `sshd` running on the container,
  however the containers must be configured to allow this access.

  Each keyword matches a service (with or without `service:`), task ID,
  container name or IP address, in any case. `-c 'COMMAND'` runs it with
  `/bin/sh -c`, so pipes and `$VARS` work. `ecs-session CONTAINER -- COMMAND
  ARG ...` runs it without a shell, for example in a distroless image.

  ECS Exec always runs as root. `-u USER`, `-l USER`, `--user USER` or
  `USER@CONTAINER` run the shell or `-c` command as USER instead, through
  `runuser`, `su` or `chroot --userspec`, whichever the container has.
  Distroless images have none of them.

  Check out *[Interactive shell in ECS Containers](https://aws.nz/projects/ecs-session/)*
  for an example use.

* **ec2-ssh** (formerly _ssm-ssh_)

  Open an SSH connection to the remote server through *Systems Manager*
  without the need for open firewall or direct internet access. SSH can
  then be used to forward ports, copy files, etc.

  Unlike `ssm-tunnel` it doesn't create a full VPN link, however it's in
  some aspects more versatile as it can be used with `rsync`, `scp`,
  `sftp`, etc.

  It works with any client that can run SSH (including Mac OS-X) and
  doesn't require a special agent on the instance, other than the standard
  AWS SSM agent.

  It pushes your SSH key to the instance first (aka *EC2 Instance Connect*,
  although that's an odd name for this function), use `--no-send-key` to
  skip that. The key is the first RSA or Ed25519 key that `ssh` will offer,
  from your agent or from `-i` and `IdentityFile`, as `ssh` orders them.
  `--gen-key` sends a new key made for this connection instead, and deletes
  it when `ssh` exits. The login user is `-u USER`, `--user USER`, `-l USER` or
  `USER@INSTANCE`, or else the user `ssh` itself would use, e.g. from a
  `Host` stanza in `~/.ssh/config` matching the name you typed.

  With `--use-endpoint` it connects through an *EC2 Instance Connect
  Endpoint* instead (needs AWS CLI 2.12.0 or newer). Names are looked up in
  SSM, so give instances without the SSM agent by their instance ID.

  *Hybrid managed instances* (`mi-...`) work too, but EC2 Instance Connect
  can't push a key to them, so your key must already be on the instance.
  `--use-endpoint` doesn't work with them. All the tools need the
  *advanced-instances tier* to open sessions to hybrid instances.

* **ssm-port-forward**

  Forward a local port to a remote port on an EC2 instance (or any host
  reachable from it) through Systems Manager, without needing SSH or an
  open firewall port.

  Works on Linux, Mac and Windows. Uses the native AWS SSM port-forwarding
  documents (`AWS-StartPortForwardingSession` and
  `AWS-StartPortForwardingSessionToRemoteHost`).

* **ssm-tunnel**

  Open *IP tunnel* to the SSM instance and to enable *network access*
  to the instance VPC. This requires [ssm-tunnel-agent](#standalone-ssm-tunnel-agent-installation)
  installed on the instance.

  Works with *Amazon Linux 2* instances and probably other recent Linux
  EC2 instances. Requires *Linux* on the client side - if you are on Mac
  or Windows you can install a Linux VM in a [VirtualBox](https://virtualbox.org).

  Requires `ssm-tunnel-agent` installed on the instance - see below for
  instructions.

## Usage

1. **List instances** available for connection

    ```
    ~ $ ec2-session --list
    InstanceId           InstanceName      HostName                        Addresses
    -------------------  ----------------  ------------------------------  --------------
    i-07c189021bc56e042  nginx-web-server  ip-10-251-128-70.ec2.internal   10.251.128.70
    i-094df06d3633f3267  bastion-host      ip-10-251-128-73.ec2.internal   10.251.128.73
    i-02689d593e17f2b75  jenkins-server    ip-10-251-129-78.ec2.internal   10.251.129.78
    ```

    If you're like me and have access to many different AWS accounts you
    can select the right one with `--profile` and / or change the `--region`:

    ```
    ~ $ ec2-session --profile aws-sandpit --region us-west-2 --list
    InstanceId           InstanceName      HostName                       Addresses
    -------------------  ----------------  -----------------------------  --------------
    i-07c189021bc56e042  nginx-web-server  ip-10-251-128-70.ec2.internal  10.251.128.70
    ```

    Alternatively use the standard AWS *environment variables*:

    ```
    ~ $ export AWS_DEFAULT_PROFILE=aws-sandpit
    ~ $ export AWS_DEFAULT_REGION=us-west-2
    ~ $ ec2-session --list
    InstanceId           InstanceName      HostName                       Addresses
    -------------------  ----------------  -----------------------------  -------------
    i-07c189021bc56e042  nginx-web-server  ip-10-251-128-70.ec2.internal  10.251.128.70
    ```

2. **Open SSM session** to an instance:

    This opens an interactive shell session over SSM without the need for
    a password or SSH key. Note that by default the login user is `ssm-user`.
    You can specify most a different user with e.g. `--user ec2-user` or
    even `--user root`. `-u USER`, `-l USER`, `--sudo USER` and
    `USER@INSTANCE` do the same. This runs `sudo -i -u USER` on Linux
    instances and is refused for Windows instances.


    Running `ec2-session` without specifying an IP or hostname to connect to will show a simple terminal menu.
    You can see all the servers managed by SSM here and pressing enter will start a connection to the highted server.
    note that you will still need to pass in `--user` if you are not using the default values.

    You can skip the interactive menu by specifying the server directly into the command.
    ```
    ~ $ ec2-session -v nginx-web-server --user ec2-user --reason "optional - The reason why you are connecting to the instance"
    InstanceId           InstanceName       HostName                      Addresses
    -------------------  ----------------  -----------------------------  -------------
    i-07c189021bc56e042  nginx-web-server  ip-10-251-128-70.ec2.internal  10.251.128.70

    Starting session with SessionId: botocore-session-0d381a3ef740153ac
    [ec2-user@ip-10-251-128-70] ~ $ hostname
    ip-10-251-128-70.ec2.internal
    ```

    Run a single command with `-c 'COMMAND'`. It reaches the instance's
    shell exactly as typed, commas, quotes and brackets included. On Windows
    instances the SSM agent strips double quotes, so use single quotes there.
    A command that reads input before printing anything hangs, because
    `session-manager-plugin` only starts sending your keystrokes after the
    first output. Print something first, e.g. `-c "printf '\r'; read x; ..."`.

    `ec2-session` and `ecs-session` don't return the remote command's exit
    code: `session-manager-plugin` exits with 0 either way
    ([aws/session-manager-plugin#59](https://github.com/aws/session-manager-plugin/issues/59)).
    In scripts use `ec2-ssh INSTANCE COMMAND`, which does.

    You can specify other SSM documents to run with `--document-name AWS-...`
    and their parameters as JSON, e.g. `--parameters '{"command": ["top"]}'`,
    to customise your session. Refer to AWS docs for details.

    To end a session that stopped responding press `Enter` then `~.`
    (tilde, dot), as in SSH. That needs `session-manager-plugin` 1.2.835.0
    or newer; `ec2-session` and `ecs-session` warn when yours is older.
    Otherwise kill `ec2-session` from another terminal: it ends the session
    and restores your terminal. `ec2-ssh` uses SSH's own `~.`, which works
    even when the network is gone.

3. **Open SSH session** over SSM with *port forwarding*.

    The `ec2-ssh` tool provides a connection and authentication mechanism
    for running SSH over Systems Manager.

    The target instance *does not need* a public IP address, it also does
    *not* need an open SSH port in the Security Group. All it needs is to be
    registered in the Systems Manager.

    All `ssh` options are supported, go wild. In this example we will port forward
    our local 3306 port to our MySQL RDS database which is running on the same standard port
    `-L 3306:mysql-rds.aws.nz:3306` SSH port forwarding method.

    ```
    ~ $ ec2-ssh ec2-user@test1 -L 3306:mysql-rds.aws.nz:3306 -i ~/.ssh/aws-nz.pem
    InstanceId           InstanceName                 HostName                        Addresses
    -------------------  ---------------------------  ------------------------------  --------------
    i-07c189021bc56e042   nginx-web-server            ip-10-251-128-70.ec2.internal   10.251.128.70
    [ec2-ssh] INFO: Resolved instance name 'test1' to 'i-07c189021bc56e042'
    [ec2-ssh] INFO: Running: ssh -o ProxyCommand='aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters portNumber=%p' -L 3306:mysql-rds.aws.nz:3306 -i ~/.ssh/aws-nz.pem -- ec2-user@i-07c189021bc56e042
    OpenSSH_7.6p1 Ubuntu-4ubuntu0.3, OpenSSL 1.0.2n  7 Dec 2017
    ...
    Last login: Sun Apr 12 20:05:09 2020 from localhost

       __|  __|_  )
       _|  (     /   Amazon Linux 2 AMI
      ___|\___|___|

    [ec2-user@ip-192-168-45-158] ~ $
    ```

    From another terminal we can now connect to the MySQL RDS. Since the
    port 3306 is forwarded from *localhost* through the tunnel we will
    instruct `mysql` client to connect to `127.0.0.1` (localhost).

    ```
    ~ $ mysql -h 127.0.0.1 -u {RdsMasterUser} -p
    Enter password: {RdsMasterPassword}
    Welcome to the MariaDB monitor.  Commands end with ; or \g.
    Server version: 5.6.10 MySQL Community Server (GPL)

    MySQL [(none)]> show processlist;
    +-----+------------+-----------------------+
    | Id  | User       | Host                  |
    +-----+------------+-----------------------+
    |  52 | rdsadmin   | localhost             |
    | 289 | masteruser | 192.168.45.158:52182  | <<< Connection from test1 IP
    +-----+------------+-----------------------+
    2 rows in set (0.04 sec)
    ```

4. **Use `rsync` with `ec2-ssh`** to copy files to/from EC2 instance.

    Since in the end we run a standard `ssh` client we can use it with
    [rsync](https://en.wikipedia.org/wiki/Rsync) to copy files to/from the
    EC2 instance.

    ```
    ~ $ rsync -e ec2-ssh -Prv ec2-user@test1:some-file.tar.gz .
    some-file.tar.gz
         31,337,841 100%  889.58kB/s    0:00:34 (xfr#1, to-chk=0/1)
    sent 43 bytes  received 31,345,607 bytes  814,172.73 bytes/sec
    total size is 31,337,841  speedup is 1.00
    ```

    We can also select a different AWS profile and/or region:

    ```
    ~ $ rsync -e "ec2-ssh --profile aws-sandpit --region us-west-2" -Prv ...
    ```

    Alternatively set the profile and region through standard AWS
    *environment variables* `AWS_DEFAULT_PROFILE` and
    `AWS_DEFAULT_REGION`.`

5. **Forward a local port** to an EC2 instance or a host in its VPC.

    No SSH, no open firewall ports - just SSM. Works on Linux, Mac and Windows.

    Forward RDP (port 3389) from `localhost:13389` to a Windows instance:

    ```
    ~ $ ssm-port-forward my-windows-host --remote-port 3389 --local-port 13389
    ```

    Then connect your RDP client to `localhost:13389`.

    Forward a local port to an RDS database *through* a bastion instance
    (the RDS endpoint never needs to be publicly accessible):

    ```
    ~ $ ssm-port-forward my-bastion --remote-port 5432 --remote-host my-db.cluster.rds.amazonaws.com
    ```

    If `--local-port` is omitted it defaults to the same value as `--remote-port`.
    With `--local-port 0` the plugin picks a free port and prints it.

    The SSM agent on the instance makes the connection to the remote host,
    out of `ssm-port-forward`'s sight. If the host refuses it or its name
    doesn't resolve, the session ends with exit code 0. If a security group
    drops it, your connection just hangs. Check the host and port from the
    instance itself, e.g. in `ec2-session`.

    Like `ec2-session`, running without an instance name shows an interactive
    selection menu.

6. **Create IP tunnel** and SSH to another instance in the VPC through it.

    We will use `--route 192.168.44.0/23` that gives us access to the VPC CIDR.

    ```
    ~ $ ssm-tunnel -v tunnel-test --route 192.168.44.0/23
    [ssm-tunnel] INFO: Local IP: 100.64.160.100 / Remote IP: 100.64.160.101
    00:00:15 | In:  156.0 B @    5.2 B/s | Out:  509.0 B @   40.4 B/s
    ```

    Leave it running and from another shell `ssh` to one of the instances listed
    with `--list` above. For example to `test1` that's got VPC IP `192.168.45.158`:

    ```
    ~ $ ssh ec2-user@192.168.45.158
    Last login: Tue Jun 18 20:50:59 2019 from 100.64.142.232
    ...
    [ec2-user@test1 ~]$ w -i
     21:20:43 up  1:43,  1 user,  load average: 0.00, 0.00, 0.00
    USER     TTY      FROM             LOGIN@   IDLE   JCPU   PCPU WHAT
    ec2-user pts/0    192.168.44.95    21:20    3.00s  0.02s  0.00s w -i
                      ^^^^^^^^^^^^^
    [ec2-user@test1 ~]$ exit
    Connection to 192.168.45.158 closed.
    ~ $
    ```

    Note the source IP `192.168.44.95` that belongs to the `tunnel-test`
    instance - our connections will *appear* as if they come from this instance.
    Obviously the **Security Groups** of your other instances must allow SSH
    access from the IP or SG of your tunnelling instance.

All these tools support `--help` and a set of common parameters:

    --profile PROFILE, -p PROFILE
                        Configuration profile from ~/.aws/{credentials,config}
    --region REGION, -g REGION
                        Set / override AWS region.
    --verbose, -v       Increase log level.
    --debug, -d         Increase log level even more.

`ec2-ssh` only supports the long options to prevent conflict with `ssh`'s
own short options that are being passed through. It reads the command line
like `ssh` does: options, then `[user@]instance`, then the remote command.
The remote command is passed through untouched, even if it contains options
like `--profile`. A name that isn't an SSM instance, e.g. a `Host` alias from
`~/.ssh/config`, is passed to `ssh` as it is.

`ssh` connects to the instance ID, so `Host i-*` stanzas in `~/.ssh/config`
apply. From a `Host name` stanza matching the name you typed, only `User` is
used.

Instances can be given by instance ID, *Name* tag, host name or IP. A *Name*
match wins over host names and IPs, because instances report those
themselves, and the tools say so when they match only a host name or IP.
Hybrid instances get their *Name* from their activation, so anyone with the
activation code can register more instances with the same *Name*.
A name that matches several instances opens the instance menu with just
those, or, without a terminal, lists them and exits.

Standard AWS environment variables like `AWS_DEFAULT_PROFILE`,
`AWS_DEFAULT_REGION`, etc, are also supported.

## Installation

The tools open an **SSM Session** and then use that session to run commands
on the target instance. `ec2-ssh` opens it through the
**AWS CLI**; the others open it themselves and need only `session-manager-plugin`. The target instances **must be
registered in SSM**, which means they need:

- **connectivity to SSM endpoint**, e.g. through public IP, NAT Gateway, or
  SSM VPC endpoint.
- **EC2 instance IAM Role** with permissions to connect to Systems Manager.

Follow the detailed instructions at [**Using SSM Session Manager for
interactive instance access**](https://aws.nz/best-practice/ssm-session-manager/)
for more informations.

### Install *AWS CLI* and `session-manager-plugin`

Make sure you've got `session-manager-plugin` installed locally on your
laptop, and `aws` too if you use `ec2-ssh`.

```
~ $ aws --version
aws-cli/1.18.31 Python/3.6.9 Linux/5.3.0-42-generic botocore/1.15.31

~ $ session-manager-plugin --version
1.2.835.0
```

Follow [AWS CLI installation
guide](https://docs.aws.amazon.com/cli/latest/userguide/cli-chap-install.html)
and [session-manager-plugin
installation guide](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) to install them if needed.

Note that `ec2-session`, `ecs-session`, `ssm-port-forward` and `ssm-tunnel` need
`session-manager-plugin` version *1.2.497.0* or newer, `ec2-ssh` needs
*1.1.23* or newer, and `Enter ~.` to end a stuck session needs *1.2.835.0*
or newer.
Upgrade if your version is older.

### Register your instances with Systems Manager

*Amazon Linux 2* instances already have the `amazon-ssm-agent` installed and
running. All they need to register with *Systems Manager* is
**AmazonEC2RoleforSSM** managed role in their *IAM Instance Role* and network
access to `ssm.{region}.amazonaws.com` either directly or through a *https proxy*.

Check out the [detailed instructions](https://aws.nz/best-practice/ssm-session-manager/) for more info.

### Install SSM-Tools *(finally! :)*

The easiest way is to install the ssm-tools from *[PyPI](https://pypi.org/)* repository:

**Linux / Mac:**
```
sudo pip3 install aws-ssm-tools
```

**Windows** (run in *Command Prompt* or *PowerShell*):
```
pip install aws-ssm-tools
```

**From source** (Linux, Mac or Windows):
```
pip install .
```

**NOTE:** SSM Tools require **Python 3.11 or newer**. The `python3` that
comes with macOS is older, so install a newer one first, e.g. with
`brew install python`. `ssm-tunnel-agent` on the instance only needs
Python 3.8, see below.

#### Windows compatibility

| Tool | Works on Windows? |
|---|---|
| `ssm-port-forward` | Yes |
| `ec2-session` | Yes (requires `session-manager-plugin`) |
| `ec2-ssh` | Yes (requires OpenSSH and `session-manager-plugin`) |
| `ecs-session` | Yes |
| `ssm-tunnel` | **No** — requires Linux TUN device and iptables |

When no instance name is given, Windows shows a numbered list of instances instead of the interactive menu.

#### Pre-built Windows binaries (no Python required)

If you don't have Python installed, download the pre-built zip bundle from the
[**GitHub Releases**](https://github.com/mludvig/aws-ssm-tools/releases/latest) page.
Extract it — you'll get a single `aws-ssm-tools\` folder containing all four `.exe` files and a shared `_internal\` directory with the Python runtime and libraries.
Add the `aws-ssm-tools\` folder to your `PATH` to use the tools from anywhere.

You still need **session-manager-plugin** installed, and **AWS CLI** for
`ec2-ssh` — the bundles only contain the Python code, not the AWS CLI itself.

### Standalone *ssm-tunnel-agent* installation

`ssm-tunnel-agent` is bundled with this package as `ssm_tools/ssm_tunnel_agent.py`.
Copy it to `/usr/local/bin/ssm-tunnel-agent` on the instance and make it
executable. If it's missing, `ssm-tunnel` tells you where the file is on your
computer. The instance needs Python 3.8 or newer, `ip`, `iptables`, and `sudo`
for the session user. `ssm-tunnel` also warns when the instance's agent is older
than the bundled one. Copy the new file over the old one to update it.

`--up-down SCRIPT` is called as `SCRIPT up|down DEVICE LOCAL_IP REMOTE_IP [ROUTE ...]`.
It must be a path to an executable, with no arguments of its own.

## Other AWS Utilities

Check out **[AWS Utils](https://github.com/mludvig/aws-utils)**
repository for more useful AWS tools.

## Author and License

All these scripts were written by [Michael Ludvig](https://aws.nz/)
and are released under [Apache License 2.0](http://www.apache.org/licenses/LICENSE-2.0).
