# ssm-tunnel-agent

This file / package should be installed on the EC2 instance
through which you want to tunnel traffic to its VPC.

It is useless on its own, it should be used with **[ssm-tunnel]** from
**[aws-ssm-tools](https://github.com/mludvig/aws-ssm-tools)**.

## Installation

The agent requires Python 2.7 or newer and has no external dependencies. It should
work out of the box on any recently installed *Amazon Linux 2* instance.

The easiest way to install the agent is from *[PyPI](https://pypi.org/)* repository:

```
sudo pip install aws-ssm-tunnel-agent
```

If `pip` command is not available you can download it straight from GitHub:

```
sudo curl -o /usr/local/bin/ssm-tunnel-agent https://raw.githubusercontent.com/mludvig/aws-ssm-tools/master/ssm-tunnel-agent
sudo chmod +x /usr/local/bin/ssm-tunnel-agent
```

Hint: these commands can be *copy & pasted* to an **[ec2-session](https://raw.githubusercontent.com/mludvig/aws-ssm-tools)** command prompt :)

## Author and License

This script was written by [Michael Ludvig](https://aws.nz/)
and is released under [Apache License 2.0](http://www.apache.org/licenses/LICENSE-2.0).
