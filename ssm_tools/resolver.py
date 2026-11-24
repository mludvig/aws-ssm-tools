#!/usr/bin/env python3

import argparse
import logging
import re
import sys
from collections import Counter
from typing import Any, NoReturn, TextIO

from botocore.exceptions import ClientError
from tabulate import tabulate

from .common import AWSSessionBase, printable
from .picker import has_terminal, target_selector

logger = logging.getLogger("ssm-tools.resolver")


def instance_not_found(instance: str) -> NoReturn:
    """--list shows only the instances that SSM has online, so that's where to look."""
    logger.error(
        "No online SSM instance matches '%s'. It may be stopped, offline or not registered in SSM, see --list.",
        instance,
    )
    sys.exit(1)


# EC2 instance ID (old short or long form) or hybrid managed instance ID
INSTANCE_ID_RE = re.compile(r"i-[0-9a-f]{8}|m?i-[0-9a-f]{17}", re.IGNORECASE)


def print_targets(
    rows: list[dict[str, Any]],
    quiet: bool = False,
    file: TextIO | None = None,
    hidden: tuple[str, ...] = (),
) -> tuple[str, list[Any]]:
    """
    Print the rows as a table without the 'hidden' columns, unless quiet. Return the menu
    header and the rows, each with its table line as "summary", for target_selector().
    """
    cells = [
        {
            key: printable(", ".join(value) if isinstance(value, list) else str(value))
            for key, value in row.items()
            if key not in hidden
        }
        for row in rows
    ]
    lines = tabulate(cells, headers="keys").split("\n")
    if not quiet:
        print("\n".join(lines), file=file or sys.stdout)

    menu_header = "\n".join(f"  {line}" for line in lines[:2])
    return menu_header, [{"summary": line, **row} for row, line in zip(rows, lines[2:])]


def choose_target(matches: str, rows: list[dict[str, Any]], hint: str, hidden: tuple[str, ...] = ()) -> dict[str, Any]:
    """
    'matches' found several rows. Let the user pick one, or list them and exit without a terminal.
    """
    logger.warning("Found %d matches for %s", len(rows), matches)
    headers, targets = print_targets(rows, quiet=has_terminal(), file=sys.stderr, hidden=hidden)
    if has_terminal():
        return target_selector(headers, targets)
    logger.warning(hint)
    sys.exit(1)


