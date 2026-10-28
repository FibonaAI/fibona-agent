"""Upload a job to <archive>/<run>/: the whole job directory as <run>.tar.gz, plus meta.json and result.json."""

import json
import os
import re
import subprocess
from pathlib import Path
from tempfile import NamedTemporaryFile

import tos
from steps.workers import REGION


def redact_credentials(job_dir: Path) -> None:
    """Remove known runtime credentials from collected files before they leave the controller."""
    secrets = sorted(
        {
            value.encode()
            for name, value in os.environ.items()
            if value and re.search(r"(^|_)(KEY|TOKEN|SECRET|PASSWORD)(_|$)", name.upper())
        },
        key=len,
        reverse=True,
    )
    if not secrets:
        return
    pattern = re.compile(b"|".join(re.escape(value) for value in secrets))

    def mask(match: re.Match[bytes]) -> bytes:
        value = match.group()
        return value[:4] + b"...[REDACTED]..." + value[-4:] if len(value) >= 16 else b"[REDACTED]"

    changed = []
    for path in list(job_dir.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        count = 0
        with NamedTemporaryFile(dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            try:
                with path.open("rb") as source:
                    for line in source:
                        masked, matches = pattern.subn(mask, line)
                        if matches and b"\0" in line:
                            raise ValueError(f"Credential in binary artifact: {path.relative_to(job_dir)}")
                        count += matches
                        line = masked
                        output.write(line)
                output.flush()
                if count:
                    temporary.chmod(path.stat().st_mode & 0o777)
                    temporary.replace(path)
                    changed.append({"path": str(path.relative_to(job_dir)), "occurrences": count})
            finally:
                temporary.unlink(missing_ok=True)
    if changed:
        report = job_dir / "redaction.json"
        previous = json.loads(report.read_text()) if report.exists() else {"files": []}
        previous["files"].extend(changed)
        report.write_text(json.dumps(previous, indent=2) + "\n")


def upload(job_dir: Path, archive: str) -> str:
    redact_credentials(job_dir)
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
