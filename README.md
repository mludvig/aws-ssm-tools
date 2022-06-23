# aws-ssm-tools - AWS System Manager Tools

[![CircleCI](https://circleci.com/gh/mludvig/aws-ssm-tools.svg?style=shield)](https://circleci.com/gh/mludvig/aws-ssm-tools)
[![PyPI](https://img.shields.io/pypi/v/aws-ssm-tools.svg)](https://pypi.org/project/aws-ssm-tools/)
[![Python Versions](https://img.shields.io/pypi/pyversions/aws-ssm-tools.svg)](https://pypi.org/project/aws-ssm-tools/)

Helper tools for AWS Systems Manager: `ssm-session`, `ssm-ssh` and `ssm-tunnel`,
and for ECS Docker Exec: `ecs-session`

## Scripts included

* **ssm-session**

  Wrapper around `aws ssm start-session` that can open
  SSM Session to an instance specified by *Name* or *IP Address*.

  It doesn't need user credentials or even `sshd` running on the instance.

  Check out *[SSM Sessions the easy
  way](https://aws.nz/projects/ssm-session/)* for an example use.

  Works with any Linux or Windows EC2 instance registered in SSM.

* **ecs-session**

  Wrapper around `aws ecs execute-command` that can run a command
  or open an interactive session to an Exec-enabled ECS container
  specified by the service, name, IP address, etc.

  It doesn't need user credentials or `sshd` running on the container,
  however the containers must be configured to allow this access.

  Check out *[Interactive shell in ECS Containers](https://aws.nz/projects/ecs-session/)*
  for an example use.

* **ssm-tunnel**

  Open *IP tunnel* to the SSM instance and to enable *network access*
  to the instance VPC. This requires [ssm-tunnel-agent](README-agent.md)
  installed on the instance.

  Works with *Amazon Linux 2* instances and probably other recent Linux
  EC2 instances. Requires *Linux* on the client side - if you are on Mac
  or Windows you can install a Linux VM in a [VirtualBox](https://virtualbox.org).

  Requires `ssm-tunnel-agent` installed on the instance - see below for
  instructions.

* **ssm-ssh**

  Open an SSH connection to the remote server through *Systems Manager*
  without the need for open firewall or direct internet access. SSH can
  then be used to forward ports, copy files, etc.

  Unlike `ssm-tunnel` it doesn't create a full VPN link, however it's in
  some aspects more versatile as it can be used with `rsync`, `scp`,
  `sftp`, etc.

  It works with any client that can run SSH (including Mac OS-X) and
  doesn't require a special agent on the instance, other than the standard
  AWS SSM agent.

* **ssm-copy**

  **DEPRECATED and REMOVED** - use `rsync` with `ssm-ssh` instead.

## Usage

1. **List instances** available for connection

    ```
    ~ $ ssm-session --list
    i-07c189021bc56e042   test1.aws.nz       test1        192.168.45.158
    i-094df06d3633f3267   tunnel-test.aws.nz tunnel-test  192.168.44.95
    i-02689d593e17f2b75   winbox.aws.nz      winbox       192.168.45.5    13.11.22.33
    ```

    If you're like me and have access to many different AWS accounts you
    can select the right one with `--profile` and / or change the `--region`:

    ```
    ~ $ ssm-session --profile aws-sandpit --region us-west-2 --list
    i-0beb42b1e6b60ac10   uswest2.aws.nz     uswest2      172.31.0.92
    ```

    Alternatively use the standard AWS *environment variables*:

    ```
    ~ $ export AWS_DEFAULT_PROFILE=aws-sandpit
    ~ $ export AWS_DEFAULT_REGION=us-west-2
    ~ $ ssm-session --list
    i-0beb42b1e6b60ac10   uswest2.aws.nz     uswest2      172.31.0.92
    ```

2. **Open SSM session** to an instance:

    This opens an interactive shell session over SSM without the need for
    a password or SSH key. Note that by default the login user is `ssm-user`.
    You can specify most a different user with e.g. `--user ec2-user` or
    even `--user root`.

    ```
    ~ $ ssm-session -v test1 --user ec2-user
    Starting session with SessionId: botocore-session-0d381a3ef740153ac
    [ec2-user@ip-192-168-45-158] ~ $ hostname
    test1.aws.nz

    [ec2-user@ip-192-168-45-158] ~ $ id
    uid=1000(ec2-user) gid=1000(ec2-user) groups=1000(ec2-user),...

    [ec2-user@ip-192-168-45-158] ~ $ ^D
    Exiting session with sessionId: botocore-session-0d381a3ef740153ac.
    ~ $
    ```

    You can specify other SSM documents to run with `--document-name AWS-...`
    to customise your session. Refer to AWS docs for details.

3. **Open SSH session** over SSM with *port forwarding*.

    The `ssm-ssh` tool provides a connection and authentication mechanism
    for running SSH over Systems Manager.

    The target instance *does not need* a public IP address, it also does
    *not* need an open SSH port in the Security Group. All it needs is to be
    registered in the Systems Manager.

    All `ssh` options are supported, go wild. In this example we will
    forward port 3306 to our MySQL RDS database using the standard
    `-L 3306:mysql-rds.aws.nz:3306` SSH port forwarding method.

    ```
    ~ $ ssm-ssh ec2-user@test1 -L 3306:mysql-rds.aws.nz:3306 -i ~/.ssh/aws-nz.pem
    [ssm-ssh] INFO: Resolved instance name 'test1' to 'i-07c189021bc56e042'
    [ssm-ssh] INFO: Running: ssh -o ProxyCommand='aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters portNumber=%p' i-07c189021bc56e042 -l ec2-user -L 3306:mysql-rds.aws.nz:3306 -i ~/.ssh/aws-nz.pem
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

4. **Use `rsync` with `ssm-ssh`** to copy files to/from EC2 instance.

    Since in the end we run a standard `ssh` client we can use it with
    [rsync](https://en.wikipedia.org/wiki/Rsync) to copy files to/from the
    EC2 instance.

    ```
    ~ $ rsync -e ssm-ssh -Prv ec2-user@test1:some-file.tar.gz .
    some-file.tar.gz
         31,337,841 100%  889.58kB/s    0:00:34 (xfr#1, to-chk=0/1)
    sent 43 bytes  received 31,345,607 bytes  814,172.73 bytes/sec
    total size is 31,337,841  speedup is 1.00
    ```

    We can also select a different AWS profile and/or region:

    ```
    ~ $ rsync -e "ssm-ssh --profile aws-sandpit --region us-west-2" -Prv ...
    ```

    Alternatively set the profile and region through standard AWS
    *environment variables* `AWS_DEFAULT_PROFILE` and
    `AWS_DEFAULT_REGION`.`

5. **Create IP tunnel** and SSH to another instance in the VPC through it.

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

`ssm-ssh` only supports the long options to prevent conflict with `ssh`'s
own short options that are being passed through.

Standard AWS environment variables like `AWS_DEFAULT_PROFILE`,
`AWS_DEFAULT_REGION`, etc, are also supported.

## Installation

All the tools use **AWS CLI** to open **SSM Session** and then use that
session to run commands on the target instance. The target instances **must be
registered in SSM**, which means they need:

- **connectivity to SSM endpoint**, e.g. through public IP, NAT Gateway, or
  SSM VPC endpoint.
- **EC2 instance IAM Role** with permissions to connect to Systems Manager.

Follow the detailed instructions at [**Using SSM Session Manager for
interactive instance access**](https://aws.nz/best-practice/ssm-session-manager/)
for more informations.

### Install *AWS CLI* and `session-manager-plugin`

Make sure you've got `aws` and `session-manager-plugin` installed locally
on your laptop.

```
~ $ aws --version
aws-cli/1.18.31 Python/3.6.9 Linux/5.3.0-42-generic botocore/1.15.31

~ $ session-manager-plugin --version
1.1.56.0
```

Follow [AWS CLI installation
guide](https://docs.aws.amazon.com/cli/latest/userguide/cli-chap-install.html)
and [session-manager-plugin
installation guide](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) to install them if needed.

Note that `ssm-ssh` needs `session-manager-plugin` version *1.1.23* or
newer. Upgrade if your version is older.

### Register your instances with Systems Manager

*Amazon Linux 2* instances already have the `amazon-ssm-agent` installed and
running. All they need to register with *Systems Manager* is
**AmazonEC2RoleforSSM** managed role in their *IAM Instance Role* and network
access to `ssm.{region}.amazonaws.com` either directly or through a *https proxy*.

Check out the [detailed instructions](https://aws.nz/best-practice/ssm-session-manager/) for more info.

### Install SSM-Tools *(finally! :)*

The easiest way is to install the ssm-tools from *[PyPI](https://pypi.org/)* repository:

```
sudo pip3 install aws-ssm-tools
```

**NOTE:** SSM Tools require **Python 3.6 or newer**. Only the `ssm-tunnel-agent`
requires **Python 2.7 or newer** as that's what's available by default
on *Amazon Linux 2* instances.

### Standalone *ssm-tunnel-agent* installation

Refer to *[README-agent.md](README-agent.md)* for `ssm-tunnel-agent`
installation details.

Alternatively it's also bundled with this package, you can take it from here and
copy to `/usr/local/bin/ssm-tunnel-agent` on the instance. Make it executable
and it should just work.

## Other AWS Utilities

Check out **[AWS Utils](https://github.com/mludvig/aws-utils)**
repository for more useful AWS tools.

## Author and License

All these scripts were written by [Michael Ludvig](https://aws.nz/)
and are released under [Apache License 2.0](http://www.apache.org/licenses/LICENSE-2.0).
