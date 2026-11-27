#!/usr/bin/env python3

# Open SSH connections through AWS Session Manager
#
# See https://aws.nz/aws-utils/ec2-ssh for more info.
#
# Author: Michael Ludvig (https://aws.nz)

# The script can list available instances, resolve instance names,
# and host names, etc. In the end it executes 'ssh' with the correct
# parameters to actually start the SSH session.

import argparse
import contextlib
import dataclasses
import io
import logging
import os
import re
import shlex
import subprocess
import sys
import tempfile

import botocore.exceptions

from .common import (
    add_general_parameters,
    configure_logging,
    handle_boto_error,
    port_number,
    session_reason,
    show_version,
    unique_login,
    verify_awscli_version,
    verify_plugin_version,
)
from .ec2_instance_connect import EC2InstanceConnectHelper, generate_key, select_key
from .picker import target_selector
from .plugin import PLUGIN_ENV_VERSION, run_plugin
from .resolver import InstanceResolver, instance_not_found
from .terminal import run_session

logger = logging.getLogger("ssm-tools.ec2-ssh")

MIN_PLUGIN_VERSION = "1.1.23"  # AWS-StartSSHSession support
MIN_AWSCLI_VERSION_ENDPOINT = "2.12.0"  # aws ec2-instance-connect open-tunnel
# ssh exits about 60 s (4 intervals) after the tunnel dies, see docs/adr/0023-ec2-ssh-server-alive-interval.md
SERVER_ALIVE_INTERVAL = 15
# The REGION in NAME.REGION.ssm, e.g. ap-southeast-6, us-gov-west-1 or eusc-de-east-1
REGION_RE = re.compile(r"([a-z]{2}|eusc)(-[a-z]+)+-\d+")

# ssh(1) option letters without and with a value, as in OpenSSH's getopt() string
SSH_FLAGS = "46AaCfGgKkMNnqsTtVvXxYy"
SSH_VALUE_FLAGS = "BbcDEeFIiJLlmOoPpQRSWw"


@dataclasses.dataclass
class SshArgs:
    """The ssh command line: options (one flag and its value per token), destination and remote command."""

    options: list[str] = dataclasses.field(default_factory=list)
    destination: str = ""
    command: list[str] = dataclasses.field(default_factory=list)

    def option(self, flag: str) -> str:
        """Return the first value of a value option, e.g. option("-l"), or ''."""
        options = iter(self.options)
        for option in options:
            if option[1] in SSH_VALUE_FLAGS:
                value = next(options)
                if option == flag:
                    return value
        return ""


def split_ssh_argv(argv: list[str], own_options: dict[str, bool]) -> tuple[list[str], SshArgs]:
    """
    Split argv the way ssh(1) parses it: options before and after the destination, up to
    the first word after it, which starts the remote command. '--' ends the options.

    Long options and the short ones in 'own_options' ({option: takes_value}) are ours,
    everything else is ssh's. The remote command is never looked at.
    Raises ValueError for an option without its value.
    See docs/adr/0003-ec2-ssh-argv-parsing.md
    """
    own: list[str] = []
    ssh = SshArgs()
    args = list(argv)

    def value(option: str) -> str:
        if not args:
            raise ValueError(f"option {option} requires a value")
        return args.pop(0)

    while args:
        arg = args.pop(0)

        if arg == "--":
            if not ssh.destination and args:
                ssh.destination = args.pop(0)
            ssh.command = args
            break

        if arg.startswith("--"):
            option = arg.split("=", 1)[0]
            own.append(arg)
            if own_options.get(option) and "=" not in arg:
                own.append(value(option))
            continue

        if not arg.startswith("-") or arg == "-":
            if ssh.destination:
                ssh.command = [arg, *args]
                break
            ssh.destination = arg
            continue

        # A cluster of short options, e.g. -tt, -p2222 or -vl user
        for pos, letter in enumerate(arg[1:], 2):
            option = "-" + letter
            is_own = option in own_options
            target = own if is_own else ssh.options
            target.append(option)
            if own_options[option] if is_own else letter in SSH_VALUE_FLAGS:
                target.append(arg[pos:] or value(option))
                break

    return own, ssh


