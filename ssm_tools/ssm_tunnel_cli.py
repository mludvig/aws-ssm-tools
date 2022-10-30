#!/usr/bin/env python3

# Set up IP tunnel through SSM-enabled instance.
#
# See https://aws.nz/aws-utils/ssm-tunnel for more info.
#
# Author: Michael Ludvig (https://aws.nz)

import os
import sys
import time
import copy
import errno
import logging
import threading
import random
import struct
import select
import fcntl
import argparse
import ipaddress
from base64 import b64encode, b64decode

from typing import List, Any, Tuple

import pexpect
import botocore.exceptions

from .common import add_general_parameters, show_version, configure_logging, bytes_to_human, seconds_to_human
from .talker import SsmTalker
from .resolver import InstanceResolver

logger = logging.getLogger("ssm-tools.ssm-tunnel")

tunnel_cidr = "100.64.0.0/16"
keepalive_sec = 10


def parse_args(argv: list) -> argparse.Namespace:
    """
    Parse command line arguments.
    """

    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False)

    add_general_parameters(parser)

    # fmt: off
    group_instance = parser.add_argument_group("Instance Selection")
    group_instance.add_argument("INSTANCE", nargs="?", help="Instance ID, Name, Host name or IP address")
    group_instance.add_argument("--list", "-l", dest="list", action="store_true", help="List instances registered in SSM.")

    group_network = parser.add_argument_group("Networking Options")
    group_network.add_argument("--route", "-r", metavar="ROUTE", dest="routes", type=str, action="append", default=[], help="CIDR(s) to route through this tunnel. May be used multiple times.")
    group_network.add_argument("--tunnel-cidr", metavar="CIDR", type=str, default=tunnel_cidr,
        help=f"By default the tunnel endpoint IPs are randomly assigned from the reserved {tunnel_cidr} block (RFC6598). This should be ok for most users."
    )
    group_network.add_argument("--up-down", metavar="SCRIPT", dest="updown_script", type=str,
        help="Script to call during tunnel start up and close down. Check out 'ssm-tunnel-updown.dns-example' that supports setting a custom DNS server when the tunnel goes up."
    )
    # fmt: on

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

    # Require exactly one of INSTANCE or --list
    if bool(args.INSTANCE) + bool(args.list) != 1:
        parser.error("Specify either INSTANCE or --list")

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
        self.routes: List[str] = []
        self.updown_script = ""
        self.updown_up_success = False

    def run_command(self, command: str, assert_0: bool = True) -> None:
        logger.debug("command: %s", command)
        ret = os.system(command)
        if assert_0:
            assert ret == 0

    def open_remote_tunnel(self) -> None:
        logger.debug("Creating tunnel")

        # Open remote tun0 device
        self._child.sendline(f"ssm-tunnel-agent {self.remote_ip} {self.local_ip}")
        patterns = ["# Agent device .* is ready", "command not found"]
        match = self._child.expect(patterns)
        if match != 0:  # Index matched in the 'patterns'
            logger.error("Unable to establish the tunnel!")
            logger.error("ssm-tunnel-agent: command not found on the target instance %s.", self._instance_id)
            logger.error("Install with: ssm-session %s --command 'sudo pip install aws-ssm-tunnel-agent'", self._instance_id)
            logger.error("(replace 'pip' with 'pip3' above if the former doesn't work)")
            sys.exit(1)
        logger.debug(self._child.after)

    def open_local_tunnel(self) -> None:
        tun_suffix = ".".join(self.local_ip.split(".")[2:])
        self.tun_name = f"tunSSM.{tun_suffix}"

        self.create_tun()
        self._tun_fd = self.open_tun()

        logger.debug("# Local device %s is ready", self.tun_name)
        logger.info("Local IP: %s / Remote IP: %s", self.local_ip, self.remote_ip)

    def create_tun(self) -> None:
        try:
            user_id = os.getuid()
            self.run_command(f"sudo ip tuntap add {self.tun_name} mode tun user {user_id}")
            self.run_command(f"sudo ip addr add {self.local_ip} peer {self.remote_ip} dev {self.tun_name}")
            self.run_command(f"sudo ip link set {self.tun_name} up")
            # Configure routes
            for route in self.routes:
                self.run_command(f"sudo ip route add {route} via {self.remote_ip}")
        except AssertionError:
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
            self.run_command(f"sudo ip link set {self.tun_name} down", assert_0=False)
            self.run_command(f"sudo ip tuntap del {self.tun_name} mode tun", assert_0=False)
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
                r, w, x = select.select([self._tun_fd], [], [], 1)
                if not self._tun_fd in r:
                    if last_ts + keepalive_sec < time.time():
                        # Keepalive timeout - send '#'
                        self._child.sendline("#")
                        last_ts = time.time()
                    continue
                buf = os.read(self._tun_fd, 1504)  # Virtual GRE header adds 4 bytes
                self._child.sendline("%{}".format(b64encode(buf).decode("ascii")))
            except OSError as e:
                if e.errno == errno.EBADF and self._exiting:
                    break

            last_ts = time.time()
            # Update stats
            self.stats_lock.acquire()
            self.stats["l2r"] += len(buf)
            self.stats_lock.release()

        logger.debug("local_to_remote() has exited.")

    def remote_to_local(self) -> None:
        while True:
            if self._exiting:
                break
            try:
                line = self._child.readline()
            except pexpect.exceptions.TIMEOUT:
                # This is a long timeout, 30 sec, not very useful
                continue
            if type(self._child.after) == pexpect.exceptions.EOF:
                logger.warning("Received unexpected EOF - tunnel went down?")
                self._exiting = True
                break
            if not line or line[0] != "%":
                continue

            buf = b64decode(line[1:].strip("\r\n"))
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

        routes = " ".join(self.routes)
        try:
            cmd = f"{self.updown_script} {status} {self.tun_name} {self.local_ip} {self.remote_ip} {routes}"
            logger.info("Running --up-down script: %s", cmd)
            self.run_command(cmd)
            self.updown_up_success = True
        except AssertionError:
            logger.error("Updown script %s exitted with error.", self.updown_script)
            sys.exit(1)

    def start(self, local_ip: str, remote_ip: str, routes: List[str], updown_script: str) -> None:
        self.local_ip = local_ip
        self.remote_ip = remote_ip
        self.routes = routes
        self.updown_script = updown_script

        try:
            self.open_remote_tunnel()
            self.open_local_tunnel()
            self.run_updown("up")
            self.process_traffic()

        finally:
            logger.info("Closing tunnel, please wait...")
            self.run_updown("down")
            self.exit()
            self._exiting = True
            self.delete_tun()

    def display_stats(self) -> None:
        def _erase_line() -> None:
            print("\r\x1B[K", end="")  # Erase line

        stat_history = [self.stats]
        stat_history_len = int(self.stats_secs / self.stats_refresh)
        start_ts = time.time()

        while True:
            time.sleep(self.stats_refresh)

            # Take another 'stat' snapshot
            self.stats_lock.acquire()
            stat_history.insert(1, copy.copy(self.stats))
            self.stats_lock.release()
            stat_history[1]["ts"] = time.time()

            # Calculate sliding window average
            if stat_history[1]["ts"] > stat_history[-1]["ts"]:
                l2r_avg = (stat_history[1]["l2r"] - stat_history[-1]["l2r"]) / (stat_history[1]["ts"] - stat_history[-1]["ts"])
                r2l_avg = (stat_history[1]["r2l"] - stat_history[-1]["r2l"]) / (stat_history[1]["ts"] - stat_history[-1]["ts"])
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
            print(f"{uptime} | In: {r2l_t_h:6.1f}{r2l_t_u:>2s} @ {r2l_a_h:6.1f}{r2l_a_u:>2s}/s | Out: {l2r_t_h:6.1f}{l2r_t_u:>2s} @ {l2r_a_h:6.1f}{l2r_a_u:>2s}/s", end="", flush=True)