class InstanceResolver(AWSSessionBase):
    HIDDEN = ("AvailabilityZone",)  # Not shown in tables

    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__(args)

        # Create boto3 clients from session
        self.ssm_client = self.session.client("ssm")
        self.ec2_client = self.session.client("ec2")

    def get_list(self) -> dict[str, dict[str, Any]]:
        def _try_append(_list: list, _dict: dict, _key: str) -> None:
            if _key in _dict:
                _list.append(_dict[_key])

        items = {}

        # List instances from SSM
        logger.debug("Fetching SSM inventory")
        paginator = self.ssm_client.get_paginator("get_inventory")
        response_iterator = paginator.paginate(
            Filters=[
                {
                    "Key": "AWS:InstanceInformation.ResourceType",
                    "Values": ["EC2Instance", "ManagedInstance"],
                    "Type": "Equal",
                },
                {
                    "Key": "AWS:InstanceInformation.InstanceStatus",
                    "Values": ["Terminated", "Stopped", "ConnectionLost"],
                    "Type": "NotEqual",
                },
            ],
        )

        for inventory in response_iterator:
            for entity in inventory["Entities"]:
                logger.debug(entity)
                content = entity["Data"]["AWS:InstanceInformation"]["Content"][0]
                instance_id = content["InstanceId"]
                items[instance_id] = {
                    "InstanceId": instance_id,
                    "InstanceName": "",
                    "HostName": content.get("ComputerName", ""),
                    "Addresses": [content["IpAddress"]] if content.get("IpAddress") else [],
                }
                logger.debug("Added instance: %s: %r", instance_id, items[instance_id])

        # Add names of hybrid managed instances (mi-*): the Name tag if set, else the activation DefaultInstanceName
        if any(instance_id.startswith("mi-") for instance_id in items):
            logger.debug("Fetching SSM managed instance names")
            paginator = self.ssm_client.get_paginator("describe_instance_information")
            try:
                for page in paginator.paginate(
                    Filters=[{"Key": "ResourceType", "Values": ["ManagedInstance"]}],
                    PaginationConfig={"PageSize": 50},
                ):
                    for info in page["InstanceInformationList"]:
                        if info["InstanceId"] in items and info.get("Name"):
                            items[info["InstanceId"]]["InstanceName"] = info["Name"]
            except ClientError as ex:
                logger.warning("Unable to list managed instance names: %s", ex)

        # Add attributes from EC2. An empty InstanceIds list would describe the whole region.
        paginator = self.ec2_client.get_paginator("describe_instances")
        ec2_instance_ids = [instance_id for instance_id in items if instance_id.startswith("i-")]

        tries = 5
        while ec2_instance_ids and tries:
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
                    for reservation in reservations["Reservations"]:
                        for instance in reservation["Instances"]:
                            instance_id = instance["InstanceId"]
                            if instance_id not in items:
                                continue

                            # Find instance IPs
                            items[instance_id]["Addresses"] = []
                            _try_append(items[instance_id]["Addresses"], instance, "PrivateIpAddress")
                            _try_append(items[instance_id]["Addresses"], instance, "PublicIpAddress")

                            # Store instance AZ - useful for EC2 Instance Connect
                            items[instance_id]["AvailabilityZone"] = instance["Placement"]["AvailabilityZone"]

                            # Find instance name from tag Name
                            for tag in instance.get("Tags", []):
                                if tag["Key"] == "Name" and tag["Value"]:
                                    items[instance_id]["InstanceName"] = tag["Value"]

                            logger.debug("Updated instance: %s: %r", instance_id, items[instance_id])
                break

            except ClientError as ex:
                if ex.response.get("Error", {}).get("Code", "") != "InvalidInstanceID.NotFound":
                    raise
                message = ex.response.get("Error", {}).get("Message", "")
                if not message.startswith("The instance ID") or not message.endswith("not exist"):
                    logger.warning("Unexpected InvalidInstanceID.NotFound message: %s", message)
                # Try to extract instace ids ...
                remove_instance_ids = re.findall("i-[0-9a-f]+", message)
                logger.debug("Removing non-existent InstanceIds: %s", remove_instance_ids)
                # Remove the failed ids from the list and try again
                ec2_instance_ids = list(set(ec2_instance_ids) - set(remove_instance_ids))
                tries -= 1

        if not tries:
            logger.warning("Unable to list instance details. Some instance names and IPs may be missing.")

        return items

    def print_list(self, quiet: bool = False) -> tuple[str, list[Any]]:
        items = self.get_list().values()

        if not items:
            logger.warning("No instances registered in SSM!")
            sys.exit(1)

        items_list = list(items)
        del items
        items_list.sort(key=lambda x: x.get("InstanceName") or x.get("HostName"))  # type: ignore

        return print_targets(items_list, quiet=quiet, hidden=self.HIDDEN)

    def platform_type(self, instance_id: str) -> str:
        """Return the SSM PlatformType of the instance, e.g. 'Linux' or 'Windows', or '' if unknown."""
        response = self.ssm_client.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": [instance_id]}],
        )
        return next((info["PlatformType"] for info in response["InstanceInformationList"]), "")

    def resolve_instance(self, instance: str) -> tuple[str, dict[str, Any]]:
        # Is it a valid Instance ID?
        if INSTANCE_ID_RE.fullmatch(instance):
            return instance.lower(), {}

        # It is not - find it in the list. The Name, which the instance can't change, beats
        # the host name and IP, which it can. See docs/adr/0008-name-resolution-trust.md
        needle = instance.lower()
        items = self.get_list()
        by_name = [i for i, item in items.items() if item["InstanceName"].lower() == needle]
        by_host = [i for i, item in items.items() if needle in [item["HostName"].lower(), *item["Addresses"]]]
        instances = by_name or by_host

        if not instances:
            return "", {}

        if len(instances) > 1:
            rows = [items[instance_id] for instance_id in sorted(instances)]
            chosen = choose_target(f"'{instance}'", rows, "Use INSTANCE_ID to connect to a specific one", self.HIDDEN)
            return chosen["InstanceId"], items[chosen["InstanceId"]]

        instance_id = instances[0]
        if by_name:
            ignored = sorted(set(by_host) - {instance_id})
            if ignored:
                logger.warning("Ignoring %s, whose host name or IP is also '%s'", " ".join(ignored), instance)
        else:
            field = "host name" if items[instance_id]["HostName"].lower() == needle else "IP address"
            if instance_id.startswith("mi-"):
                logger.warning("'%s' is the %s that %s reports itself, not its Name", instance, field, instance_id)
            else:
                logger.info("'%s' is the %s of %s", instance, field, instance_id)

        # Show the matched instance we are connecting to
        quiet = not logger.isEnabledFor(logging.INFO)
        print_targets([items[instance_id]], quiet=quiet, file=sys.stderr, hidden=self.HIDDEN)

        # Found only one instance - return it
        return instance_id, items[instance_id]


