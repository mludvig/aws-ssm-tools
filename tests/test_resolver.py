import io
import logging
import sys
from typing import Any

import pytest
from wcwidth import wcswidth

from ssm_tools import resolver as resolver_module
from ssm_tools.common import configure_logging
from ssm_tools.resolver import ContainerResolver, InstanceResolver, print_targets

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


HYBRID = "mi-0123456789abcdef0"
DII_PARAMS = {"Filters": [{"Key": "ResourceType", "Values": ["ManagedInstance"]}], "MaxResults": 50}


def hybrid_names(**names: str) -> dict[str, Any]:
    """SSM DescribeInstanceInformation response for hybrid managed instances with these names."""
    return {"InstanceInformationList": [{"InstanceId": i, "Name": name} for i, name in names.items()]}


@pytest.mark.parametrize("target", ["I-0123ABCD", "MI-0123456789ABCDEF0", "i-0123456789abcdef0"])
def test_instance_id_is_lowercased(instance_resolver: InstanceResolver, stub: Any, target: str) -> None:
    stub(instance_resolver.ssm_client)
    stub(instance_resolver.ec2_client)
    assert instance_resolver.resolve_instance(target) == (target.lower(), {})


@pytest.mark.parametrize("target", ["i-0123456", "mi-0123abcd", "i-0123456789abcdef", "i-0123456789abcdefg"])
def test_malformed_instance_id_is_looked_up_by_name(
    instance_resolver: InstanceResolver,
    stub: Any,
    target: str,
) -> None:
    stub(instance_resolver.ssm_client).add_response("get_inventory", inventory({"InstanceId": "i-1"}))
    stub(instance_resolver.ec2_client).add_response("describe_instances", reservations(ec2("i-1", "10.0.0.1", "web")))
    assert instance_resolver.resolve_instance(target) == ("", {})


def test_managed_instances_only(instance_resolver: InstanceResolver, stub: Any) -> None:
    ssm = stub(instance_resolver.ssm_client)
    ssm.add_response(
        "get_inventory",
        inventory({"InstanceId": HYBRID, "ComputerName": "onprem", "IpAddress": "192.168.1.1"}),
    )
    ssm.add_response("describe_instance_information", hybrid_names(**{HYBRID: "tagged"}), DII_PARAMS)
    stub(instance_resolver.ec2_client)  # No DescribeInstances, it would list the whole region
    assert instance_resolver.get_list() == {
        HYBRID: {"InstanceId": HYBRID, "InstanceName": "tagged", "HostName": "onprem", "Addresses": ["192.168.1.1"]},
    }


def test_mixed_instances_over_two_pages(instance_resolver: InstanceResolver, stub: Any) -> None:
    ssm = stub(instance_resolver.ssm_client)
    ssm.add_response(
        "get_inventory",
        inventory({"InstanceId": "i-1"}, {"InstanceId": HYBRID}, {"InstanceId": "i-2"}),
    )
    ssm.add_response("describe_instance_information", hybrid_names(**{HYBRID: "onprem"}), DII_PARAMS)
    ec2_stub = stub(instance_resolver.ec2_client)
    ec2_stub.add_response(
        "describe_instances",
        {**reservations(ec2("i-1", "10.0.0.1", "web")), "NextToken": "page2"},
        {"InstanceIds": ["i-1", "i-2"]},
    )
    ec2_stub.add_response(
        "describe_instances",
        reservations(ec2("i-2", "10.0.0.2", "db")),
        {"InstanceIds": ["i-1", "i-2"], "NextToken": "page2"},
    )
    items = instance_resolver.get_list()
    assert {i: item["InstanceName"] for i, item in items.items()} == {"i-1": "web", HYBRID: "onprem", "i-2": "db"}


