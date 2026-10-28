"""A job's ECS workers: launch them, wait for SSH, and delete them by their fibona-job tag."""

import subprocess
import time
from uuid import NAMESPACE_URL, uuid5

import volcenginesdkecs as ecs
from plugins.hosts import Resources
from pydantic import BaseModel, ConfigDict, Field, PositiveInt
from steps.retry import network_retry, transient
from volcenginesdkcore.rest import ApiException

TAG = "fibona-job"
REGION = "cn-beijing"


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
        client_token=str(uuid5(NAMESPACE_URL, f"{run}/{group}")),
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

    @network_retry
    def create():
        try:
            return ecs.ECSApi().run_instances(request).instance_ids
        except Exception as error:
            if not transient(error):
                raise
            # A lost response may hide a successful creation. Reconcile before resending
            # the same idempotency token; never issue a fresh allocation request.
            existing = [
                i.instance_id
                for i in describe(tag_filters=[ecs.TagFilterForDescribeInstancesInput(key=TAG, values=[run])])
                if i.instance_name == request.instance_name
            ]
            if len(existing) == spec.count:
                return existing
            raise

    return create()


def describe(**filters) -> list:
    api, instances, token = ecs.ECSApi(), [], None
    while True:
        result = network_retry(api.describe_instances)(
            ecs.DescribeInstancesRequest(max_results=100, next_token=token, **filters)
        )
        instances += result.instances or []
        token = result.next_token
        if not token:
            return instances


def wait(ids: list[str], ssh: list[str], timeout: int = 900) -> list[str]:
    """Wait until every instance runs and accepts SSH; return private IPs in launch order."""
    deadline = time.monotonic() + timeout
    while True:
        instances = {i.instance_id: i for i in describe(instance_ids=ids)}
        if all(i in instances and instances[i].status == "RUNNING" for i in ids):
            break
        if time.monotonic() > deadline:
            raise TimeoutError(f"Instances not running: {ids}")
        time.sleep(10)
    ips = [instances[i].network_interfaces[0].primary_ip_address for i in ids]
    for ip in ips:
        # VPC addresses are reused; a previous worker's host key would fail StrictHostKeyChecking.
        subprocess.run(["ssh-keygen", "-R", ip], capture_output=True)
        while subprocess.run([*ssh, "-o", "ConnectTimeout=5", "-n", ip, "true"], capture_output=True).returncode:
            if time.monotonic() > deadline:
                raise TimeoutError(f"No SSH on {ip}")
            time.sleep(5)
    return ips


def release(run: str) -> list[str]:
    """Delete every instance tagged with this run; their disks and EIPs go with them."""
    ids = [i.instance_id for i in describe(tag_filters=[ecs.TagFilterForDescribeInstancesInput(key=TAG, values=[run])])]
    api = ecs.ECSApi()
    for instance in ids:
        try:
            network_retry(api.delete_instance)(ecs.DeleteInstanceRequest(instance_id=instance))
        except ApiException as error:
            if error.status != 404 and "NotFound" not in str(error.body):
                raise
    return ids