class ContainerResolver(AWSSessionBase):
    HIDDEN = ("cluster_arn", "task_arn", "runtime_id")  # Not shown in tables

    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__(args)

        # Create boto3 clients from session
        self.ecs_client = self.session.client("ecs")

        self.args = args
        self.containers: list[dict[str, Any]] = []
        self.skipped: list[dict[str, Any]] = []  # Containers without ECS Exec, with a "reason"
        self._tasks: dict[str, Any] = {}
        self._hosts: dict[str, str] = {}  # containerInstanceArn -> "host:<EC2 instance ID>"

    def add_container(self, container: dict[str, Any], skipped_reason: str = "") -> None:
        _task_parsed = container["taskArn"].split(":")[-1].split("/")
        task = self._tasks[container["taskArn"]]
        # Bridge / host network mode tasks have no ENI, show their EC2 host instead
        nics = container.get("networkInterfaces")
        if nics:
            address = nics[0].get("privateIpv4Address") or nics[0].get("ipv6Address", "")
        else:
            address = self._hosts.get(task.get("containerInstanceArn", ""), "")
        row = {
            "cluster_name": _task_parsed[1],
            "task_id": _task_parsed[2],
            "cluster_arn": self._tasks[container["taskArn"]]["clusterArn"],
            "task_arn": container["taskArn"],
            "group_name": self._tasks[container["taskArn"]]["group"],
            "container_name": container["name"],
            "runtime_id": container.get("runtimeId", ""),  # Not there until the container started
            "address": address,
        }
        if skipped_reason:
            self.skipped.append({**row, "reason": skipped_reason})
        else:
            self.containers.append(row)

    def get_list(self) -> list[dict[str, Any]]:
        # List ECS Clusters
        clusters = []
        logger.debug("Listing ECS Clusters")
        paginator = self.ecs_client.get_paginator("list_clusters")
        for page in paginator.paginate():
            clusters.extend(page["clusterArns"])

        if self.args.cluster:
            filtered_clusters = []
            for cluster in clusters:
                if (self.args.cluster.startswith("arn:") and cluster == self.args.cluster) or cluster.endswith(
                    f"/{self.args.cluster}",
                ):
                    filtered_clusters.append(cluster)
                    break
            clusters = filtered_clusters
            if not clusters:
                logger.error("ECS cluster %s not found.", self.args.cluster)
                sys.exit(1)

        if not clusters:
            logger.warning("No ECS Clusters found.")
            return []

        # List tasks in each cluster
        paginator = self.ecs_client.get_paginator("list_tasks")
        for cluster in clusters:
            logger.debug("Listing tasks in cluster: %s", cluster)

            # maxResults must be <= 100 because describe_tasks() doesn't accept more than that
            for page in paginator.paginate(cluster=cluster, maxResults=100):
                if "taskArns" not in page or not page["taskArns"]:
                    logger.debug(f"No tasks found in cluster {cluster}")
                    break
                response = self.ecs_client.describe_tasks(cluster=cluster, tasks=page["taskArns"])

                host_arns = {
                    task["containerInstanceArn"]
                    for task in response["tasks"]
                    if "containerInstanceArn" in task
                    and not all(c.get("networkInterfaces") for c in task["containers"])
                } - self._hosts.keys()
                if host_arns:
                    hosts = self.ecs_client.describe_container_instances(
                        cluster=cluster,
                        containerInstances=sorted(host_arns),
                    )
                    for host in hosts["containerInstances"]:
                        self._hosts[host["containerInstanceArn"]] = f"host:{host.get('ec2InstanceId', '')}"

                # Keep the containers that have a running ExecuteCommandAgent, and say why we skip the rest
                for task in response["tasks"]:
                    logger.debug(task)
                    self._tasks[task["taskArn"]] = task
                    for container in task["containers"]:
                        self.add_container(container, self._exec_unavailable(task, container))

        return self.containers

    def print_list(self, quiet: bool = False) -> tuple[str, list[Any]]:
        containers = self.get_list()

        if not containers:
            logger.warning("No Execute-Command capable containers found!")
            for reason, count in Counter(container["reason"] for container in self.skipped).items():
                logger.warning("Skipped %d container(s): %s.", count, reason)
            sys.exit(1)

        return print_targets(sorted(containers, key=self._sort_key), quiet=quiet, hidden=self.HIDDEN)

    def resolve_container(self, keywords: list[str]) -> dict[str, Any]:
        containers = self.get_list()
        logger.debug("Searching for containers matching all keywords: %s", " ".join(keywords))
        candidates = [container for container in containers if self._matches(container, keywords)]
        if not candidates:
            logger.warning("No container matches: %s", " AND ".join(keywords))
            for container in self.skipped:
                if self._matches(container, keywords):
                    logger.warning(
                        "Container %s in task %s matches, but %s.",
                        container["container_name"],
                        container["task_id"],
                        container["reason"],
                    )
            sys.exit(1)
        if len(candidates) > 1:
            matches = " AND ".join(f"'{keyword}'" for keyword in keywords)
            hint = "Use Container IP or Task ID to connect to a specific one"
            return choose_target(matches, sorted(candidates, key=self._sort_key), hint, self.HIDDEN)
        return candidates[0]

    @staticmethod
    def _exec_unavailable(task: dict[str, Any], container: dict[str, Any]) -> str:
        """Why we can't ECS Exec into this container, or "" if we can."""
        agent = next((a for a in container.get("managedAgents", []) if a["name"] == "ExecuteCommandAgent"), None)
        if agent and agent["lastStatus"] == "RUNNING":
            return ""
        if not task.get("enableExecuteCommand"):
            return "ECS Exec isn't enabled on its task"
        if not agent:
            return "it has no ExecuteCommandAgent"
        return f"its ExecuteCommandAgent is {agent['lastStatus']}"

    @staticmethod
    def _matches(container: dict[str, Any], keywords: list[str]) -> bool:
        """Case-insensitive, and the group matches with or without its "service:" / "family:" prefix."""
        group = container["group_name"]
        fields = {
            group,
            group.partition(":")[2],
            container["task_id"],
            container["container_name"],
            container["address"],
        }
        fields = {field.lower() for field in fields} - {""}
        return all(keyword.lower() in fields for keyword in keywords)

    @staticmethod
    def _sort_key(container: dict[str, Any]) -> list[str]:
        return [container["cluster_name"], container["container_name"]]
