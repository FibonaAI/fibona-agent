"""Upload a job to <archive>/<run>/: the whole job directory as <run>.tar.gz, plus meta.json and result.json."""

import subprocess
from pathlib import Path

import tos
from steps.workers import REGION


def upload(job_dir: Path, archive: str) -> str:
    bucket, _, prefix = archive.removeprefix("tos://").partition("/")
    prefix = "/".join(filter(None, [prefix.strip("/"), job_dir.name]))
    tarball = job_dir.with_suffix(".tar.gz")
    subprocess.run(["tar", "czf", tarball, "-C", job_dir.parent, job_dir.name], check=True)
    client = tos.TosClientV2(
        endpoint=f"tos-{REGION}.volces.com", region=REGION, credentials_provider=tos.EnvCredentialsProvider()
    )
    try:
        for path in [tarball, job_dir / "meta.json", *(job_dir / "jobs").glob("*/result.json")]:
            client.upload_file(bucket, f"{prefix}/{path.name}", str(path))
    finally:
        tarball.unlink()
    return f"tos://{bucket}/{prefix}/"
