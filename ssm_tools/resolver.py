#!/usr/bin/env python3

import os
import re
import logging
import botocore.credentials
import botocore.session
import boto3

logger = logging.getLogger("ssm-session")

class InstanceResolver():
    def __init__(self, args):
        # aws-cli compatible MFA cache
        cli_cache = os.path.join(os.path.expanduser('~'),'.aws/cli/cache')

        # Construct boto3 session with MFA cache
        session = boto3.session.Session(profile_name=args.profile, region_name=args.region)
        session._session.get_component('credential_provider').get_provider('assume-role').cache = botocore.credentials.JSONFileCache(cli_cache)

        # Create boto3 clients from session
        self.ssm_client = session.client('ssm')
        self.ec2_client = session.client('ec2')

    def get_list(self):
        def _try_append(_list, _dict, _key):
            if _key in _dict:
                _list.append(_dict[_key])

        items = {}

        # List instances from SSM
        logger.debug("Fetching SSM inventory")
        paginator = self.ssm_client.get_paginator('get_inventory')
        response_iterator = paginator.paginate()
        for inventory in response_iterator:
            for entity in inventory["Entities"]:
                try:
                    content = entity['Data']['AWS:InstanceInformation']["Content"][0]

                    # At the moment we only support EC2 Instances and ManagedInstances
                    if content["ResourceType"] not in [ "EC2Instance", "ManagedInstance" ]:
                        logger.warning("Unknown instance type: %s: %s", entity['Id'], content['ResourceType'])
                        logger.debug(entity)
                        continue

                    # Ignore Terminated instances
                    if content.get("InstanceStatus") == "Terminated":
                        logger.debug("Ignoring terminated instance: %s", entity)
                        continue

                    # Add to the list
                    instance_id = content['InstanceId']
                    items[instance_id] = {
                        "InstanceId": instance_id,
                        "InstanceName": "",
                        "HostName": content.get("ComputerName", ""),
                        "Addresses": [ content.get("IpAddress") ],
                    }
                    logger.debug("Added instance: %s: %r", instance_id, items[instance_id])
                except (KeyError, ValueError):
                    logger.warning("SSM inventory entity not recognised: %s", entity)
                    continue

        # Add attributes from EC2
        paginator = self.ec2_client.get_paginator('describe_instances')
        ec2_instance_ids = list(filter(lambda x: x.startswith("i-"), items))

        tries = 5
        while tries:
            # The SSM inventory sometimes returns instances that have been terminated
            # a short while ago which makes the following call fail
            # with InvalidInstanceID.NotFound exception. We'll try and remove the invalid
            # instance ids a {tries} times or until we succeed. If unsuccessful we'll remove
            # the list obtained from SSM without extra details (host name, public IPs, etc).
            # This mostly / only affects accounts with high churn of starting / stopping
            # instances - most users will pass this loop only once.
            try:
                response_iterator = paginator.paginate(InstanceIds=ec2_instance_ids)
                for reservations in response_iterator:
                    for reservation in reservations['Reservations']:
                        for instance in reservation['Instances']:
                            instance_id = instance['InstanceId']
                            if not instance_id in items:
                                continue

                            # Find instance IPs
                            items[instance_id]['Addresses'] = []
                            _try_append(items[instance_id]['Addresses'], instance, 'PrivateIpAddress')
                            _try_append(items[instance_id]['Addresses'], instance, 'PublicIpAddress')

                            # Find instance name from tag Name
                            for tag in instance['Tags']:
                                if tag['Key'] == 'Name' and tag['Value']:
                                    items[instance_id]['InstanceName'] = tag['Value']

                            logger.debug("Updated instance: %s: %r", instance_id, items[instance_id])
                    return items

            except botocore.exceptions.ClientError as ex:
                if ex.response.get('Error', {}).get('Code', '') != 'InvalidInstanceID.NotFound':
                    raise
                message = ex.response.get('Error', {}).get('Message', '')
                if not message.startswith("The instance ID") or not message.endswith("not exist"):
                    logger.warning("Unexpected InvalidInstanceID.NotFound message:", message)
                # Try to extract instace ids ...
                remove_instance_ids = re.findall('i-[0-9a-f]+', message)
                logger.debug("Removing non-existent InstanceIds: %s", remove_instance_ids)
                # Remove the failed ids from the list and try again
                ec2_instance_ids = list(set(ec2_instance_ids) - set(remove_instance_ids))
                tries -= 1

        if not tries:
            logger.warning("Unable to list instance details. Some instance names and IPs may be missing.")

        return items

    def print_list(self):
        hostname_len = 1    # Minimum of 1 char, otherwise f-string below fails for empty hostnames
        instname_len = 1

        items = self.get_list().values()

        if not items:
            logger.warning("No instances registered in SSM!")
            return

        items = list(items)
        items.sort(key=lambda x: x.get('InstanceName') or x.get('HostName'))

        for item in items:
            hostname_len = max(hostname_len, len(item['HostName']))
            instname_len = max(instname_len, len(item['InstanceName']))

        for item in items:
            print(f"{item['InstanceId']:20}   {item['HostName']:{hostname_len}}   {item['InstanceName']:{instname_len}}   {' '.join(item['Addresses'])}")

    def resolve_instance(self, instance):
        # Is it a valid Instance ID?
        if re.match('^i-[a-f0-9]+$', instance):
            return instance

        # It is not - find it in the list
        instances = []

        items = self.get_list()
        for instance_id in items:
            item = items[instance_id]
            if instance.lower() in [item['HostName'].lower(), item['InstanceName'].lower()] + item['Addresses']:
                instances.append(instance_id)

        if not instances:
            return None

        if len(instances) > 1:
            logger.warning("Found %d instances for '%s': %s", len(instances), instance, " ".join(instances))
            logger.warning("Use INSTANCE_ID to connect to a specific one")
            quit(1)

        # Found only one instance - return it
        return instances[0]

