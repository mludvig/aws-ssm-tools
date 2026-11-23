from typing import Any

import pytest
from botocore.exceptions import UnStubbedResponseError

from ssm_tools.resolver import ContainerResolver, InstanceResolver

ACCOUNT = "123456789012"
REGION = "ap-southeast-2"


def inventory(*instances: dict[str, str]) -> dict[str, Any]:
    """SSM GetInventory response with one AWS:InstanceInformation entity per instance."""
    return {
        "Entities": [
            {
                "Id": instance["InstanceId"],
                "Data": {
                    "AWS:InstanceInformation": {
                        "TypeName": "AWS:InstanceInformation",
                        "SchemaVersion": "1.0",
                        "Content": [instance],
                    },
                },
            }
            for instance in instances
        ],
    }


def reservations(*instances: dict[str, Any]) -> dict[str, Any]:
    """EC2 DescribeInstances response with all instances in one reservation."""
    return {
        "Reservations": [
            {"Instances": [{"Placement": {"AvailabilityZone": f"{REGION}a"}, **instance} for instance in instances]},
        ],
    }


def ec2(instance_id: str, ip: str, name: str) -> dict[str, Any]:
    return {"InstanceId": instance_id, "PrivateIpAddress": ip, "Tags": [{"Key": "Name", "Value": name}]}


@pytest.fixture
def instance_resolver(cli_args: Any) -> InstanceResolver:
    return InstanceResolver(cli_args())


@pytest.mark.parametrize("target", ["i-0123456789abcdef0", "mi-0123456789abcdef0"])
def test_resolve_instance_id_without_api_calls(instance_resolver: InstanceResolver, stub: Any, target: str) -> None:
    stub(instance_resolver.ssm_client)
    stub(instance_resolver.ec2_client)
    assert instance_resolver.resolve_instance(target) == (target, {})


@pytest.mark.parametrize("target", ["web", "WEB", "ip-10-0-0-1", "10.0.0.1"])
def test_resolve_instance_by_name_hostname_or_ip(instance_resolver: InstanceResolver, stub: Any, target: str) -> None:
    stub(instance_resolver.ssm_client).add_response(
        "get_inventory",
        inventory(
            {"InstanceId": "i-1", "ComputerName": "ip-10-0-0-1", "IpAddress": "10.0.0.1"},
            {"InstanceId": "i-2", "ComputerName": "ip-10-0-0-2", "IpAddress": "10.0.0.2"},
        ),
    )
    stub(instance_resolver.ec2_client).add_response(
        "describe_instances",
        reservations(ec2("i-1", "10.0.0.1", "web"), ec2("i-2", "10.0.0.2", "db")),
        {"InstanceIds": ["i-1", "i-2"]},
    )
    instance_id, details = instance_resolver.resolve_instance(target)
    assert instance_id == "i-1"
    assert details["InstanceName"] == "web"


def test_resolve_ambiguous_name_exits(instance_resolver: InstanceResolver, stub: Any) -> None:
    stub(instance_resolver.ssm_client).add_response(
        "get_inventory",
        inventory({"InstanceId": "i-1"}, {"InstanceId": "i-2"}),
    )
    stub(instance_resolver.ec2_client).add_response(
        "describe_instances",
        reservations(ec2("i-1", "10.0.0.1", "dup"), ec2("i-2", "10.0.0.2", "dup")),
    )
    with pytest.raises(SystemExit) as exc:
        instance_resolver.resolve_instance("dup")
    assert exc.value.code == 1


@pytest.mark.xfail(
    strict=True,
    raises=UnStubbedResponseError,
    reason="C10: DescribeInstances(InstanceIds=[]) lists the whole region",
)
def test_no_ec2_lookup_for_managed_instances_only(instance_resolver: InstanceResolver, stub: Any) -> None:
    stub(instance_resolver.ssm_client).add_response(
        "get_inventory",
        inventory({"InstanceId": "mi-0123456789abcdef0", "ComputerName": "onprem", "IpAddress": "192.168.1.1"}),
    )
    stub(instance_resolver.ec2_client)  # No DescribeInstances call expected
    assert list(instance_resolver.get_list()) == ["mi-0123456789abcdef0"]


