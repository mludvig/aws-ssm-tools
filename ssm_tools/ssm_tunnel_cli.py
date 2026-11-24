#!/usr/bin/env python3

# Set up IP tunnel through SSM-enabled instance.
#
# See https://aws.nz/aws-utils/ssm-tunnel for more info.
#
# Author: Michael Ludvig (https://aws.nz)

import argparse
import copy
import fcntl
import ipaddress
import logging
import os
import random
import re
import select
import shlex
import signal
import struct
import subprocess
import sys
import threading
import time
from base64 import b64decode, b64encode
from typing import Any

import botocore.exceptions
import pexpect

from . import ssm_tunnel_agent
from .common import (
    add_general_parameters,
    bytes_to_human,
    configure_logging,
    exit_on_signal,
    handle_boto_error,
    seconds_to_human,
    session_reason,
    show_version,
    verify_plugin_version,
)
from .picker import target_selector
from .plugin import PLUGIN_ENV_VERSION
from .resolver import InstanceResolver, instance_not_found
from .talker import PROMPT, SessionError, SsmTalker

logger = logging.getLogger("ssm-tools.ssm-tunnel")

tunnel_cidr = "100.64.0.0/16"
keepalive_sec = 10
agent_timeout_sec = 3 * keepalive_sec  # The agent sends keepalives too


def tunnel_network(value: str) -> ipaddress.IPv4Network:
    """An IPv4 network big enough for random_ips()."""
    try:
        network = ipaddress.IPv4Network(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"{value} isn't an IPv4 network: {e}") from None
    if network.prefixlen > 29:
        raise argparse.ArgumentTypeError(f"{value} is too small for a tunnel, it needs to be /29 or bigger")
    return network


def parse_args(argv: list) -> argparse.Namespace:
    """
    Parse command line arguments.
    """

    parser = argparse.ArgumentParser(
        prog="ssm-tunnel",  # Python 3.14 on Windows says "python.exe C:\...\Scripts\ssm-tunnel"
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
        help="List instances registered in SSM.",
    )

    group_network = parser.add_argument_group("Networking Options")
    group_network.add_argument(
        "--route",
        "-R",
        metavar="ROUTE",
        dest="routes",
        type=ipaddress.IPv4Network,
        action="append",
        default=[],
        help="CIDR(s) to route through this tunnel. May be used multiple times.",
    )
    group_network.add_argument(
        "--tunnel-cidr",
        metavar="CIDR",
        type=tunnel_network,
        default=tunnel_cidr,
        help=f"By default the tunnel endpoint IPs are randomly assigned from the reserved {tunnel_cidr} block (RFC6598). This should be ok for most users.",
    )
    group_network.add_argument("--reason", "-r", type=session_reason, help="The reason for the session.")
    group_network.add_argument(
        "--up-down",
        metavar="SCRIPT",
        dest="updown_script",
        type=str,
        help="Script to call during tunnel start up and close down. Check out 'ssm-tunnel-updown.dns-example' that supports setting a custom DNS server when the tunnel goes up.",
    )

    parser.description = "Start IP tunnel to a given SSM instance"
    parser.epilog = f"""
IMPORTANT: instances must be registered in AWS Systems Manager (SSM)
before you can copy files to/from them! Instances not registered in SSM
will not be recognised by {parser.prog} nor show up in --list output.

Visit https://aws.nz/aws-utils/ssm-tunnel for more info and usage examples.

Author: Michael Ludvig
"""

    # Parse supplied arguments
    args = parser.parse_args(argv)

    # If --version do it now and exit
    if args.show_version:
        show_version(args)

    return args


