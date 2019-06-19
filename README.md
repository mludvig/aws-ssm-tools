# aws-ssm-tools - AWS System Manager Tools

Helper tools for AWS Systems Manager.

## Scripts included

* **[ssm-session](ssm-session)**

  Wrapper around `aws ssm start-session` that can open
  SSM Session to an instance specified by *Name* or *IP Address*.

  Check out *[SSM Sessions the easy
  way](https://aws.nz/projects/ssm-session/)* for an example use.

  Works with any Linux or Windows EC2 instance registered in SSM.

* **[ssm-copy](ssm-copy)**

  Copy files to/from EC2 instances over *SSM Session* without the need to have a
  direct SSH access.

  Works with *Linux* instances only, however *no* remote agent is required. All
  that is needed is a shell and standard linux tools like `base64` (yes, we are
  transferring the files base64-encoded as *SSM Sessions* won't pass through
  binary data).

  Only *copy to instance* is implemented at the moment. *Copy from* is on my todo
  list :)

* **[ssm-tunnel](ssm-tunnel)**

  Open *IP tunnel* to the SSM instance and to enable *network access*
  to the instance VPC. This requires [ssm-tunnel-agent](ssm-tunnel-agent)
  installed on the instance.

  Works with *Amazon Linux 2* instances and probably other recent systems.

  Requires `ssm-tunnel-agent` installed on the instance - see below for
  instructions.

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
