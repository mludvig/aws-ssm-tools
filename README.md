# aws-ssm-tools - AWS System Manager Tools

Convenient tools for AWS Systems Manager.

## Scripts included

* **[ssm-session](ssm-session)**

  Wrapper around `aws ssm start-session` that can open
  SSM Session to an instance specified by *Name* or *IP Address*.

  Check out *[SSM Sessions the easy
  way](https://aws.nz/projects/ssm-session/)* for an example use.

* **[ssm-copy](ssm-copy)**

  Copy files to/from EC2 instances to which we don't have access
  over SSH. The file transfer uses the *SSM Session* channel and
  transfers files *base64*-encoded.

* **[ssm-tunnel](ssm-tunnel)**

  Open *IP tunnel* to the SSM instance and to enable *network access*
  to the instance VPC. This requires [ssm-tunnel-agent](ssm-tunnel-agent)
  installed on the instance.

## Installation

The easiest way is to install the tools from *[PyPI](https://pypi.org/)* repository:

```
pip install aws-ssm-tools
```

**Note: SSM Tools require Python 3.6+** except for the `ssm-tunnel-agent` that only requires **Python 2.7+**

### Standalone tunnel agent installation

Refer to *[README-agent.md](README-agent.md)* for `ssm-tunnel-agent` installation details.

Alternatively it's also bundled with this package, you can take it from here.

## Other AWS Utilities

Check out **[AWS Utils](https://github.com/mludvig/aws-utils)**
repository for more useful AWS tools.

## Author and License

All these scripts were written by [Michael Ludvig](https://aws.nz/)
and are released under [Apache License 2.0](http://www.apache.org/licenses/LICENSE-2.0).
