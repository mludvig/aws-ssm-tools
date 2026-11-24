#!/usr/bin/env python3

# ssm-tunnel-agent - remote agent for ssm-tunnel
#                    this should be installed on the EC2 instance
#                    and available in the $PATH (e.g. in /usr/local/bin)
#
# Author: Michael Ludvig

# Updated for Python 3.8+ compatibility as part of aws-ssm-tools modernization

import contextlib
import fcntl
import os
import select
import signal
import struct
import subprocess
import sys
import threading
import time
from base64 import b64decode, b64encode

# ssm-tunnel warns if this is older than its own copy. See docs/adr/0013-ssm-tunnel-agent-cleanup.md
AGENT_VERSION = 2

timeout_sec = 60  # Exit and cleanup if we don't get any input
keepalive_sec = 10  # Send a dummy message this often

IP_FORWARD = "/proc/sys/net/ipv4/ip_forward"
NET_DEVICES = "/sys/class/net"
forwarding_was_off = False  # If so, turn it back off on exit, unless another tunnel remains


def run_command(*command: str, check: bool = True) -> None:
    with contextlib.suppress(OSError):  # Our terminal may be gone, e.g. after a TerminateSession
        print(f"# {' '.join(command)}", flush=True)
    subprocess.run(command, check=check)


def create_tun(tun_name: str, local_ip: str, remote_ip: str) -> None:
    global forwarding_was_off
    try:
        run_command("sudo", "ip", "tuntap", "add", tun_name, "mode", "tun", "user", str(os.getuid()))
    except subprocess.CalledProcessError:
        print(f"# ERROR: Can't create {tun_name}. Is another ssm-tunnel using it?")
        sys.exit(1)  # It isn't ours, so don't delete it
    try:
        run_command("sudo", "ip", "addr", "add", local_ip, "peer", remote_ip, "dev", tun_name)
        run_command("sudo", "ip", "link", "set", tun_name, "up")
        with open(IP_FORWARD) as f:
            forwarding_was_off = f.read().strip() == "0"
        if forwarding_was_off:
            run_command("sudo", "sysctl", "-q", "-w", "net.ipv4.ip_forward=1", check=False)
        run_command(*masquerade("-I", tun_name, remote_ip), check=False)
    except subprocess.CalledProcessError:
        delete_tun(tun_name, local_ip, remote_ip)
        sys.exit(1)
    except BaseException:  # Including a signal
        delete_tun(tun_name, local_ip, remote_ip)
        raise


def ignore_signals() -> None:
    """Let the cleanup, and the commands it runs, finish. bash sends SIGHUP twice when the session goes."""
    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(signum, signal.SIG_IGN)


def delete_tun(tun_name: str, local_ip: str, remote_ip: str) -> None:
    ignore_signals()
    # We don't check return code here - best effort to delete the devices
    run_command("sudo", "ip", "link", "set", tun_name, "down", check=False)
    run_command("sudo", "ip", "tuntap", "del", tun_name, "mode", "tun", check=False)
    run_command(*masquerade("-D", tun_name, remote_ip), check=False)
    if forwarding_was_off and not any(name.startswith("tunSSM.") for name in os.listdir(NET_DEVICES)):
        run_command("sudo", "sysctl", "-q", "-w", "net.ipv4.ip_forward=0", check=False)


def masquerade(action: str, tun_name: str, remote_ip: str) -> list:
    """The iptables command that adds ('-I') or deletes ('-D') the NAT rule for our tunnel."""
    rule = ["-m", "comment", "--comment", tun_name, "-s", remote_ip, "-j", "MASQUERADE"]
    return ["sudo", "iptables", "-t", "nat", action, "POSTROUTING", *rule]


def setup_tun(tun_name: str) -> int:
    TUNSETIFF = 0x400454CA
    IFF_TUN = 0x0001

    tun_fd = os.open("/dev/net/tun", os.O_RDWR)

    flags = IFF_TUN
    ifr = struct.pack("16sH22s", tun_name.encode(), flags, b"\x00" * 22)
    fcntl.ioctl(tun_fd, TUNSETIFF, ifr)

    return tun_fd


def tun_reader(tun_fd: int, stop_fd: int) -> None:
    """Send packets from the device to the client, until stop_fd becomes readable."""
    while True:
        try:
            r, _, _ = select.select([tun_fd, stop_fd], [], [], keepalive_sec)
            if stop_fd in r:
                break
            if tun_fd not in r:
                # Keepalive timeout - send '#'
                sys.stdout.write("#\n")
                sys.stdout.flush()
                continue
            buf = os.read(tun_fd, 1504)  # Virtual GRE header adds 4 bytes
            sys.stdout.write(f"%{b64encode(buf).decode('ascii')}\n")
            sys.stdout.flush()
        except OSError:  # e.g. our terminal has gone
            break


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: ssm-tunnel-agent <local_ip> <remote_ip>", file=sys.stderr)
        sys.exit(1)

    local_ip = sys.argv[1]
    remote_ip = sys.argv[2]

    tun_suffix = ".".join(local_ip.split(".")[2:])
    tun_name = f"tunSSM.{tun_suffix}"

    for signum in (signal.SIGTERM, signal.SIGHUP):  # e.g. pkill or a TerminateSession: clean up in 'finally'
        signal.signal(signum, lambda signum, _frame: sys.exit(128 + signum))

    create_tun(tun_name, local_ip, remote_ip)

    tun_fd = setup_tun(tun_name)
    print(f"# Agent device {tun_name} is ready [{sys.argv[1]}] version {AGENT_VERSION}")

    stop_fd, stop_reader_fd = os.pipe()
    t = threading.Thread(target=tun_reader, args=(tun_fd, stop_fd))
    t.daemon = True
    t.start()

    try:
        last_ts = time.time()
        stdin_fd = sys.stdin.fileno()  # Should be '0', but still...
        while True:
            r, _, _ = select.select([stdin_fd], [], [], 1)  # Wait 1 sec for input
            if stdin_fd not in r:
                if last_ts + timeout_sec < time.time():
                    print(f"# ERROR: {timeout_sec} sec timeout, exiting...")
                    break
                continue
            line = sys.stdin.readline()
            if not line:  # The session has closed
                break
            last_ts = time.time()
            if line[0] == "%":
                buf = b64decode(line[1:].strip("\n\r"))
                os.write(tun_fd, buf)

    except KeyboardInterrupt:
        pass

    finally:
        ignore_signals()
        # Until its select() returns the device stays attached, and 'ip tuntap del' fails as busy
        os.close(stop_reader_fd)
        t.join(5)
        os.close(tun_fd)
        delete_tun(tun_name, local_ip, remote_ip)


if __name__ == "__main__":
    main()
