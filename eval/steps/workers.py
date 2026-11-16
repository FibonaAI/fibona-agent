"""A job's ECS workers: launch them, wait for SSH, and delete them by their fibona-job tag.

Optionally they mount vePFS. The mount service's client-mount nodes install the client on the workers over SSH, join
them to the service's GPFS cluster and mount its file systems at /vepfs (`vepfs add`, about 2 minutes per batch);
`vepfs del` takes them out again, /etc/hosts entries included, before the workers are deleted. We run that CLI on a
client-mount node because the API for it (AddMountServiceClients) rejects custom images. We log in there as root with
the node's default password from DescribeMountServices; the CLI logs in to the workers with the root password job.py
set on them once they accept SSH. We reach the node through one of the job's workers (via: an SSH ProxyCommand), which
sits in the node's VPC: this machine has no route to private IPs outside cn-beijing, and one path for every region
keeps a job's vePFS steps the same everywhere.
"""

import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import volcenginesdkecs as ecs
import volcenginesdkvepfs as vepfs
from plugins.hosts import Resources
from pydantic import BaseModel, ConfigDict, Field, PositiveInt
from volcenginesdkcore.rest import ApiException

TAG = "fibona-job"
REGION = "cn-beijing"  # This machine's: archives and the registry live here; elsewhere workers are reached over EIPs.
# Per region, the directory under /vepfs that MLP dev boxes mount as /home/users; workers bind it there too, so job data
# goes to /home/users/shared on either. It is a bind because /vepfs/perf-test is root 700.
HOMES = {"cn-beijing": "perf-test/home-users", "ap-southeast-1": "home-users"}


class ECSSpec(BaseModel):
    """Identical pay-as-you-go workers from the CPU worker image, each with its own EIP for the proxy."""

    model_config = ConfigDict(extra="forbid")

    count: PositiveInt
    instance_type: str  # e.g. ecs.g3a.16xlarge.
    budget: Resources  # Scheduling budget per worker, after system/sidecar headroom.
    image_id: str
    zone: str
    subnet_id: str
    security_group_ids: list[str] = Field(min_length=1)
    key_pair: str  # Root login; Harbor itself uses the image's harbor-runner key.
    system_gb: PositiveInt = 100
    data_gb: PositiveInt = 1024  # Formatted and mounted at /data on first boot.
    eip_mbps: PositiveInt = 100


def launch(run: str, group: int, spec: ECSSpec) -> list[str]:
    volume = lambda size: ecs.VolumeForRunInstancesInput(  # noqa: E731
        volume_type="ESSD_PL0", size=size, delete_with_instance=True
    )
    request = ecs.RunInstancesRequest(
        zone_id=spec.zone,
        instance_type_id=spec.instance_type,
        image_id=spec.image_id,
        instance_name=f"fibona-{run}-{group}",
        key_pair_name=spec.key_pair,
        instance_charge_type="PostPaid",
        count=spec.count,
        network_interfaces=[
            ecs.NetworkInterfaceForRunInstancesInput(
                subnet_id=spec.subnet_id, security_group_ids=spec.security_group_ids
            )
        ],
        volumes=[volume(spec.system_gb), volume(spec.data_gb)],
        eip_address=ecs.EipAddressForRunInstancesInput(
            charge_type="PayByTraffic", bandwidth_mbps=spec.eip_mbps, release_with_instance=True
        ),
        tags=[ecs.TagForRunInstancesInput(key=TAG, value=run)],
    )
    return ecs.ECSApi().run_instances(request).instance_ids


def describe(**filters) -> list:
    api, instances, token = ecs.ECSApi(), [], None
    while True:
        result = api.describe_instances(ecs.DescribeInstancesRequest(max_results=100, next_token=token, **filters))
        instances += result.instances or []
        token = result.next_token
        if not token:
            return instances


