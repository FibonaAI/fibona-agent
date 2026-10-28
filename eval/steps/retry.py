"""Bounded retries for transient transport failures, not task or authentication errors."""

import httpx
import urllib3
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential
from volcenginesdkcore.rest import ApiException


def transient(error: BaseException) -> bool:
    if "certificate verify failed" in str(error).lower().replace("_", " "):
        return False
    if isinstance(
        error,
        (
            httpx.TransportError,
            urllib3.exceptions.ProtocolError,
            urllib3.exceptions.TimeoutError,
            urllib3.exceptions.NewConnectionError,
            urllib3.exceptions.SSLError,
            TimeoutError,
            ConnectionError,
        ),
    ):
        return True
    status = getattr(error, "status_code", None)
    if isinstance(error, ApiException):
        status = error.status
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
    return status in {0, 408, 429, 500, 502, 503, 504}


network_retry = retry(
    retry=retry_if_exception(transient),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, max=8),
    reraise=True,
)


def transient_output(output: str) -> bool:
    """CLI tools have no typed transport exception; only recognize explicit transient failures."""
    return any(
        message in output.lower()
        for message in (
            "connection reset",
            "connection refused",
            "connection timed out",
            "i/o timeout",
            "tls handshake timeout",
            "temporary failure in name resolution",
            "unexpected eof",
            "too many requests",
            "502 bad gateway",
            "503 service unavailable",
            "504 gateway timeout",
        )
    )
