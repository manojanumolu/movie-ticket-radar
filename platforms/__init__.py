"""Ticketing platform providers.

BookMyShow is live. PVR INOX is registered but **disabled**: its provider
exists and is tested, but no page offers it, no monitor can be created on
it, and the worker checks nothing on it until ``enabled`` is flipped — which
waits on the PVR INOX diagnose workflow proving a GitHub runner may call the
API (see ``platforms/pvr_inox.py``). The monitoring engine only ever sees
``monitor.models`` types; a provider implements :class:`platforms.base.Provider`
and registers itself below.
"""

from __future__ import annotations

from platforms.base import Platform, PlatformBlocked, PlatformError, Provider
from platforms.bookmyshow import BookMyShowProvider, is_bookmyshow_url
from platforms.pvr_inox import PvrInoxProvider, is_pvr_url

_PROVIDERS: dict[str, Provider] = {
    BookMyShowProvider.slug: BookMyShowProvider(),
    PvrInoxProvider.slug: PvrInoxProvider(),
}

#: Shown on the platform selector; only ``enabled`` ones can be picked,
#: offered in the wizard, or checked by the worker.
PLATFORMS: list[Platform] = [
    Platform("bookmyshow", "BookMyShow", enabled=True, city="Hyderabad"),
    Platform("district", "District", enabled=False),
    Platform("pvr_inox", "PVR INOX", enabled=False, city="Hyderabad"),
    Platform("cinepolis", "Cinépolis", enabled=False),
]

DEFAULT_PLATFORM = "bookmyshow"


def get_provider(slug: str) -> Provider:
    try:
        return _PROVIDERS[slug]
    except KeyError:
        raise PlatformError(f"No provider registered for '{slug}'.") from None


def is_enabled(slug: str) -> bool:
    """Is this platform switched on *and* backed by a provider?"""
    return slug in _PROVIDERS and any(p.slug == slug and p.enabled for p in PLATFORMS)


def enabled_platforms() -> list[Platform]:
    return [p for p in PLATFORMS if p.enabled and p.slug in _PROVIDERS]


def platform_name(slug: str) -> str:
    """'BookMyShow', 'PVR INOX' — what people are told they are watching."""
    return next((p.name for p in PLATFORMS if p.slug == slug), slug or "BookMyShow")


def is_platform_url(url: str, slug: str) -> bool:
    """Is ``url`` a link on this platform's own https site? Each platform
    checks its own hosts; a link from one is never accepted for another."""
    if slug == "bookmyshow":
        return is_bookmyshow_url(url)
    if slug == "pvr_inox":
        return is_pvr_url(url)
    return False


__all__ = [
    "DEFAULT_PLATFORM",
    "PLATFORMS",
    "Platform",
    "PlatformBlocked",
    "PlatformError",
    "Provider",
    "enabled_platforms",
    "get_provider",
    "is_enabled",
    "is_platform_url",
    "platform_name",
]
