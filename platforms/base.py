"""The contract every ticketing platform must satisfy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from monitor.models import MovieRef, Snapshot  # noqa: F401 - used in the Protocol


class PlatformError(Exception):
    """We could not get an answer from the platform.

    Raised — never swallowed into an empty result — so the checker can record
    ``ERROR`` instead of mistaking a failure for "no tickets".
    """


class PlatformBlocked(PlatformError):
    """The platform refused us (WAF / bot check / rate limit).

    Separate from a generic error because the remedy is different: retrying
    harder makes it worse, and the honest UI copy is "we couldn't look",
    not "try again now".
    """


@dataclass(frozen=True)
class Platform:
    slug: str
    name: str
    enabled: bool = False
    city: str = ""


class Provider(Protocol):
    """A ticketing source.

    Implementations must raise :class:`PlatformError` on any failure and only
    return a :class:`Snapshot` when they genuinely read the listing.
    """

    slug: str
    name: str

    def list_movies(self, region_slug: str) -> list[MovieRef]:
        """Every movie currently listed in a city.

        An empty list means the city genuinely has nothing on. Not being able
        to ask must raise :class:`PlatformError` instead — the difference is
        what stops the UI showing an empty grid for a bot check.
        """
        ...

    def resolve(self, url: str) -> Snapshot:
        """Turn a listing URL into a full snapshot. Admin path, not user-facing."""
        ...

    def fetch(self, movie: MovieRef, date_codes: list[str] | None = None) -> Snapshot:
        """Re-read the listing for an already known movie."""
        ...

    def booking_url(self, movie: MovieRef, date_code: str = "") -> str:
        """A link the user can actually click. Must be derived, never invented."""
        ...


__all__ = ["Platform", "PlatformBlocked", "PlatformError", "Provider"]