def ssh_config(options: list[str], destination: str) -> dict[str, list[str]]:
    """
    Return the config that ssh would use for 'destination' with these options and the ssh_config,
    as {keyword: [values]}. Most keywords have one value, e.g. "user", some more, e.g. "identityfile".
    """
    result = subprocess.run(["ssh", "-G", *options, "--", destination], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.error("ssh -G failed: %s", result.stderr.strip())
        sys.exit(255)
    config: dict[str, list[str]] = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition(" ")
        config.setdefault(key, []).append(value)
    return config


def proxy_target(value: str) -> tuple[str, str, int]:
    """argparse type for --proxy USER@HOST:PORT, ssh's %r@%h:%p"""
    user_host, _, port = value.rpartition(":")
    user, _, host = user_host.rpartition("@")
    if not (user and host):
        raise argparse.ArgumentTypeError(f"{value} isn't USER@HOST:PORT")
    return user, host, port_number(1)(port)


def split_host(host: str) -> tuple[str, str]:
    """Split NAME[.REGION].ssm into NAME and REGION, or ''. Other host names come back as they are."""
    name, dot, suffix = host.rpartition(".")
    if not dot or suffix.lower() != "ssm":
        return host, ""
    rest, _, region = name.rpartition(".")
    if rest and REGION_RE.fullmatch(region.lower()):
        return rest, region.lower()
    return name, ""


def parse_args(argv: list) -> tuple[argparse.Namespace, SshArgs]:
    """
    Parse command line arguments.
    """

    parser = argparse.ArgumentParser(
        prog="ec2-ssh",  # Python 3.14 on Windows says "python.exe C:\...\Scripts\ec2-ssh"
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
        allow_abbrev=False,
        usage="%(prog)s [OPTIONS] [SSH OPTIONS] [[USER@]INSTANCE [COMMAND ...]]",
    )

    add_general_parameters(parser, long_only=True)

    group_instance = parser.add_argument_group("Instance Selection")
    group_instance.add_argument(
        "--list",
        dest="list",
        action="store_true",
        help="List instances available for SSM Session",
    )

    group_ec2ic = parser.add_argument_group("EC2 Instance Connect")
    group_ec2ic.add_argument("--reason", type=session_reason, help="The reason for connecting to the instance.")
    group_ec2ic.add_argument(
        "--user",
        "-u",
        dest="user",
        metavar="USER",
        action="append",
        default=[],
        help="Log in as USER, the same as -l USER or USER@INSTANCE. Default: the user ssh would use.",
    )
    group_ec2ic.add_argument("--send-key", dest="send_key", action="store_true", default=True, help=argparse.SUPPRESS)
    group_send_key = group_ec2ic.add_mutually_exclusive_group()
    group_send_key.add_argument(
        "--gen-key",
        dest="gen_key",
        action="store_true",
        help="Send a new key, made for this connection only, instead of one of yours",
    )
    group_send_key.add_argument(
        "--no-send-key",
        dest="send_key",
        action="store_false",
        help="Don't send the SSH key to the instance with EC2 Instance Connect",
    )
    group_ec2ic.add_argument(
        "--use-endpoint",
        dest="use_endpoint",
        action="store_true",
        default=False,
        help="Connect using 'EC2 Instance Connect Endpoint'",
    )

    group_config = parser.add_argument_group("ssh_config").add_mutually_exclusive_group()
    group_config.add_argument(
        "--print-ssh-config",
        dest="print_ssh_config",
        action="store_true",
        help="Print an ssh_config stanza that lets ssh, scp, rsync, VS Code, etc. connect to "
        "i-..., mi-..., NAME.ssm and NAME.REGION.ssm through %(prog)s --proxy",
    )
    group_config.add_argument(
        "--proxy",
        metavar="USER@HOST:PORT",
        type=proxy_target,
        help="Be ssh's ProxyCommand, as set up by --print-ssh-config",
    )
    # ssh's %n, the host as typed, e.g. a Host alias with its own IdentityFile
    parser.add_argument("--ssh-host", help=argparse.SUPPRESS)

    parser.description = "Open SSH connection through Session Manager"
    parser.epilog = f"""
IMPORTANT: instances must be registered in AWS Systems Manager (SSM)
before you can start a shell session! Instances not registered in SSM
will not be recognised by {parser.prog} nor show up in --list output.

Visit https://aws.nz/aws-utils/ec2-ssh for more info and usage examples.

Author: Michael Ludvig
"""

    # Our options are only taken from before the remote command, the rest is for ssh
    own_options = {option: action.nargs != 0 for action in parser._actions for option in action.option_strings}
    try:
        own_argv, ssh_args = split_ssh_argv(argv, own_options)
        args = parser.parse_args(own_argv)
        login, _, args.host = ssh_args.destination.rpartition("@")  # OpenSSH splits on the last '@'
        args.user = unique_login(*args.user, ssh_args.option("-l"), login)
    except ValueError as e:
        parser.error(str(e))

    if (args.proxy or args.print_ssh_config) and (
        ssh_args.destination or ssh_args.options or args.user or args.list or args.gen_key or args.use_endpoint
    ):
        parser.error("--proxy and --print-ssh-config only go with --profile, --region, --reason and --no-send-key")
    if args.ssh_host and not args.proxy:
        parser.error("--ssh-host only goes with --proxy")

    # If --version do it now and exit
    if args.show_version:
        show_version(args)

    return args, ssh_args


def _quote(value: str) -> str:
    # Win32-OpenSSH runs it without a shell, for aws.exe to split, see docs/adr/0019-windows-and-macos.md
    quoted = subprocess.list2cmdline([value]) if os.name == "nt" else shlex.quote(value)
    return quoted.replace("%", "%%")


def _with_general_options(command: str, profile: str, region: str) -> str:
    if profile:
        command += f" --profile {_quote(profile)}"
    if region:
        command += f" --region {_quote(region)}"
    return command


def proxy_command(profile: str, region: str, use_endpoint: bool, reason: str = "") -> str:
    """
    Return the ssh ProxyCommand. Our values are quoted for the shell that runs it and
    their '%' escaped from ssh's own %h/%p expansion, see docs/adr/0005-proxycommand-quoting.md
    """

    command = _with_general_options("aws", profile, region)

    if use_endpoint:
        if reason:
            logger.warning("--reason is not supported with --use-endpoint, ignoring it")
        return f"{command} ec2-instance-connect open-tunnel --instance-id %h"

    command += " ssm start-session --target %h --document-name AWS-StartSSHSession --parameters portNumber=%p"
    if reason:
        command += f" --reason {_quote(reason)}"
    return command


def ssh_command(ssh_args: SshArgs, profile: str, region: str, use_endpoint: bool, reason: str = "") -> list[str]:
    proxy_option = ["-o", f"ProxyCommand={proxy_command(profile, region, use_endpoint, reason)}"]
    # Nothing after the destination may be read as an ssh option
    return ["ssh", *proxy_option, *ssh_args.options, "--", ssh_args.destination, *ssh_args.command]


def ssh_config_stanza(profile: str, region: str, reason: str, send_key: bool) -> str:
    """The ssh_config for --print-ssh-config. See docs/adr/0024-ec2-ssh-proxy-mode.md"""
    # The full path, for programs that run ssh without the user's PATH, e.g. VS Code.
    # A PyInstaller exe's argv[0] is the name as typed.
    program = sys.executable if getattr(sys, "frozen", False) else os.path.abspath(sys.argv[0])
    # Quiet: no instance table or key message on every scp and rsync, only warnings and errors
    command = _with_general_options(f"{_quote(program)} --quiet", profile, region)
    if reason:
        command += f" --reason {_quote(reason)}"
    if not send_key:
        command += " --no-send-key"
    return (
        "Host i-* mi-* *.ssm\n"
        f"    ProxyCommand {command} --proxy %r@%h:%p --ssh-host %n\n"
        f"    ServerAliveInterval {SERVER_ALIVE_INTERVAL}\n"
    )


def proxy(args: argparse.Namespace) -> int:
    """
    Be ssh's ProxyCommand: send the key for USER to HOST, and connect stdin and stdout to its PORT.
    Nothing but the connection may go to stdout. See docs/adr/0024-ec2-ssh-proxy-mode.md
    """
    user, host, port = args.proxy
    name, region = split_host(host)
    args.region = region or args.region  # NAME.REGION.ssm beats --region

    if not verify_plugin_version(logger, required=PLUGIN_ENV_VERSION):
        return 1

    instance_resolver = InstanceResolver(args)
    instance_id, _ = instance_resolver.resolve_instance(name)
    if not instance_id:
        instance_not_found(name)
    logger.debug("Resolved instance name '%s' to '%s'", name, instance_id)

    # EC2 Instance Connect only knows EC2 instances. ssh -G for the host as typed knows the keys
    # that ssh will offer, except a -i given to ssh itself or an ssh -F config.
    if args.send_key and instance_id.startswith("i-"):
        hint = "Give one with IdentityFile in the ssh_config, or add --no-send-key to the ProxyCommand."
        public_key = select_key(ssh_config([], f"{user}@{args.ssh_host or host}"), hint)
        EC2InstanceConnectHelper(args).send_ssh_key(instance_id, user, public_key)

    ssm_client = instance_resolver.ssm_client
    parameters = {
        "Target": instance_id,
        "DocumentName": "AWS-StartSSHSession",
        "Parameters": {"portNumber": [str(port)]},
    }
    if args.reason:
        parameters["Reason"] = args.reason
    logger.debug("Starting session: %s", parameters)
    return run_plugin(ssm_client, ssm_client.start_session(**parameters), parameters, args.profile)


def main() -> int:
    args, ssh_args = parse_args(sys.argv[1:])

    if args.log_level == logging.DEBUG:
        ssh_args.options.insert(0, "-v")

    configure_logging(args.log_level)

    if args.print_ssh_config:
        if isinstance(sys.stdout, io.TextIOWrapper):  # ssh reads its config as UTF-8
            sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(ssh_config_stanza(args.profile, args.region, args.reason, args.send_key))
        return 0

    try:
        if args.proxy:
            return proxy(args)

        instance_resolver = InstanceResolver(args)

        if args.list:
            instance_resolver.print_list()
            sys.exit(0)

        if args.use_endpoint and not verify_awscli_version(MIN_AWSCLI_VERSION_ENDPOINT, logger):
            sys.exit(1)

        if not args.use_endpoint and not verify_plugin_version(logger, required=MIN_PLUGIN_VERSION):
            sys.exit(1)

        if args.host:
            instance_id, _ = instance_resolver.resolve_instance(args.host)
        else:
            headers, session_details = instance_resolver.print_list(quiet=True)
            instance_id = target_selector(headers, session_details)["InstanceId"]

        # The --gen-key key is deleted when ssh exits, so ssh runs as our child, see docs/adr/0010-eic-key-selection.md
        with tempfile.TemporaryDirectory(prefix="ec2-ssh-") if args.gen_key else contextlib.nullcontext() as key_dir:
            if instance_id:
                logger.debug("Resolved instance name '%s' to '%s'", args.host, instance_id)
                # EC2 Instance Connect only knows EC2 instances, not hybrid managed instances (mi-)
                is_ec2 = instance_id.startswith("i-")
                if args.use_endpoint and not is_ec2:
                    logger.error("--use-endpoint only works with EC2 instances, not with %s", instance_id)
                    sys.exit(1)
                # ssh only sees the instance ID, so ask it which user it would use for the name we were given.
                # See docs/adr/0004-ec2-ssh-login-user.md
                login_name = args.user or ssh_config(ssh_args.options, args.host or instance_id)["user"][0]
                ssh_args.destination = f"{login_name}@{instance_id}"
                if args.send_key and not is_ec2:
                    logger.info("Not sending an SSH key to %s, it's not an EC2 instance", instance_id)
                elif args.send_key:
                    if key_dir:  # Offered first and alone
                        ssh_args.options[:0] = ["-i", generate_key(key_dir), "-o", "IdentitiesOnly=yes"]
                    public_key = select_key(ssh_config(ssh_args.options, ssh_args.destination))
                    EC2InstanceConnectHelper(args).send_ssh_key(instance_id, login_name, public_key)
            else:
                # E.g. an ssh_config alias, let ssh resolve it
                logger.info("No instance found for '%s', passing it to ssh as is", args.host)

            if ssh_config(ssh_args.options, ssh_args.destination)["serveraliveinterval"] == ["0"]:
                ssh_args.options[:0] = ["-o", f"ServerAliveInterval={SERVER_ALIVE_INTERVAL}"]
            command = ssh_command(ssh_args, args.profile, args.region, args.use_endpoint, args.reason)
            logger.debug("Running: %s", command)
            # On Windows os.execvp() doesn't replace us, see docs/adr/0019-windows-and-macos.md
            if key_dir or os.name == "nt":
                return run_session(command)
            os.execvp(command[0], command)

    except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError) as e:
        handle_boto_error(e, logger, args.profile)
        sys.exit(1)

    return 0


if __name__ == "__main__":
    sys.exit(main())