def wait(
    ids: list[str], ssh: list[str], log: Callable[[str], None], public: bool = False, timeout: int = 900
) -> list[tuple[str, str]]:
    """Wait until every instance runs and accepts SSH, over its EIP if public; return (private IP, EIP) pairs in
    launch order.

    log(message) gets each instance's progress: RUNNING with its IP, ready over SSH, and every
    30 seconds what it still waits for, with the last SSH error.
    """
    started = beat = time.monotonic()
    deadline = started + timeout
    status, ips, ready, error = dict.fromkeys(ids, "PENDING"), {}, set(), {}
    while len(ready) < len(ids):
        elapsed = f"{round(time.monotonic() - started)}s"
        if starting := [i for i in ids if i not in ips]:
            for instance in describe(instance_ids=starting):
                status[instance.instance_id] = instance.status
                if instance.status == "RUNNING":
                    pair = instance.network_interfaces[0].primary_ip_address, instance.eip_address.ip_address
                    ips[instance.instance_id] = pair
                    # Addresses are reused; a previous worker's host key would fail StrictHostKeyChecking.
                    for ip in pair:
                        subprocess.run(["ssh-keygen", "-R", ip], capture_output=True)
                    log(f"{instance.instance_id}  RUNNING {pair[0]}, EIP {pair[1]} ({elapsed})")
        for instance, pair in ips.items():
            if instance in ready:
                continue
            check = subprocess.run(
                [*ssh, "-o", "ConnectTimeout=5", "-n", pair[public], "true"], capture_output=True, text=True
            )
            if check.returncode:
                error[instance] = (check.stderr.strip().splitlines() or [f"exit {check.returncode}"])[-1]
            else:
                ready.add(instance)
                log(f"{instance}  ready over SSH ({elapsed})")
        if len(ready) == len(ids):
            break
        if time.monotonic() - beat >= 30:
            beat = time.monotonic()
            for instance in ids:
                if instance not in ips:
                    log(f"{instance}  still {status[instance]} ({elapsed})")
                elif instance not in ready:
                    log(f"{instance}  still waiting for SSH: {error.get(instance, 'no answer yet')} ({elapsed})")
        if time.monotonic() > deadline:
            raise TimeoutError(f"Not ready after {timeout}s: {', '.join(i for i in ids if i not in ready)}")
        time.sleep(5)
    return [ips[i] for i in ids]


def release(run: str) -> list[str]:
    """Delete every instance tagged with this run; their disks and EIPs go with them."""
    ids = [i.instance_id for i in describe(tag_filters=[ecs.TagFilterForDescribeInstancesInput(key=TAG, values=[run])])]
    api = ecs.ECSApi()
    for instance in ids:
        try:
            api.delete_instance(ecs.DeleteInstanceRequest(instance_id=instance))
        except ApiException as error:
            if error.status != 404 and "NotFound" not in str(error.body):
                raise
    return ids


def on_mount_node(service: str, command: str, via: str) -> str:
    """Run command as root on the service's first client-mount node, logging in with its default password through
    SSH_ASKPASS; returns its output."""
    found = vepfs.VEPFSApi().describe_mount_services(vepfs.DescribeMountServicesRequest(page_size=100)).mount_services
    match = [m for m in found or [] if m.mount_service_id == service]
    if not match:
        raise RuntimeError(f"No vePFS mount service {service}")
    node = match[0].nodes[0]
    ip = describe(instance_ids=[node.node_id])[0].network_interfaces[0].primary_ip_address
    with tempfile.TemporaryDirectory() as tmp:
        askpass = Path(tmp) / "askpass"
        askpass.write_text("#!/bin/sh\nprintf '%s\\n' \"$VEPFS_PASSWORD\"\n")
        askpass.chmod(0o700)
        env = {**os.environ, "SSH_ASKPASS": str(askpass), "SSH_ASKPASS_REQUIRE": "force"}
        env["VEPFS_PASSWORD"] = node.default_password
        options = ["-o", "PubkeyAuthentication=no", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10"]
        options += ["-o", f"ProxyCommand={via}"]
        result = subprocess.run(
            ["ssh", "-l", "root", *options, "-n", ip, command], env=env, capture_output=True, text=True, check=False
        )
    if result.returncode:
        raise RuntimeError(f"{ip}: {command.split()[0]} exited {result.returncode}: {result.stderr.strip()[-500:]}")
    return result.stdout