def random_ips(network: str) -> Tuple[str, str]:
    # Network address
    net = ipaddress.ip_network(network)
    # Random host-part
    host_bytes = int(random.uniform(2, 2 ** (net.max_prefixlen - net.prefixlen) - 4)) & 0xFFFFFFFE
    # Construct local/remote IP
    local_ip = net.network_address + host_bytes
    remote_ip = net.network_address + host_bytes + 1
    return local_ip.compressed, remote_ip.compressed


def main() -> int:
    if sys.platform != "linux":
        print("The 'ssm-tunnel' program only works on Linux at the moment!", file=sys.stderr)
        print("In other systems you are welcome to install it in VirtualBox or in a similar virtual environment running Linux.", file=sys.stderr)
        sys.exit(1)

    ## Split command line args
    args = parse_args(sys.argv[1:])

    configure_logging(args.log_level)

    tunnel = None
    try:
        if args.list:
            # --list
            InstanceResolver(args).print_list()
            sys.exit(0)

        instance_id, _ = InstanceResolver(args).resolve_instance(args.INSTANCE)
        if not instance_id:
            logger.warning("Could not resolve Instance ID for '%s'", args.INSTANCE)
            logger.warning("Perhaps the '%s' is not registered in SSM?", args.INSTANCE)
            sys.exit(1)

        local_ip, remote_ip = random_ips(args.tunnel_cidr)
        tunnel = SsmTunnel(instance_id, profile=args.profile, region=args.region)
        tunnel.start(local_ip, remote_ip, list(args.routes) or [], args.updown_script)

    except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError) as e:
        logger.error(e)
        sys.exit(1)

    finally:
        if tunnel:
            tunnel.delete_tun()

    return 0


if __name__ == "__main__":
    main()
