#!/usr/bin/env python3
"""One-request BookMyShow connectivity probe for the worker image.

Makes exactly ONE HTTP GET: the city quick-book listing that the worker's
discovery already reads every ~15 minutes while a monitor runs
(``BookMyShowProvider._listing_quickbook``), through the same TLS client, with
block retries and strategy fall-through disabled. No Firestore, no secrets,
no email. The last line is the verdict:

    BMS_PROBE_OK movies=<n>        exit 0
    BMS_PROBE_BLOCKED http=<code>  exit 2   (401/403)
    BMS_PROBE_RATE_LIMITED         exit 3   (429)
    BMS_PROBE_HTTP_ERROR http=<c>  exit 4
    BMS_PROBE_NETWORK_ERROR <kind> exit 5
    BMS_PROBE_MALFORMED            exit 6

Technical reachability only. It says nothing about whether BookMyShow
permits automated access.
"""
from __future__ import annotations

import sys


class CountingSession:
    """Wraps the production session and refuses a second request."""

    def __init__(self, inner):
        self.inner = inner
        self.requests = 0
        self.status = None

    def get(self, url, headers=None, params=None, timeout=20):
        if self.requests:
            raise RuntimeError("probe allows exactly one request")
        self.requests += 1
        response = self.inner.get(url, headers=headers, params=params, timeout=timeout)
        self.status = getattr(response, "status_code", None)
        return response


def probe(city: str = "hyderabad", session=None) -> tuple[int, str]:
    from platforms.base import PlatformBlocked, PlatformError
    from platforms.bookmyshow import BookMyShowProvider, region_for
    from platforms.http import HAS_CURL_CFFI, ImpersonatingSession

    if session is None:
        if not HAS_CURL_CFFI:
            return 6, "BMS_PROBE_MALFORMED curl_cffi missing from the image"
        session = ImpersonatingSession(retries=1)
    counting = CountingSession(session)
    provider = BookMyShowProvider(session=counting, sleeper=lambda _s: None)
    try:
        result = provider._listing_quickbook(region_for(city))
    except PlatformBlocked:
        return 2, f"BMS_PROBE_BLOCKED http={counting.status}"
    except PlatformError as exc:
        if counting.status == 429:
            return 3, "BMS_PROBE_RATE_LIMITED http=429"
        if counting.status is None:
            return 5, f"BMS_PROBE_NETWORK_ERROR {exc}"
        if counting.status == 200:
            return 6, "BMS_PROBE_MALFORMED"
        return 4, f"BMS_PROBE_HTTP_ERROR http={counting.status}"
    except Exception as exc:  # noqa: BLE001 - the verdict line must always print
        return 5, f"BMS_PROBE_NETWORK_ERROR {type(exc).__name__}"
    finally:
        print(f"[probe] requests={counting.requests} status={counting.status}", flush=True)
    if not result.authoritative and not result.movies:
        return 6, "BMS_PROBE_MALFORMED"
    return 0, f"BMS_PROBE_OK movies={len(result.movies)}"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    code, line = probe(args[0] if args else "hyderabad")
    print(line, flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
