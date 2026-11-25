#!/usr/bin/env python3

# Convenience wrapper around Session Manager's StartSession for port forwarding.
# Supports both local port forwarding and remote host forwarding.
#
# See https://github.com/mludvig/aws-ssm-tools for more info.
#
# Author: Michael Ludvig (https://aws.nz)

import argparse
import contextlib
import ipaddress
import logging
import re
import socket
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

import botocore.exceptions

from .common import (
    add_general_parameters,
    configure_logging,
    handle_boto_error,
    session_reason,
    show_version,
    verify_plugin_version,
)
from .picker import target_selector
from .plugin import PLUGIN_ENV_VERSION, run_plugin
from .resolver import InstanceResolver, instance_not_found

logger = logging.getLogger("ssm-tools.ssm-port-forward")


def port_number(lowest: int) -> Callable[[str], int]:
    """argparse type for a port from 'lowest' to 65535. See docs/adr/0014-validate-before-start-session.md"""

    def port(value: str) -> int:
        try:
            if lowest <= int(value) <= 65535:
                return int(value)
        except ValueError:
            pass
        raise argparse.ArgumentTypeError(f"{value} isn't a port number from {lowest} to 65535")

    return port


def _split(value: str) -> list[str]:
    """Split at the colons outside [IPv6] brackets, and drop the brackets."""
    return [re.sub(r"^\[(.*)\]$", r"\1", part) for part in re.split(r":(?![^\[]*\])", value)]


def host_port(lowest: int) -> Callable[[str], tuple[str | None, int]]:
    """argparse type for [HOST:]PORT"""

    def parse(value: str) -> tuple[str | None, int]:
        parts = _split(value)
        if len(parts) > 2 or not all(parts):
            raise argparse.ArgumentTypeError(f"{value} isn't [HOST:]PORT")
        return (parts[0] if len(parts) == 2 else None), port_number(lowest)(parts[-1])

    return parse


def ssh_forward(value: str) -> tuple[str | None, int, str | None, int | None]:
    """argparse type for -L PORT or ssh's [BIND:]PORT:HOST:HOSTPORT, where an empty BIND or '*' is any address"""
    parts = _split(value)
    if len(parts) == 1:
        return None, port_number(0)(value), None, None
    bind = None
    if len(parts) == 4:
        bind = parts.pop(0) or "0.0.0.0"
        bind = "0.0.0.0" if bind == "*" else bind
    if len(parts) != 3 or not parts[1]:
        raise argparse.ArgumentTypeError(f"{value} isn't PORT or [BIND:]PORT:HOST:HOSTPORT")
    return bind, port_number(0)(parts[0]), parts[1], port_number(1)(parts[2])


def parse_args(argv: list) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ssm-port-forward",  # Python 3.14 on Windows says "python.exe C:\...\Scripts\ssm-port-forward"
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )

    add_general_parameters(parser)

    group_instance = parser.add_argument_group("Instance Selection")
    group_instance.add_argument("INSTANCE", nargs="?", help="Instance ID, Name, Host name or IP address")
    group_instance.add_argument(
        "--list",
        "-l",
        dest="list",
        action="store_true",
        help="List instances available for SSM Session",
    )

    # Several ways to say the same, see docs/adr/0022-port-forward-options.md
    group_fwd = parser.add_argument_group("Port Forwarding")
    group_fwd.add_argument(
        "--remote",
        metavar="[HOST:]PORT",
        type=host_port(1),
        help="Port to forward to, on HOST if given (e.g. an RDS endpoint), else on the instance itself.",
    )
    group_fwd.add_argument(
        "--local",
        metavar="[HOST:]PORT",
        type=host_port(0),
        help="Local address to listen on. The default is 127.0.0.1 and any free port.",
    )
    group_fwd.add_argument(
        "-L",
        dest="forward",
        metavar="SPEC",
        type=ssh_forward,
        help="ssh style [BIND:]PORT:HOST:HOSTPORT. HOST localhost is the instance itself. "
        "An empty BIND or '*' listens on all addresses. A plain PORT is the same as --local-port.",
    )
    group_fwd.add_argument("--remote-port", metavar="PORT", type=port_number(1), help="Same as --remote PORT.")
    group_fwd.add_argument("--remote-host", "-H", metavar="HOST", help="The HOST of --remote.")
    group_fwd.add_argument("--local-port", metavar="PORT", type=port_number(0), help="Same as --local PORT.")
    group_fwd.add_argument("--local-host", metavar="HOST", help="The HOST of --local.")
    group_fwd.add_argument("--reason", type=session_reason, help="The reason for the session.")

    parser.description = "Port-forward through an SSM Session to an EC2 instance"
    parser.epilog = f"""
Examples:
  # RDP to a Windows instance
  {parser.prog} my-instance --remote 3389 --local 13389

  # Forward to an RDS instance in the same VPC, the ssh way
  {parser.prog} my-bastion -L 5432:my-db.cluster.rds.amazonaws.com:5432

  # Let other hosts connect, on any free port
  {parser.prog} my-bastion --remote my-db.cluster.rds.amazonaws.com:5432 --local 0.0.0.0:0

IMPORTANT: instances must be registered in AWS Systems Manager (SSM)
before you can use port forwarding.

Author: Michael Ludvig
"""

    args = parser.parse_args(argv)

    if args.show_version:
        show_version(args)

    local = args.local or (None, None)
    remote = args.remote or (None, None)
    forward = args.forward or (None, None, None, None)
    options = {
        "local host": [("--local-host", args.local_host), ("--local", local[0]), ("-L", forward[0])],
        "local port": [("--local-port", args.local_port), ("--local", local[1]), ("-L", forward[1])],
        "remote host": [("--remote-host", args.remote_host), ("--remote", remote[0]), ("-L", forward[2])],
        "remote port": [("--remote-port", args.remote_port), ("--remote", remote[1]), ("-L", forward[3])],
    }
    for field, offers in options.items():
        given = [(option, value) for option, value in offers if value is not None]
        if len(given) > 1:
            parser.error(f"{' and '.join(option for option, _ in given)} both set the {field}")
        setattr(args, field.replace(" ", "_"), given[0][1] if given else None)

    if not args.list and not args.remote_port:
        parser.error("a remote port is required: --remote [HOST:]PORT, --remote-port or -L")

    if args.remote_host in ("localhost", "127.0.0.1"):
        args.remote_host = None  # The instance itself
    if args.local_host is None:
        args.local_host = "127.0.0.1"
    if args.local_port is None:
        args.local_port = 0  # The plugin picks a free one and says which

    return args


