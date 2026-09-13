"""Ticketing platform providers.

V1 ships BookMyShow only. A future ``district.py`` / ``pvr.py`` just needs to
implement :class:`platforms.base.Provider` and register itself below — the
monitoring engine only ever sees ``monitor.models`` types.
"""

from __future__ import annotations

from platforms.base import Platform, PlatformBlocked, PlatformError, Provider
from platforms.bookmyshow import BookMyShowProvider

_PROVIDERS: dict[str, Provider] = {
    BookMyShowProvider.slug: BookMyShowProvider(),
}

#: Shown on the platform selector; only ``enabled`` ones can be picked.
PLATFORMS: list[Platform] = [
    Platform("bookmyshow", "BookMyShow", enabled=True, city="Hyderabad"),
    Platform("district", "District", enabled=False),
    Platform("pvr", "PVR Cinemas", enabled=False),
    Platform("cinepolis", "Cinépolis", enabled=False),
]


def get_provider(slug: str) -> Provider:
    try:
        return _PROVIDERS[slug]
    except KeyError:
        raise PlatformError(f"No provider registered for '{slug}'.") from None


__all__ = [
    "PLATFORMS",
    "Platform",
    "PlatformBlocked",
    "PlatformError",
    "Provider",
    "get_provider",
]