class SsmTunnel(SsmTalker):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        # Stats structure
        self.stats = {"ts": 0.0, "l2r": 0, "r2l": 0}
        self.stats_lock = threading.Lock()
        self.stats_secs = 10
        self.stats_refresh = 0.5  # Print stats every this many seconds

        self._exiting = False

        self.tun_name = ""
        self._tun_fd = -1
        self.local_ip = self.remote_ip = ""
        self.routes: list[str] = []
        self.updown_script = ""
        self.updown_up_success = False

    def run_command(self, *command: str, check: bool = True) -> None:
        logger.debug("command: %s", shlex.join(command))
        subprocess.run(command, check=check)

    def open_remote_tunnel(self) -> None:
        logger.debug("Creating tunnel")

        # Open remote tun0 device
        self._child.sendline(f"ssm-tunnel-agent {self.remote_ip} {self.local_ip}")
        # See docs/adr/0013-ssm-tunnel-agent-cleanup.md
        ready = r"# Agent device \S+ is ready \[[^\]]*\](?: version (\d+))?\r*\n"  # Version 1 didn't say
        match = self.expect([ready, "ssm-tunnel-agent: .*not found", re.escape(PROMPT)], "ssm-tunnel-agent")
        if match == 1:
            raise SessionError(
                f"ssm-tunnel-agent is not installed on {self._instance_id}. Copy {ssm_tunnel_agent.__file__} "
                "to /usr/local/bin/ssm-tunnel-agent there and make it executable (it needs Python 3.8+).",
            )
        if match == 2:  # It has exited
            raise SessionError(f"ssm-tunnel-agent failed: {self.output()}")
        logger.debug(self._child.after)
        version = int(self._child.match.group(1) or 1)
        if version < ssm_tunnel_agent.AGENT_VERSION:
            logger.warning(
                "ssm-tunnel-agent on %s is version %d, this ssm-tunnel has version %d. "
                "Update it from %s to get its fixes.",
                self._instance_id,
                version,
                ssm_tunnel_agent.AGENT_VERSION,
                ssm_tunnel_agent.__file__,
            )

    def open_local_tunnel(self) -> None:
        tun_suffix = ".".join(self.local_ip.split(".")[2:])
        self.create_tun(f"tunSSM.{tun_suffix}")
        self._tun_fd = self.open_tun()

        logger.debug("# Local device %s is ready", self.tun_name)
        logger.info("Local IP: %s / Remote IP: %s", self.local_ip, self.remote_ip)

    def create_tun(self, tun_name: str) -> None:
        try:
            self.run_command("sudo", "ip", "tuntap", "add", tun_name, "mode", "tun", "user", str(os.getuid()))
            self.tun_name = tun_name  # Only now is it ours to delete
            self.run_command("sudo", "ip", "addr", "add", self.local_ip, "peer", self.remote_ip, "dev", self.tun_name)
            self.run_command("sudo", "ip", "link", "set", self.tun_name, "up")
            # Configure routes
            for route in self.routes:
                self.run_command("sudo", "ip", "route", "add", route, "via", self.remote_ip)
        except (subprocess.CalledProcessError, OSError) as e:  # OSError: e.g. no sudo
            if isinstance(e, OSError):
                logger.error("%s", e)
            elif not self.tun_name:
                logger.error("Can't create %s. Is another ssm-tunnel using the same IPs? Try again.", tun_name)
            self.delete_tun()
            sys.exit(1)
        except Exception as e:
            logger.exception(e)
            self.delete_tun()
            raise

    def delete_tun(self) -> None:
        # We don't check return code here - best effort to close and delete the device
        if self._tun_fd >= 0:
            try:
                os.close(self._tun_fd)
                self._tun_fd = -1
            except Exception as e:
                logger.exception(e)
        if self.tun_name:
            self.run_command("sudo", "ip", "link", "set", self.tun_name, "down", check=False)
            self.run_command("sudo", "ip", "tuntap", "del", self.tun_name, "mode", "tun", check=False)
            self.tun_name = ""

    def open_tun(self) -> int:
        TUNSETIFF = 0x400454CA
        IFF_TUN = 0x0001

        tun_fd = os.open("/dev/net/tun", os.O_RDWR)

        flags = IFF_TUN
        ifr = struct.pack("16sH22s", self.tun_name.encode(), flags, b"\x00" * 22)
        fcntl.ioctl(tun_fd, TUNSETIFF, ifr)

        return tun_fd

    def local_to_remote(self) -> None:
        last_ts = time.time()
        while True:
            if self._exiting:
                break
            try:
                r, _, _ = select.select([self._tun_fd], [], [], 1)
                if self._tun_fd not in r:
                    if last_ts + keepalive_sec < time.time():
                        # Keepalive timeout - send '#'
                        self._child.sendline("#")
                        last_ts = time.time()
                    continue
                buf = os.read(self._tun_fd, 1504)  # Virtual GRE header adds 4 bytes
                self._child.sendline(f"%{b64encode(buf).decode('ascii')}")
            except OSError as e:
                if not self._exiting:  # Else it's the tun device closing under us
                    logger.error("Tunnel failed: %s", e)
                    self._exiting = True
                break

            last_ts = time.time()
            # Update stats
            self.stats_lock.acquire()
            self.stats["l2r"] += len(buf)
            self.stats_lock.release()

        logger.debug("local_to_remote() has exited.")

    def remote_to_local(self) -> None:
        last_ts = time.time()
        while True:
            if self._exiting:
                break
            try:
                # Our prompt means the agent has exited. It comes without a newline.
                match = self._child.expect(["\r\n", re.escape(PROMPT), pexpect.EOF])
            except pexpect.exceptions.TIMEOUT:  # Nothing for the child's timeout (10 s)
                if self._exiting:
                    break
                if last_ts + agent_timeout_sec < time.time():
                    logger.warning(
                        "Nothing from the agent for %d s, not even a keepalive - tunnel went down?",
                        agent_timeout_sec,
                    )
                    self._exiting = True
                continue
            last_ts = time.time()
            if self._exiting:  # We're closing the session ourselves
                break
            if match == 1:
                logger.warning("ssm-tunnel-agent has exited - tunnel went down?")
                self._exiting = True
                break
            if match == 2:
                logger.warning("Received unexpected EOF - tunnel went down?")
                self._exiting = True
                break
            line = self._child.before
            if not line or line[0] != "%":
                continue

            buf = b64decode(line[1:])
            os.write(self._tun_fd, buf)
            # Update stats
            self.stats_lock.acquire()
            self.stats["r2l"] += len(buf)
            self.stats_lock.release()

        logger.debug("remote_to_local() has exited.")

    def process_traffic(self) -> None:
        tr_l2r = threading.Thread(target=self.local_to_remote, args=[])
        tr_l2r.daemon = True
        tr_l2r.start()

        tr_r2l = threading.Thread(target=self.remote_to_local, args=[])
        tr_r2l.daemon = True
        tr_r2l.start()

        try:
            self.display_stats()

        except KeyboardInterrupt:
            print("")  # Just to avoid "^C" at the end of line

    def run_updown(self, status: str) -> None:
        if not self.updown_script:
            return

        if status == "down" and not self.updown_up_success:
            # If 'up' failed we are immediately called with 'down' - don't do anything.
            return

        command = [self.updown_script, status, self.tun_name, self.local_ip, self.remote_ip, *self.routes]
        try:
            logger.info("Running --up-down script: %s", shlex.join(command))
            self.run_command(*command)
            self.updown_up_success = True
        except (subprocess.CalledProcessError, OSError) as e:  # OSError: not found, not executable, ...
            logger.error("Updown script %s failed: %s", self.updown_script, e)
            if status == "up":  # Else we're in start()'s 'finally', with the session still to close
                sys.exit(1)

    def start(self, local_ip: str, remote_ip: str, routes: list[str], updown_script: str) -> None:
        self.local_ip = local_ip
        self.remote_ip = remote_ip
        self.routes = routes
        self.updown_script = updown_script

        try:
            self.open_remote_tunnel()
            self.open_local_tunnel()
            self.run_updown("up")
            self.process_traffic()
            if self._exiting:  # The session went down, not Ctrl-C
                sys.exit(1)

        finally:
            logger.info("Closing tunnel, please wait...")
            self.run_updown("down")
            self._exiting = True  # Before exit(), so the threads don't report it as the tunnel going down
            self.exit()
            self.delete_tun()

    def display_stats(self) -> None:
        def _erase_line() -> None:
            print("\r\x1b[K", end="")  # Erase line

        stat_history = [self.stats]
        stat_history_len = int(self.stats_secs / self.stats_refresh)
        start_ts = time.time()

        while not self._exiting:
            time.sleep(self.stats_refresh)

            # Take another 'stat' snapshot
            self.stats_lock.acquire()
            stat_history.insert(1, copy.copy(self.stats))
            self.stats_lock.release()
            stat_history[1]["ts"] = time.time()

            # Calculate sliding window average
            if stat_history[1]["ts"] > stat_history[-1]["ts"]:
                l2r_avg = (stat_history[1]["l2r"] - stat_history[-1]["l2r"]) / (
                    stat_history[1]["ts"] - stat_history[-1]["ts"]
                )
                r2l_avg = (stat_history[1]["r2l"] - stat_history[-1]["r2l"]) / (
                    stat_history[1]["ts"] - stat_history[-1]["ts"]
                )
            else:
                l2r_avg = r2l_avg = 0.0

            # Trim the oldest points
            del stat_history[stat_history_len + 1 :]

            uptime = seconds_to_human(time.time() - start_ts, decimal=0)
            l2r_t_h, l2r_t_u = bytes_to_human(stat_history[1]["l2r"])
            r2l_t_h, r2l_t_u = bytes_to_human(stat_history[1]["r2l"])
            l2r_a_h, l2r_a_u = bytes_to_human(l2r_avg)
            r2l_a_h, r2l_a_u = bytes_to_human(r2l_avg)

            _erase_line()
            print(
                f"{uptime} | In: {r2l_t_h:6.1f}{r2l_t_u:>2s} @ {r2l_a_h:6.1f}{r2l_a_u:>2s}/s | Out: {l2r_t_h:6.1f}{l2r_t_u:>2s} @ {l2r_a_h:6.1f}{l2r_a_u:>2s}/s",
                end="",
                flush=True,
            )


