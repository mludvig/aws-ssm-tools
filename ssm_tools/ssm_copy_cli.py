#!/usr/bin/env python3

# Copy files to/from an EC2 instance over SSM Session channel.
#
# See https://aws.nz/aws-utils/ssm-copy for more info.
#
# Author: Michael Ludvig (https://aws.nz)

import os
import sys
import re
import logging
import time
import hashlib
import base64
import argparse
import pexpect
from enum import Enum
import botocore.exceptions

from .common import *
from .talker import SsmTalker
from .resolver import InstanceResolver

logger_name = "ssm-copy"

class Direction(Enum):
    UNKNOWN = 0
    LOCAL_TO_REMOTE = 1
    REMOTE_TO_LOCAL = 2

def parse_location(location):
    """
    Parse [[user@]host:]file location string.
    """
    m = re.match('(?:(?:(?P<user>[^@]+)@)?(?P<host>[^@:]+):)?(?P<file>[^@:]*)', location)
    if not m:
        return {'user': None, 'host': None, 'file': None}
    return m.groupdict()

def parse_args():
    """
    Parse command line arguments.
    """

    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False)

    group_general = add_general_parameters(parser)

    group_instance = parser.add_argument_group('Source / Dest Selection')
    group_instance.add_argument('--list', '-l', dest='list', action="store_true", help='List instances available for copy.')
    group_instance.add_argument('--recursive', '-r', dest='list', action="store_true", help='Recursive copy. Source file must be a directory.')
    group_instance.add_argument('SOURCE', nargs='+', help='Source file(s) for copy operation. Either source or destination must be remote.')
    group_instance.add_argument('DESTINATION', help='Destination for copy operation. Either source or destination must be remote.')

    parser.description = 'Copy files to/from a given instance over SSM.'
    parser.epilog = f'''
IMPORTANT: instances must be registered in AWS Systems Manager (SSM)
before you can copy files to/from them! Instances not registered in SSM
will not be recognised by {parser.prog} nor show up in --list output.

Visit https://aws.nz/aws-utils/ssm-copy for more info and usage examples.

Author: Michael Ludvig
'''

    # Parse supplied arguments
    args = parser.parse_args()

    # If --version do it now and exit
    if args.show_version:
        show_version(args)

    direction = Direction.UNKNOWN
    local_user_host = "None@None"

    # Source verification
    last_user_host = None
    for source in args.SOURCE:
        loc = parse_location(source)
        user_host = f"{loc['user']}@{loc['host']}"
        if direction == Direction.UNKNOWN:
            if user_host == local_user_host:
                direction = Direction.LOCAL_TO_REMOTE
            else:
                direction = Direction.REMOTE_TO_LOCAL
            last_user_host = user_host
        elif direction == Direction.REMOTE_TO_LOCAL and user_host == last_user_host:
            continue
        elif direction == Direction.LOCAL_TO_REMOTE and user_host == local_user_host:
            continue
        else:
            parser.error(f'{source}: All SOURCE files must be local or remote with the same "[user@]instance:" prefix.')

    # Destination verification
    loc = parse_location(args.DESTINATION)
    user_host = f"{loc['user']}@{loc['host']}"
    if direction == Direction.LOCAL_TO_REMOTE and user_host == local_user_host:
        parser.error('Either SOURCE or DESTINATION must be remote, e.g. [user@]instance:file')
    elif direction == Direction.REMOTE_TO_LOCAL and user_host != local_user_host:
        parser.error('Only one of SOURCE or DESTINATION must be remote, not both.')

    return args, direction

class SsmFileSender(SsmTalker):
    def send_file(self, file_name):
        def _erase_line():
            print('\r\x1B[K', end="")   # Erase line

        file_size = os.stat(file_name).st_size
        base_name = os.path.basename(file_name)
        remote_name = f"{base_name}"

        self._logger.debug(f'Copying local {file_name} to remote {remote_name} ({file_size} bytes)')

        # Calculate SHA1SUM while we go
        sha1 = hashlib.sha1()

        # We can't send binary data with pexpect
        self._child.sendline(f'base64 -d > {remote_name}')
        with open(file_name, 'rb') as f:
            sent_size = 0
            start_timestamp = time.time()
            while True:
                buf = f.read(2048)  # Larger buffers don't make it through
                if not buf:
                    break
                sha1.update(buf)
                self._child.sendline(base64.b64encode(buf).decode('ascii'))
                sent_size += len(buf)
                progress = float(sent_size)*100/file_size
                time_spent = time.time()-start_timestamp
                speed = sent_size/time_spent
                _erase_line()
                print(f'{file_name} - {progress:2.1f}% ({sent_size:6}/{file_size} bytes) {int(speed)} B/s', end="", flush=True)
            _erase_line()
        self._child.sendcontrol('d')
        self.wait_for_prompt()

        self._logger.debug('Verifying SHA1 checksum - this may take a while...')
        self._child.sendline(f'sha1sum -b {remote_name}')
        self.wait_for_prompt()
        sha1_file = self._child.before
        if not sha1_file:
            self.wait_for_prompt()
            sha1_file = self._child.before
        m=re.search('([a-f0-9]{40})', sha1_file)
        if not m:
            self._logger.error(f'{file_name} - Did not get a valid SHA1 sum: {sha1_file}')
            return
        sha1_remote = m.group(0)
        sha1_local = sha1.hexdigest()
        if sha1_remote != sha1_local:
            self._logger.error(f'{file_name} - SHA1 verification failed: {sha1_remote} != {sha1_local}')
            return
        else:
            sp_h, sp_u = bytes_to_human(speed)
            ss_h, ss_u = bytes_to_human(sent_size)
            if ss_u == "B":
                ss_u = " Bytes"
            print(f'{file_name} - {int(ss_h)}{ss_u}, {time_spent:.1f}s, {sp_h:.1f}{sp_u}/s, [SHA1 OK]', flush=True)

def main():
    ## Split command line to main args and optional command to run
    args, direction = parse_args()

    logger = configure_logging(logger_name, args.log_level)

    sender = None

    try:
        instance_id = None
        if args.list:
            # --list
            InstanceResolver(args).print_list()
            quit(0)

        if direction == Direction.LOCAL_TO_REMOTE:
            loc = parse_location(args.DESTINATION)
        else:
            loc = parse_location(args.SOURCE[0])

        instance_id = InstanceResolver(args).resolve_instance(loc['host'])
        if not instance_id:
            logger.warning("Could not resolve Instance ID for '%s'", args.INSTANCE)
            logger.warning("Perhaps the '%s' is not registered in SSM?", args.INSTANCE)
            quit(1)

        if direction == Direction.LOCAL_TO_REMOTE:
            sender = SsmFileSender(instance_id, profile = args.profile, region = args.region, logger_name = logger_name)

            for file_name in args.SOURCE:
                sender.send_file(file_name)
        else:
            logger.error('Copying from remote to local files is not yet implemented')
            quit(1)

    except (botocore.exceptions.BotoCoreError,
            botocore.exceptions.ClientError) as e:
        logger.error(e)
        quit(1)

    finally:
        if sender:
            sender.exit()

if __name__ == "__main__":
    main()
