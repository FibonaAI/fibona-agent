"""Harbor environment that runs each trial's Docker Compose project on one of our own hosts over SSH.

Harbor's EC2 environment already does everything over SSH once it has a host: staging, docker compose, exec,
file transfer and cleanup. This subclass attaches to a fixed host pool instead of AWS, so one harbor process
schedules every trial and writes one job.

Usage: harbor run --env ssh_hosts:SSHHosts --ek hosts=me@node1:16,me@node2:16 -n 32 ...
Each host needs Docker with Compose, passwordless SSH from the head and passwordless sudo.
"""

import asyncio
import shutil
from collections import Counter
from typing import ClassVar

from harbor.environments import ec2


# Attach mode with the overrides below never calls AWS. boto3, which EC2Environment checks for, already
# comes with harbor through litellm.
class SSHHosts(ec2.EC2Environment):
    _active: ClassVar[Counter[str]] = Counter()  # Trials running per host, across this process.
    _lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    @classmethod
    def preflight(cls) -> None:
        if not shutil.which("ssh"):
            raise SystemExit("SSHHosts requires the OpenSSH client (`ssh`) on PATH.")

    def __init__(self, *args, hosts: str, **kwargs) -> None:
        # "me@node1:16,me@node2:16" -> {"me@node1": 16, "me@node2": 16}
        self.slots = {host: int(n) for host, _, n in (item.strip().rpartition(":") for item in hosts.split(","))}
        super().__init__(
            *args, region="none", launch_mode="attach", instance_id="ssh-hosts", bootstrap_docker=False, **kwargs
        )

    async def _wait_for_instance_ready(self) -> None:
        pass

    async def _resolve_host(self) -> None:
        # ponytail: least-loaded by slots; slots only weight hosts, harbor -n caps the total.
        async with self._lock:
            host = min(self.slots, key=lambda h: self._active[h] / self.slots[h])
            self._active[host] += 1
        self.ssh_user, _, self._host = host.rpartition("@")

    def _ssh_destination(self) -> str:
        return f"{self.ssh_user}@{self._host}"

    async def stop(self, delete: bool) -> None:
        try:
            await super().stop(delete)
        finally:
            if self._host is not None:
                async with self._lock:
                    self._active[self._ssh_destination()] -= 1
