"""
Centralized HTTP client with retry, backoff, and timeout configuration.

All external API calls should use resilient_get() instead of raw requests.get().
"""
from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger(__name__)

# ── Centralized config ──

DEFAULT_TIMEOUT = 15
DEFAULT_RETRIES = 2          # 2 retries = up to 3 total attempts
DEFAULT_BACKOFF = 1.5        # exponential backoff base (seconds)
BASELINE_TIMEOUT = 12        # shorter for per-pitcher calls
PER_PLAYER_RETRIES = 1       # fewer retries for high-volume per-player calls


def resilient_get(
    url: str,
    *,
    params: dict | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    backoff: float = DEFAULT_BACKOFF,
    label: str = "",
) -> requests.Response:
    """GET with retries and exponential backoff.

    Retries on connection errors, timeouts, and 5xx responses.
    Raises the last exception if all attempts fail.
    """
    last_exc: Exception | None = None
    tag = label or url.split("?")[0].split("/")[-1]
    max_attempts = retries + 1

    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)

            if resp.status_code >= 500 and attempt < max_attempts:
                wait = backoff * attempt
                logger.warning(
                    "[%s] Server error %d on attempt %d/%d — retrying in %.1fs",
                    tag, resp.status_code, attempt, max_attempts, wait,
                )
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp

        except requests.exceptions.ConnectionError as e:
            last_exc = e
            if attempt < max_attempts:
                wait = backoff * attempt
                logger.warning(
                    "[%s] Connection error on attempt %d/%d — retrying in %.1fs: %s",
                    tag, attempt, max_attempts, wait, e,
                )
                time.sleep(wait)
            else:
                logger.error("[%s] Connection error on final attempt: %s", tag, e)

        except requests.exceptions.Timeout as e:
            last_exc = e
            if attempt < max_attempts:
                wait = backoff * attempt
                logger.warning(
                    "[%s] Timeout on attempt %d/%d — retrying in %.1fs",
                    tag, attempt, max_attempts, wait,
                )
                time.sleep(wait)
            else:
                logger.error("[%s] Timeout on final attempt", tag)

        except requests.exceptions.HTTPError:
            # 4xx and other non-retryable HTTP errors — raise immediately
            raise

    raise last_exc  # type: ignore[misc]
