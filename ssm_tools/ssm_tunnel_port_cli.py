#!/usr/bin/env python3

# Set up Port forward tunnel through SSM-enabled instance.
#
# Author: James Baker

import os
import subprocess

import botocore.exceptions

from .common import *
from .resolver import InstanceResolver

logger = logging.getLogger()


def parse_args():
    """
    Parse command line arguments.
    """

    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False
    )

    group_general = add_general_parameters(parser)

    group_instance = parser.add_argument_group("Instance Selection")
    group_instance.add_argument(
        "INSTANCE", nargs="?", help="Instance ID, Name, Host name or IP address"
    )
    group_instance.add_argument(
        "--list",
        "-l",
        dest="list",
        action="store_true",
        help="List instances registered in SSM.",
    )

    group_network = parser.add_argument_group("Networking Options")
    group_network.add_argument(
        "--port",
        metavar="PORT",
        dest="port",
        type=str,
        help="Port to route through ssm tunnel.",
    )
    group_network.add_argument(
        "--az",
        metavar="AZ",
        dest="az",
        type=str,
        help="az of ssm instance to tunnel through",
    )
    group_network.add_argument(
        "--os-user",
        metavar="OSUSER",
        dest="osuser",
        type=str,
        help="OS user to tunnel with",
    )
    group_network.add_argument(
        "--endpoint",
        metavar="ENDPOINT",
        dest="endpoint",
        type=str,
        help="Endpoint to route through ssm tunnel.",
    )
    group_network.add_argument(
        "--up-down",
        metavar="SCRIPT",
        dest="updown_script",
        type=str,
        help="""Script to call
        during tunnel start up and close down. Check out 'ssm-tunnel-updown.dns-example' that
        supports setting a custom DNS server when the tunnel goes up.""",
    )

    parser.description = "Start port tunnel to a given SSM instance"
    parser.epilog = f"""
IMPORTANT: instances must be registered in AWS Systems Manager (SSM)
before you can copy files to/from them! Instances not registered in SSM
will not be recognised by {parser.prog} nor show up in --list output.

Author: James Baker
"""

    # Parse supplied arguments
    args = parser.parse_args()

    # If --version do it now and exit
    if args.show_version:
        show_version(args)

    # Require exactly one of INSTANCE or --list
    if bool(args.INSTANCE) + bool(args.list) != 1:
        parser.error("Specify either INSTANCE or --list")

    return args


def gen_key():
    subprocess.call(
        'ssh-keygen -t rsa -f /tmp/temp -N "" 2>/dev/null <<< y >/dev/null', shell=True
    )


def send_ssh_key(instance_id, os_user, az, profile=None, region=None):
    extra_args = ""
    if profile:
        extra_args += f"--profile {profile} "
    if region:
        extra_args += f"--region {region} "
    command = f"aws {extra_args} ec2-instance-connect send-ssh-public-key --instance-id {instance_id} --availability-zone {az} --instance-os-user {os_user} --ssh-public-key file:///tmp/temp.pub > /dev/null 2> /dev/null"
    logger.info("Running: %s", command)
    os.system(command)


def start_portfwd_session(
    instance_id, port, endpoint, os_user, profile=None, region=None
):
    extra_args = ""
    if profile:
        extra_args += f"--profile {profile} "
    if region:
        extra_args += f"--region {region} "
    command = f'ssh -i /tmp/temp -q -N -M -S /tmp/temp-ssh.sock -L {port}:{endpoint}:{port} {os_user}@{instance_id} -o "UserKnownHostsFile=/dev/null" -o "StrictHostKeyChecking=no" -o ProxyCommand="aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters portNumber=%p {extra_args}"'
    logger.info("Running: %s", command)
    os.system(command)


def main():
    args = parse_args()
    gen_key()

    try:
        instance = None
        if args.list:
            InstanceResolver(args).print_list()
            quit(0)

        instance_id = InstanceResolver(args).resolve_instance(args.INSTANCE)

        if not instance_id:
            logger.warning("Could not resolve Instance ID for '%s'", args.INSTANCE)
            logger.warning("Perhaps the '%s' is not registered in SSM?", args.INSTANCE)
            quit(1)
        send_ssh_key(
            instance_id,
            os_user=args.osuser,
            az=args.az,
            profile=args.profile,
            region=args.region,
        )
        start_portfwd_session(
            instance_id,
            port=args.port,
            endpoint=args.endpoint,
            os_user=args.osuser,
            profile=args.profile,
            region=args.region,
        )

    except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError) as e:
        logger.error(e)
        quit(1)


if __name__ == "__main__":
    main()
