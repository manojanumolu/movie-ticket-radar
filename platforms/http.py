"""The HTTP client BookMyShow will actually answer.

Findings, from running ``tools/bms_diagnose.py`` on CI runners (two runs):

    plain requests, any headers, any URL      403   403
    requests + homepage warm-up               403   403
    curl_cffi impersonate=chrome              403   200
    curl_cffi impersonate=safari              200   403

Two things follow, and both shape this module.

**Plain ``requests`` never works.** BookMyShow's Cloudflare bot management
fingerprints the TLS ClientHello, and urllib3's is refused outright — no
header tuning changes it. ``curl_cffi`` replays a real browser's handshake.
This is why the app's original paste-a-URL flow never worked reliably either:
the URL was never the problem, the client was.

**A single profile is not enough.** The same profile that sails through on one
run is refused on the next, so the block is probabilistic rather than a
permanent verdict on a fingerprint. The fix is to rotate: on a refusal, throw
the session away, pick the next profile, and try again. A request that looks
hopeless on attempt one frequently succeeds on attempt two.

``curl_cffi`` is an optional dependency. Without it we fall back to
``requests`` so the package still imports and the tests still run — the
failure then surfaces as a normal :class:`PlatformBlocked`, which the whole
application already handles honestly, rather than an ImportError at startup.
"""

from __future__ import annotations

import random
import time
from typing import Any, Protocol

#: Browser profiles to rotate through, in order. Both have been observed
#: getting a 200 where the other got a 403.
IMPERSONATE_PROFILES = ("chrome", "safari", "chrome131", "edge99")

#: How many times a single request is re-attempted across profiles before we
#: accept the refusal. Kept small: this is a bot check, not a flaky link, and
#: hammering it is both useless and rude.
BLOCK_RETRIES = 3

try:  # pragma: no cover - depends on what's installed
    from curl_cffi import requests as _curl

    HAS_CURL_CFFI = True
except Exception:  # pragma: no cover
    _curl = None
    HAS_CURL_CFFI = False

import requests as _requests


class HttpSession(Protocol):
    """The slice of a session the provider uses."""

    def get(self, url: str, headers: dict[str, str] | None = ...,
            params: dict[str, Any] | None = ..., timeout: int | float = ...) -> Any: ...


class ImpersonatingSession:
    """curl_cffi, rotating browser profiles when a request is refused.

    Presents the ``requests``-style ``get`` the provider expects, so nothing
    downstream knows or cares which client is underneath.
    """

    def __init__(self, profiles: tuple[str, ...] = IMPERSONATE_PROFILES,
                 retries: int = BLOCK_RETRIES, sleeper=time.sleep) -> None:
        self._profiles = list(profiles)
        self._retries = max(1, retries)
        self._sleep = sleeper
        self._index = 0
        self._session = None
        self.last_profile = self._profiles[0]

    def _open(self):
        if self._session is None:
            self.last_profile = self._profiles[self._index % len(self._profiles)]
            self._session = _curl.Session(impersonate=self.last_profile)
        return self._session

    def _rotate(self) -> None:
        """Drop the session and move to the next profile.

        The session is discarded rather than reused because the refusal may
        be attached to its connection and cookies, not only its fingerprint.
        """
        try:
            if self._session is not None:
                self._session.close()
        except Exception:  # pragma: no cover - best effort
            pass
        self._session = None
        self._index += 1

    def get(self, url, headers=None, params=None, timeout=20):
        last = None
        for attempt in range(self._retries):
            session = self._open()
            try:
                response = session.get(url, headers=headers, params=params, timeout=timeout)
            except Exception as exc:  # noqa: BLE001 - retried below, re-raised at the end
                last = exc
                self._rotate()
                if attempt < self._retries - 1:
                    self._sleep(1.5 * (attempt + 1) + random.uniform(0, 0.5))
                continue

            if response.status_code not in (401, 403) or attempt == self._retries - 1:
                return response

            # Refused: a different fingerprint often gets straight through.
            self._rotate()
            self._sleep(1.5 * (attempt + 1) + random.uniform(0, 0.5))

        if last is not None:
            raise last
        return response  # pragma: no cover - unreachable

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        return f"<ImpersonatingSession profile={self.last_profile}>"


def build_session(sleeper=time.sleep) -> HttpSession:
    """The best client available here.

    Callers do not branch on the result: a ``requests`` fallback still makes
    the request, it just gets refused, and a refusal is a state the whole
    application already reports honestly.
    """
    if HAS_CURL_CFFI:
        return ImpersonatingSession(sleeper=sleeper)
    return _requests.Session()


def transport_name(session: Any) -> str:
    """Human-readable description, for diagnostics and the Settings page."""
    if isinstance(session, ImpersonatingSession):
        return f"curl_cffi, rotating {'/'.join(session._profiles)}"
    if isinstance(session, _requests.Session):
        return "requests — BookMyShow will bot-check this; install curl_cffi"
    return type(session).__name__


def request_errors() -> tuple[type[BaseException], ...]:
    """Exceptions meaning "the request did not complete", for either client."""
    errors: list[type[BaseException]] = [_requests.RequestException]
    if HAS_CURL_CFFI:  # pragma: no cover - depends on install
        try:
            from curl_cffi.requests.exceptions import RequestException as CurlError

            errors.append(CurlError)
        except Exception:
            try:
                from curl_cffi import CurlError  # type: ignore

                errors.append(CurlError)
            except Exception:
                pass
    return tuple(errors)


REQUEST_ERRORS = request_errors()

__all__ = [
    "BLOCK_RETRIES",
    "HAS_CURL_CFFI",
    "IMPERSONATE_PROFILES",
    "REQUEST_ERRORS",
    "HttpSession",
    "ImpersonatingSession",
    "build_session",
    "transport_name",
]
