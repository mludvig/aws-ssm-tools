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

## Installation

At the moment simply clone this repository and run the scripts
with `--help`.

*[Pipy](https://pypi.org/)* distribution is coming soon.

## Other AWS Utilities

Check out **[AWS Utils](https://github.com/mludvig/aws-utils)**
repository for more useful AWS tools.

## Author and License

All these scripts were written by [Michael Ludvig](https://aws.nz/)
and are released under [Apache License 2.0](http://www.apache.org/licenses/LICENSE-2.0).
