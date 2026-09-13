"""Supported locations.

V1 ships Hyderabad only, but the city is a value that flows through the app
rather than a string baked into every module: the UI renders whatever is
``enabled`` here, the provider already knows the region codes for the rest,
and the catalogue is keyed per region. Adding Bengaluru later is a one-line
change in this file plus a catalogue sync.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Location:
    slug: str
    name: str
    state: str
    country: str = "India"
    enabled: bool = True

    @property
    def label(self) -> str:
        return f"{self.name}, {self.state}"


LOCATIONS: list[Location] = [
    Location("hyderabad", "Hyderabad", "Telangana"),
    Location("bengaluru", "Bengaluru", "Karnataka", enabled=False),
    Location("chennai", "Chennai", "Tamil Nadu", enabled=False),
    Location("mumbai", "Mumbai", "Maharashtra", enabled=False),
]

DEFAULT_LOCATION = "hyderabad"


def enabled_locations() -> list[Location]:
    return [loc for loc in LOCATIONS if loc.enabled]


def get_location(slug: str) -> Location:
    for loc in LOCATIONS:
        if loc.slug == slug:
            return loc
    return get_location(DEFAULT_LOCATION) if slug != DEFAULT_LOCATION else LOCATIONS[0]


def location_name(slug: str) -> str:
    return get_location(slug).name


__all__ = [
    "DEFAULT_LOCATION",
    "LOCATIONS",
    "Location",
    "enabled_locations",
    "get_location",
    "location_name",
]