def listen(host: str, port: int) -> socket.socket:
    """Listen on host:port, or exit. Else the plugin waits silently. See docs/adr/0014-validate-before-start-session.md"""
    try:
        family = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE)[0][0]
        # Sets SO_REUSEADDR like the plugin's listener, except on Windows where it would let us share a port in use
        return socket.create_server((host, port), family=family)
    except OSError as e:
        logger.error("Can't listen on %s port %d: %s. Choose another with --local.", host, port, e.strerror or e)
        sys.exit(1)


def check_local_port(port: int) -> None:
    """Exit if the plugin couldn't listen on 127.0.0.1:port"""
    if port:  # Else the plugin picks a free one
        listen("127.0.0.1", port).close()


def relay(listener: socket.socket, port: int) -> None:
    """
    Pass the connections on 'listener' to the plugin on 127.0.0.1:port, which is all it listens on.
    See docs/adr/0022-port-forward-options.md
    """
    listener.settimeout(1)  # To notice when it's closed
    while True:
        try:
            client, _ = listener.accept()
        except TimeoutError:
            continue
        except OSError as e:
            if listener.fileno() != -1:
                logger.warning("Stopped forwarding: %s", e)
            return
        threading.Thread(target=_relay_connection, args=(client, port), daemon=True).start()


def _relay_connection(client: socket.socket, port: int) -> None:
    with client:
        for _ in range(100):  # The plugin starts listening a moment after the session starts
            try:
                upstream = socket.create_connection(("127.0.0.1", port))
                break
            except ConnectionRefusedError:
                time.sleep(0.2)
        else:
            logger.warning("The session isn't forwarding yet, closed a connection")
            return
        with upstream:
            reply = threading.Thread(target=_pipe, args=(upstream, client), daemon=True)
            reply.start()
            _pipe(client, upstream)
            reply.join()


def _pipe(source: socket.socket, sink: socket.socket) -> None:
    try:
        while data := source.recv(65536):
            sink.sendall(data)
        sink.shutdown(socket.SHUT_WR)
    except OSError:  # Reset: end the other direction too
        for sock in (source, sink):
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)


def start_port_forward(ssm_client: Any, instance_id: str, args: argparse.Namespace, local_port: int) -> int:
    """Forward the plugin's 127.0.0.1:local_port to args.remote_host:args.remote_port"""
    parameters: dict[str, Any] = {
        "Target": instance_id,
        "DocumentName": "AWS-StartPortForwardingSession",
        "Parameters": {"portNumber": [str(args.remote_port)], "localPortNumber": [str(local_port)]},
    }
    if args.remote_host:
        parameters["DocumentName"] = "AWS-StartPortForwardingSessionToRemoteHost"
        parameters["Parameters"]["host"] = [args.remote_host]
    if args.reason:
        parameters["Reason"] = args.reason

    logger.debug("Starting session: %s", parameters)
    return run_plugin(ssm_client, ssm_client.start_session(**parameters), parameters, args.profile)


def main() -> int:
    args = parse_args(sys.argv[1:])

    configure_logging(args.log_level)

    try:
        instance_resolver = InstanceResolver(args)

        if args.list:
            instance_resolver.print_list()
            sys.exit(0)

        if not verify_plugin_version(logger, required=PLUGIN_ENV_VERSION):
            sys.exit(1)

        listener = None
        if args.local_host in ("localhost", "127.0.0.1"):  # Where the plugin listens
            check_local_port(args.local_port)
        else:
            listener = listen(args.local_host, args.local_port)

        if not args.INSTANCE:
            headers, session_details = instance_resolver.print_list(quiet=True)
            selected_session = target_selector(headers, session_details)
            instance_id = selected_session["InstanceId"]
        else:
            instance_id, _ = instance_resolver.resolve_instance(args.INSTANCE)

        if not instance_id:
            instance_not_found(args.INSTANCE)

        if not listener:
            return start_port_forward(instance_resolver.ssm_client, instance_id, args, args.local_port)

        host, port = listener.getsockname()[:2]
        if ipaddress.ip_address(host).is_loopback:
            logger.info("Listening on %s port %d", host, port)
        else:
            logger.warning("Listening on %s port %d, reachable from the network", host, port)
        plugin_port = listen("127.0.0.1", 0)
        with plugin_port:
            local_port = plugin_port.getsockname()[1]
        with listener:
            threading.Thread(target=relay, args=(listener, local_port), daemon=True).start()
            return start_port_forward(instance_resolver.ssm_client, instance_id, args, local_port)

    except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError) as e:
        handle_boto_error(e, logger, args.profile)
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