def ecs_task(task_id: str, container: dict[str, Any]) -> dict[str, Any]:
    task_arn = f"arn:aws:ecs:{REGION}:{ACCOUNT}:task/lab/{task_id}"
    return {
        "taskArn": task_arn,
        "clusterArn": f"arn:aws:ecs:{REGION}:{ACCOUNT}:cluster/lab",
        "group": "service:web",
        "containers": [
            {
                "taskArn": task_arn,
                "managedAgents": [{"name": "ExecuteCommandAgent", "lastStatus": "RUNNING"}],
                **container,
            },
        ],
    }


@pytest.fixture
def ecs_stub(cli_args: Any, stub: Any) -> Any:
    """Return (resolver, add_tasks) where add_tasks() queues a single-cluster listing with the given tasks."""
    resolver = ContainerResolver(cli_args())
    stubber = stub(resolver.ecs_client)

    def add_tasks(*tasks: dict[str, Any]) -> None:
        stubber.add_response("list_clusters", {"clusterArns": [tasks[0]["clusterArn"]]})
        stubber.add_response("list_tasks", {"taskArns": [task["taskArn"] for task in tasks]})
        stubber.add_response("describe_tasks", {"tasks": list(tasks)})
        return stubber

    return resolver, add_tasks


def test_resolve_container_by_keywords(ecs_stub: Any) -> None:
    resolver, add_tasks = ecs_stub
    add_tasks(
        ecs_task("aaa", {"name": "nginx", "networkInterfaces": [{"privateIpv4Address": "10.0.1.1"}]}),
        ecs_task("bbb", {"name": "nginx", "networkInterfaces": [{"privateIpv4Address": "10.0.1.2"}]}),
    )
    container = resolver.resolve_container(["nginx", "10.0.1.2"])
    assert container["task_id"] == "bbb"


HOST_ARN = f"arn:aws:ecs:{REGION}:{ACCOUNT}:container-instance/lab/0123"


def test_bridge_mode_container_shows_its_host(ecs_stub: Any) -> None:
    resolver, add_tasks = ecs_stub
    bridge = {**ecs_task("ccc", {"name": "nginx", "networkInterfaces": []}), "containerInstanceArn": HOST_ARN}
    add_tasks(bridge).add_response(
        "describe_container_instances",
        {"containerInstances": [{"containerInstanceArn": HOST_ARN, "ec2InstanceId": "i-0123456789abcdef0"}]},
        {"cluster": bridge["clusterArn"], "containerInstances": [HOST_ARN]},
    )
    assert resolver.resolve_container(["host:i-0123456789abcdef0"])["task_id"] == "ccc"


def test_container_addresses(ecs_stub: Any) -> None:
    resolver, add_tasks = ecs_stub
    add_tasks(  # no describe_container_instances: both have an ENI
        {
            **ecs_task("aaa", {"name": "a", "networkInterfaces": [{"privateIpv4Address": "10.0.1.1"}]}),
            "containerInstanceArn": HOST_ARN,
        },
        ecs_task("bbb", {"name": "b", "networkInterfaces": [{"ipv6Address": "2001:db8::1"}]}),
    )
    assert [c["address"] for c in resolver.get_list()] == ["10.0.1.1", "2001:db8::1"]


@pytest.mark.parametrize(("information", "platform"), [([{"PlatformType": "Windows"}], "Windows"), ([], "")])
def test_platform_type(instance_resolver: InstanceResolver, stub: Any, information: list, platform: str) -> None:
    stub(instance_resolver.ssm_client).add_response(
        "describe_instance_information",
        {"InstanceInformationList": information},
        {"Filters": [{"Key": "InstanceIds", "Values": ["i-0123456789abcdef0"]}]},
    )
    assert instance_resolver.platform_type("i-0123456789abcdef0") == platform
