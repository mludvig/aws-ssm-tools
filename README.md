# aws-ssm-tools - AWS System Manager Tools

[![CircleCI](https://circleci.com/gh/mludvig/aws-ssm-tools.svg?style=shield)](https://circleci.com/gh/mludvig/aws-ssm-tools)
[![PyPI](https://img.shields.io/pypi/v/aws-ssm-tools.svg)](https://pypi.org/project/aws-ssm-tools/)
[![Python Versions](https://img.shields.io/pypi/pyversions/aws-ssm-tools.svg)](https://pypi.org/project/aws-ssm-tools/)

Helper tools for AWS Systems Manager: `ssm-session`, `ssm-copy` and
`ssm-tunnel`.

## Scripts included

* **ssm-session**

  Wrapper around `aws ssm start-session` that can open
  SSM Session to an instance specified by *Name* or *IP Address*.

  Check out *[SSM Sessions the easy
  way](https://aws.nz/projects/ssm-session/)* for an example use.

  Works with any Linux or Windows EC2 instance registered in SSM.

* **ssm-copy**

  Copy files to/from EC2 instances over *SSM Session* without the need to have a
  direct SSH access.

  Works with *Linux* instances only, however *no* remote agent is required. All
  that is needed is a shell and standard linux tools like `base64` (yes, we are
  transferring the files base64-encoded as *SSM Sessions* won't pass through
  binary data).

  Only *copy to instance* is implemented at the moment. *Copy from* is on my todo
  list :)

* **ssm-tunnel**

  Open *IP tunnel* to the SSM instance and to enable *network access*
  to the instance VPC. This requires [ssm-tunnel-agent](README-agent.md)
  installed on the instance.

  Works with *Amazon Linux 2* instances and probably other recent Linux systems.

  Requires `ssm-tunnel-agent` installed on the instance - see below for
  instructions.

## Usage

1. **List instances** available for connection

    ```
    ~ $ ssm-session --list
    i-07c189021bc56e042   test1.aws.nz       test1        192.168.45.158
    i-094df06d3633f3267   tunnel-test.aws.nz tunnel-test  192.168.44.95
    i-02689d593e17f2b75   winbox.aws.nz      winbox       192.168.45.5    13.11.22.33
    ```

2. **Copy a file** to an instance:

    ```
    ~ $ ssm-copy large-file test1:
    large-file - 1087kB, 27.6s, 39.4kB/s, [SHA1 OK]
    ```

3. **Open SSM session** to an instance:

    ```
    ~ $ ssm-session -v test1
    Starting session with SessionId: botocore-session-0d381a3ef740153ac
    sh-4.2$ hostname
    test1.aws.nz

    sh-4.2$ cd
    sh-4.2$ ls -l
    total 1088
    -rw-r--r-- 1 ssm-user ssm-user 1113504 Jun 20 02:07 large-file

    sh-4.2$ exit
    Exiting session with sessionId: botocore-session-0d381a3ef740153ac.
    ~ $
    ```

4. **Create IP tunnel** and SSH to another instance in the VPC through it.

    We'll use `--route 192.168.44.0/23` that gives us access to the VPC CIDR.

    ```
    $ ssm-tunnel -v tunnel-test --route 192.168.44.0/23
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

All the tools support `--help` and a set of common parameters:

    --profile PROFILE, -p PROFILE
                        Configuration profile from ~/.aws/{credentials,config}
    --region REGION, -g REGION
                        Set / override AWS region.
    --verbose, -v       Increase log level
    --debug, -d         Increase log level

They also support the standard AWS environment variables like `AWS_DEFAULT_PROFILE`,
`AWS_DEFAULT_REGION`, etc.

## Installation

All the tools use **AWS CLI** to open **SSM Session** and then use that
session to run commands on the target instance. The target instances must be
registered in SSM.

### Install *AWS CLI* and `session-manager-plugin`

Make sure you've got `aws` and `session-manager-plugin` installed locally
on your laptop.

```
~ $ aws --version
aws-cli/1.16.175 Python/3.6.8 Linux/4.15.0-51-generic botocore/1.12.165

~ $ session-manager-plugin --version
1.1.17.0
```

Follow [AWS CLI installation
guide](https://docs.aws.amazon.com/cli/latest/userguide/cli-chap-install.html)
and [session-manager-plugin
installation guide](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) to install them if needed.

### Register your instances with Systems Manager

*Amazon Linux 2* instances already have the `amazon-ssm-agent` installed and
running. All they need to register with *Systems Manager* is
**AmazonEC2RoleforSSM** managed role in their *IAM Instance Role* and network
access to `ssm.{region}.amazonaws.com` either directly or through a *https proxy*.

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
