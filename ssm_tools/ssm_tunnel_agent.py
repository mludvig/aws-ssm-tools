#!/usr/bin/env python3

# ssm-tunnel-agent - remote agent for ssm-tunnel
#                    this should be installed on the EC2 instance
#                    and available in the $PATH (e.g. in /usr/local/bin)
#
# Author: Michael Ludvig

# Updated for Python 3.8+ compatibility as part of aws-ssm-tools modernization

import errno
import fcntl
import os
import select
import struct
import sys
import threading
import time
from base64 import b64decode, b64encode

timeout_sec = 60  # Exit and cleanup if we don't get any input
keepalive_sec = 10  # Send a dummy message this often


def run_command(command: str, assert_0: bool = True) -> None:
    print(f"# {command}")
    ret = os.system(command)
    if assert_0:
        assert ret == 0


def create_tun(tun_name: str, local_ip: str, remote_ip: str) -> None:
    params = {
        "tun_name": tun_name,
        "local_ip": local_ip,
        "remote_ip": remote_ip,
        "user_id": os.getuid(),
    }
    try:
        run_command("sudo ip tuntap add {tun_name} mode tun user {user_id}".format(**params))
        run_command("sudo ip addr add {local_ip} peer {remote_ip} dev {tun_name}".format(**params))
        run_command("sudo ip link set {tun_name} up".format(**params))
        # Enable forwarding
        run_command("sudo sysctl -q -w net.ipv4.ip_forward=1".format(**params), assert_0=False)
        run_command(
            'sudo iptables -t nat -I POSTROUTING -m comment --comment "{tun_name}" -s {remote_ip} -j MASQUERADE'.format(
                **params,
            ),
            assert_0=False,
        )
    except AssertionError:
        delete_tun(tun_name, local_ip, remote_ip)
        quit(1)
    except Exception:
        delete_tun(tun_name, local_ip, remote_ip)
        raise


def delete_tun(tun_name: str, local_ip: str, remote_ip: str) -> None:
    params = {
        "tun_name": tun_name,
        "local_ip": local_ip,
        "remote_ip": remote_ip,
    }
    # We don't check return code here - best effort to delete the devices
    run_command("sudo ip link set {tun_name} down".format(**params), assert_0=False)
    run_command("sudo ip tuntap del {tun_name} mode tun".format(**params), assert_0=False)
    run_command(
        'sudo iptables -t nat -D POSTROUTING -m comment --comment "{tun_name}" -s {remote_ip} -j MASQUERADE'.format(
            **params,
        ),
        assert_0=False,
    )


def setup_tun(tun_name: str) -> int:
    TUNSETIFF = 0x400454CA
    IFF_TUN = 0x0001

    tun_fd = os.open("/dev/net/tun", os.O_RDWR)

    flags = IFF_TUN
    ifr = struct.pack("16sH22s", tun_name.encode(), flags, b"\x00" * 22)
    fcntl.ioctl(tun_fd, TUNSETIFF, ifr)

    return tun_fd


def tun_reader(tun_fd: int) -> None:
    while True:
        try:
            r, _, _ = select.select([tun_fd], [], [], keepalive_sec)
            if tun_fd not in r:
                # Keepalive timeout - send '#'
                sys.stdout.write("#\n")
                sys.stdout.flush()
                continue
            buf = os.read(tun_fd, 1504)  # Virtual GRE header adds 4 bytes
            sys.stdout.write(f"%{b64encode(buf).decode('ascii')}\n")
            sys.stdout.flush()
        except OSError as e:
            if e.errno == errno.EBADF:
                # Closed FD during exit
                break


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: ssm-tunnel-agent <local_ip> <remote_ip>", file=sys.stderr)
        sys.exit(1)

    local_ip = sys.argv[1]
    remote_ip = sys.argv[2]

    tun_suffix = ".".join(local_ip.split(".")[2:])
    tun_name = f"tunSSM.{tun_suffix}"

    create_tun(tun_name, local_ip, remote_ip)

    tun_fd = setup_tun(tun_name)
    print(f"# Agent device {tun_name} is ready [{sys.argv[1]}]")

    t = threading.Thread(target=tun_reader, args=(tun_fd,))
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
            last_ts = time.time()
            if line[0] == "%":
                buf = b64decode(line[1:].strip("\n\r"))
                os.write(tun_fd, buf)

    except KeyboardInterrupt:
        pass

    finally:
        os.close(tun_fd)
        delete_tun(tun_name, local_ip, remote_ip)


if __name__ == "__main__":
    main()