def test_managed_instance_names_denied(
    instance_resolver: InstanceResolver,
    stub: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ssm = stub(instance_resolver.ssm_client)
    ssm.add_response("get_inventory", inventory({"InstanceId": HYBRID, "ComputerName": "onprem"}))
    ssm.add_client_error("describe_instance_information", "AccessDeniedException")
    stub(instance_resolver.ec2_client)
    assert instance_resolver.get_list()[HYBRID]["InstanceName"] == ""
    assert "Unable to list managed instance names" in caplog.text


def test_resolve_managed_instance_without_ip(instance_resolver: InstanceResolver, stub: Any) -> None:
    ssm = stub(instance_resolver.ssm_client)
    ssm.add_response("get_inventory", inventory({"InstanceId": HYBRID, "ComputerName": "onprem"}))
    ssm.add_response("describe_instance_information", hybrid_names(), DII_PARAMS)
    stub(instance_resolver.ec2_client)
    instance_id, details = instance_resolver.resolve_instance("onprem")
    assert instance_id == HYBRID
    assert details["Addresses"] == []


def item(instance_id: str, name: str = "", host: str = "", ip: str = "") -> dict[str, Any]:
    return {"InstanceId": instance_id, "InstanceName": name, "HostName": host, "Addresses": [ip] if ip else []}


@pytest.mark.parametrize(
    ("items", "target", "expected", "message"),
    [
        # A hybrid node claims an EC2 instance's Name as its host name: the Name wins
        ([item("i-1", name="web"), item(HYBRID, host="web")], "web", "i-1", f"Ignoring {HYBRID}, whose host name"),
        ([item("i-1", name="web"), item("i-2", ip="web")], "WEB", "i-1", "Ignoring i-2, whose host name or IP"),
        # Its own host name equal to its Name is no collision
        ([item("i-1", name="web", host="web")], "web", "i-1", ""),
        # Only a self-reported field matched: say which, louder for hybrid nodes
        ([item(HYBRID, name="onprem", host="stopped")], "stopped", HYBRID, f"is the host name that {HYBRID} reports"),
        ([item(HYBRID, ip="10.0.0.9")], "10.0.0.9", HYBRID, f"is the IP address that {HYBRID} reports"),
        ([item("i-1", host="ip-10-0-0-1")], "ip-10-0-0-1", "i-1", "is the host name of i-1"),
    ],
)
def test_name_beats_self_reported_fields(
    instance_resolver: InstanceResolver,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    items: list[dict[str, Any]],
    target: str,
    expected: str,
    message: str,
) -> None:
    caplog.set_level("INFO")
    monkeypatch.setattr(instance_resolver, "get_list", lambda: {i["InstanceId"]: i for i in items})
    assert instance_resolver.resolve_instance(target)[0] == expected
    assert message in caplog.text
    if not message:
        assert not caplog.records


@pytest.mark.parametrize(
    "items",
    [
        [item("i-1", name="web"), item(HYBRID, name="web")],
        [item("i-1", host="web"), item(HYBRID, ip="web")],
    ],
)
def test_ambiguous_within_a_tier_exits(
    instance_resolver: InstanceResolver,
    monkeypatch: pytest.MonkeyPatch,
    items: list[dict[str, Any]],
) -> None:
    monkeypatch.setattr(instance_resolver, "get_list", lambda: {i["InstanceId"]: i for i in items})
    with pytest.raises(SystemExit) as exc:
        instance_resolver.resolve_instance("web")
    assert exc.value.code == 1


def test_ambiguous_without_terminal_lists_the_candidates(
    instance_resolver: InstanceResolver,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture,
) -> None:
    items = [item("i-2", name="web"), item("i-1", name="web"), item("i-3", name="db")]
    monkeypatch.setattr(instance_resolver, "get_list", lambda: {i["InstanceId"]: i for i in items})
    with pytest.raises(SystemExit) as exc:
        instance_resolver.resolve_instance("web")
    assert exc.value.code == 1
    assert "Found 2 matches for 'web'" in caplog.text
    out, err = capsys.readouterr()
    assert out == ""
    assert [line.split()[0] for line in err.splitlines()[2:]] == ["i-1", "i-2"]


def test_ambiguous_with_terminal_picks_from_the_candidates(
    instance_resolver: InstanceResolver,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    items = [item("i-2", name="web"), item("i-1", name="web"), item("i-3", name="db")]
    monkeypatch.setattr(instance_resolver, "get_list", lambda: {i["InstanceId"]: i for i in items})
    monkeypatch.setattr(resolver_module, "has_terminal", lambda: True)
    offered: list[str] = []

    def pick(_headers: str, targets: list[dict[str, Any]]) -> dict[str, Any]:
        offered.extend(target["InstanceId"] for target in targets)
        return targets[1]

    monkeypatch.setattr(resolver_module, "target_selector", pick)
    assert instance_resolver.resolve_instance("web") == ("i-2", items[0])
    assert offered == ["i-1", "i-2"]
    assert capsys.readouterr().err == ""  # The picker shows them


def test_matched_instance_goes_to_stderr_unless_quiet(
    instance_resolver: InstanceResolver,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setattr(instance_resolver, "get_list", lambda: {"i-1": item("i-1", name="web", ip="10.0.0.1")})
    caplog.set_level(logging.INFO, logger="ssm-tools")
    instance_resolver.resolve_instance("web")
    out, err = capsys.readouterr()
    assert out == ""
    assert err.splitlines()[2].split() == ["i-1", "web", "10.0.0.1"]
    caplog.set_level(logging.WARNING, logger="ssm-tools")
    instance_resolver.resolve_instance("web")
    assert capsys.readouterr() == ("", "")


ROWS = [
    {"Id": "i-1", "Name": "名前🚀", "Ip": ["10.0.0.1", "10.0.0.2"], "Zone": "a", "Arn": "arn:1"},
    {"Id": "i-2", "Name": "evil\x1b[2J|x", "Ip": [], "Zone": "b", "Arn": "arn:2"},
]


def test_print_targets(capsys: pytest.CaptureFixture) -> None:
    headers, targets = print_targets(ROWS, hidden=("Arn",))
    lines = capsys.readouterr().out.splitlines()
    # Wide characters keep the columns aligned: the last one starts at the same display column
    assert len({wcswidth(line[: line.rindex(line.split()[-1])]) for line in lines}) == 1
    assert lines[2].split() == ["i-1", "名前🚀", "10.0.0.1,", "10.0.0.2", "a"]
    assert lines[3].split() == ["i-2", "evil?[2J|x", "b"]
    assert headers == "\n".join(f"  {line}" for line in lines[:2])
    assert [target["summary"] for target in targets] == lines[2:]
    assert targets[1]["Arn"] == "arn:2"  # Hidden from the table, not from the targets


def test_print_targets_to_an_ascii_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    monkeypatch.setattr(sys, "stdout", stdout)
    configure_logging(logging.INFO)
    print_targets(ROWS)
    stdout.flush()
    lines = stdout.buffer.getvalue().decode().splitlines()  # type: ignore[attr-defined]
    assert lines[2].split()[:2] == ["i-1", "???"]


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
        ecs_task(
            "bbb",
            {"name": "nginx", "runtimeId": "bbb-1", "networkInterfaces": [{"privateIpv4Address": "10.0.1.2"}]},
        ),
    )
    container = resolver.resolve_container(["nginx", "10.0.1.2"])
    assert container["task_id"] == "bbb"
    assert container["runtime_id"] == "bbb-1"  # For the plugin's target


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


@pytest.mark.parametrize("terminal", [True, False])
def test_ambiguous_container(
    ecs_stub: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture,
    terminal: bool,
) -> None:
    resolver, add_tasks = ecs_stub
    add_tasks(
        ecs_task("bbb", {"name": "nginx", "networkInterfaces": [{"privateIpv4Address": "10.0.1.2"}]}),
        ecs_task("aaa", {"name": "nginx", "networkInterfaces": [{"privateIpv4Address": "10.0.1.1"}]}),
    )
    monkeypatch.setattr(resolver_module, "has_terminal", lambda: terminal)
    monkeypatch.setattr(resolver_module, "target_selector", lambda _headers, targets: targets[1])
    if terminal:
        container = resolver.resolve_container(["nginx", "service:web"])
        assert container["task_arn"].endswith("/aaa")  # The whole container, hidden columns too
        return
    with pytest.raises(SystemExit) as exc:
        resolver.resolve_container(["nginx", "service:web"])
    assert exc.value.code == 1
    assert "Found 2 matches for 'nginx' AND 'service:web'" in caplog.text
    err = capsys.readouterr().err
    assert "aaa" in err and "bbb" in err and "arn:" not in err


@pytest.mark.parametrize(
    ("keywords", "task_ids"),
    [
        (["service:web"], ["aaa"]),
        (["web"], ["aaa"]),  # The service without "service:"
        (["batch"], ["bbb"]),  # A RunTask family without "family:"
        (["NGINX", "Service:Web"], ["aaa"]),  # Any case
        (["BBB"], ["bbb"]),
        (["worker", "10.0.1.2"], ["bbb"]),
        (["web", "worker"], []),
        ([""], []),  # Not an empty field
    ],
)
def test_container_keywords(ecs_stub: Any, keywords: list[str], task_ids: list[str]) -> None:
    resolver, add_tasks = ecs_stub
    add_tasks(
        ecs_task("aaa", {"name": "nginx", "networkInterfaces": [{"privateIpv4Address": "10.0.1.1"}]}),
        {
            **ecs_task("bbb", {"name": "Worker", "networkInterfaces": [{"privateIpv4Address": "10.0.1.2"}]}),
            "group": "family:batch",
        },
        {**ecs_task("ccc", {"name": "nginx", "networkInterfaces": []}), "group": ""},
    )
    if not task_ids:
        with pytest.raises(SystemExit):
            resolver.resolve_container(keywords)
        return
    assert [resolver.resolve_container(keywords)["task_id"]] == task_ids


def test_unknown_cluster(cli_args: Any, stub: Any, caplog: pytest.LogCaptureFixture) -> None:
    resolver = ContainerResolver(cli_args(cluster="nosuch"))
    stub(resolver.ecs_client).add_response(
        "list_clusters",
        {"clusterArns": [f"arn:aws:ecs:{REGION}:{ACCOUNT}:cluster/lab"]},
    )
    with pytest.raises(SystemExit) as exc:
        resolver.get_list()
    assert exc.value.code == 1
    assert "ECS cluster nosuch not found." in caplog.text


def skipped_tasks() -> list[dict[str, Any]]:
    """Tasks with containers that ECS Exec can't reach."""
    no_exec = ecs_task("aaa", {"name": "db"})
    del no_exec["containers"][0]["managedAgents"]
    pending = {**ecs_task("bbb", {"name": "db"}), "enableExecuteCommand": True}
    pending["containers"][0]["managedAgents"][0]["lastStatus"] = "PENDING"
    no_agent = {**ecs_task("ccc", {"name": "app", "managedAgents": []}), "enableExecuteCommand": True}
    return [no_exec, pending, no_agent]


def test_skipped_containers_that_match(ecs_stub: Any, caplog: pytest.LogCaptureFixture) -> None:
    resolver, add_tasks = ecs_stub
    add_tasks(*skipped_tasks(), ecs_task("ddd", {"name": "web"}))
    with pytest.raises(SystemExit) as exc:
        resolver.resolve_container(["db"])
    assert exc.value.code == 1
    assert "No container matches: db" in caplog.text
    assert "Container db in task aaa matches, but ECS Exec isn't enabled on its task." in caplog.text
    assert "Container db in task bbb matches, but its ExecuteCommandAgent is PENDING." in caplog.text
    assert "task ccc" not in caplog.text and "task ddd" not in caplog.text


def test_skipped_containers_summary(ecs_stub: Any, caplog: pytest.LogCaptureFixture) -> None:
    resolver, add_tasks = ecs_stub
    add_tasks(*skipped_tasks(), skipped_tasks()[0])
    with pytest.raises(SystemExit) as exc:
        resolver.print_list()
    assert exc.value.code == 1
    assert "No Execute-Command capable containers found!" in caplog.text
    assert "Skipped 2 container(s): ECS Exec isn't enabled on its task." in caplog.text
    assert "Skipped 1 container(s): its ExecuteCommandAgent is PENDING." in caplog.text
    assert "Skipped 1 container(s): it has no ExecuteCommandAgent." in caplog.text
