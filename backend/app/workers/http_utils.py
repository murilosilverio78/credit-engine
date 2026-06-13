import time
from typing import Any

import httpx
import structlog


logger = structlog.get_logger()


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after and retry_after.isdigit():
            return min(float(retry_after), 15.0)
    return min(float(2 ** attempt), 15.0)


def fetch_json_with_retry(
    client: httpx.Client,
    url: str,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    max_retries: int = 3,
) -> list | dict:
    """Fetch JSON with retry/backoff for transient Portal da Transparencia errors."""
    retry_exceptions = (
        httpx.ConnectError,
        httpx.RemoteProtocolError,
        httpx.ReadTimeout,
    )
    for attempt in range(max_retries + 1):
        response: httpx.Response | None = None
        try:
            response = client.get(url, headers=headers, params=params)
            if response.status_code in {429} or response.status_code >= 500:
                response.raise_for_status()
            response.raise_for_status()
            if not response.content or not response.text.strip():
                return []
            return response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status != 429 and status < 500:
                raise
            if attempt >= max_retries:
                raise
            delay = _retry_delay(exc.response, attempt)
            logger.warning(
                "portal_transparencia.retry",
                url=url.split("?", 1)[0],
                status=status,
                tentativa=attempt + 1,
                delay_s=delay,
            )
            time.sleep(delay)
        except retry_exceptions as exc:
            if attempt >= max_retries:
                raise
            delay = _retry_delay(response, attempt)
            logger.warning(
                "portal_transparencia.retry",
                url=url.split("?", 1)[0],
                status=None,
                tentativa=attempt + 1,
                delay_s=delay,
                error=type(exc).__name__,
            )
            time.sleep(delay)

    raise RuntimeError("retry loop exited unexpectedly")