def random_ips(net: ipaddress.IPv4Network) -> tuple[str, str]:
    # Random host-part
    host_bytes = int(random.uniform(2, 2 ** (net.max_prefixlen - net.prefixlen) - 4)) & 0xFFFFFFFE
    # Construct local/remote IP
    local_ip = net.network_address + host_bytes
    remote_ip = net.network_address + host_bytes + 1
    return local_ip.compressed, remote_ip.compressed


def main() -> int:
    ## Split command line args
    args = parse_args(sys.argv[1:])

    if sys.platform != "linux":
        print("The 'ssm-tunnel' program only works on Linux at the moment!", file=sys.stderr)
        print(
            "In other systems you are welcome to install it in VirtualBox or in a similar virtual environment running Linux.",
            file=sys.stderr,
        )
        sys.exit(1)

    configure_logging(args.log_level)

    tunnel = None
    try:
        instance_resolver = InstanceResolver(args)
        if args.list:
            instance_resolver.print_list()
            sys.exit(0)

        if not verify_plugin_version(logger, required=PLUGIN_ENV_VERSION):
            sys.exit(1)

        if not args.INSTANCE:
            headers, session_details = instance_resolver.print_list(quiet=True)
            selected_session = target_selector(headers, session_details)
            args.INSTANCE = selected_session["InstanceId"]

        instance_id, _ = instance_resolver.resolve_instance(args.INSTANCE)
        if not instance_id:
            instance_not_found(args.INSTANCE)

        local_ip, remote_ip = random_ips(args.tunnel_cidr)
        # Close the session, and delete the tun device and its routes, in the 'finally's
        for signum in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, exit_on_signal)
        tunnel = SsmTunnel(instance_resolver.ssm_client, instance_id, args.profile, args.reason)
        tunnel.start(local_ip, remote_ip, [str(route) for route in args.routes], args.updown_script)

    except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError) as e:
        handle_boto_error(e, logger, args.profile)
        sys.exit(1)

    except SessionError as e:
        logger.error("%s", e)
        sys.exit(1)

    except KeyboardInterrupt:
        print("", file=sys.stderr)  # Just to avoid "^C" at the end of line
        sys.exit(130)

    finally:
        if tunnel:
            tunnel.delete_tun()

    return 0


if __name__ == "__main__":
    main()
