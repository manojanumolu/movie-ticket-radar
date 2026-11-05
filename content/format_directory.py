"""The Explorer's 24 craft icons, each explaining one cinema format.

Educational only. Nothing here is read by monitoring, the wizard's Formats
step, catalogue detection or the BookMyShow provider. The categories keep
different kinds of thing apart: an auditorium format is not a projector, a
sound system is not a screen, and a luxury brand or a ticketing label is not
a technology at all.

Hyderabad: a card says a format is in TicketRadar's Hyderabad theatre data
only when ``config.theatre_capabilities.PREMIUM_SCREENS`` names it — read
here, never written — and no card says any theatre is showing it now.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.theatre_capabilities import PREMIUM_SCREENS

SCREEN = "Premium screen format"
PROJECTION = "Projection & display technology"
THREE_D = "3D system"
SOUND = "Sound system"
MOTION = "Motion & effects system"
LUXURY = "Luxury auditorium brand"
SEATING = "Premium seating label"
LABEL = "Ticketing label"
CATEGORIES = (SCREEN, PROJECTION, THREE_D, SOUND, MOTION, LUXURY, SEATING, LABEL)

INDIAN = "Indian cinema chains"
INTERNATIONAL = "International"


@dataclass(frozen=True)
class Format:
    name: str
    category: str
    group: str          # INDIAN or INTERNATIONAL
    description: str
    source: str
    match: str = ""     # how PREMIUM_SCREENS spells it, when that differs


#: craft key (``ui.crafts``) -> the format that icon explains.
BY_CRAFT: dict[str, Format] = {
    # ── the band above the hero ─────────────────────────────────────────
    "direction": Format("P[XL]", SCREEN, INDIAN,
                        "PVR INOX's own premium large-format auditorium: an extra-large screen with laser projection.",
                        "CinemaView, PVR INOX premium formats explained; existing theatre data", "PXL"),
    "cinematography": Format("PCX", SCREEN, INDIAN,
                             "Prasads Multiplex's own premium large-screen auditorium.",
                             "HyderabadTheatres.com, via existing theatre data"),
    "screenwriting": Format("EPIQ", SCREEN, INDIAN,
                            "A premium-screen name used by AAA Cinemas and ART Cinemas. AAA's EPIQ is a "
                            "direct-view LED screen rather than a projected picture.",
                            "HyderabadTheatres.com, via existing theatre data"),
    "producing": Format("Macro XE", SCREEN, INDIAN,
                        "Cinépolis's own premium large-format auditorium.",
                        "HyderabadTheatres.com, via existing theatre data"),
    "acting": Format("Infinity Vision", LABEL, INDIAN,
                     "A BookMyShow listing label used for Marvel Studios releases on selected screens — "
                     "not a physical screen technology.",
                     "TicketRadar's existing BookMyShow handling (monitor/models.py)"),
    "editing": Format("ICE", SCREEN, INDIAN,
                      "Immersive Cinema Experience: an auditorium concept from France's CGR Cinemas, "
                      "run in India by PVR INOX.",
                      "Indian Broadcasting World; CinemaView"),
    "projection": Format("HDR by Barco", PROJECTION, INDIAN,
                         "Barco's high-dynamic-range laser projection, for deeper blacks and brighter highlights.",
                         "Barco; existing theatre data", "HDR By Barco"),
    "distribution": Format("ONYX", PROJECTION, INDIAN,
                           "Samsung's LED cinema screen: the picture comes from the screen itself, with no "
                           "projector. India's first opened at PVR Icon, Delhi, in 2018.",
                           "Business Standard; Celluloid Junkie"),
    # ── the ledge under the results ─────────────────────────────────────
    "production_design": Format("LUXE", LUXURY, INDIAN,
                                "PVR INOX's luxury auditorium brand — a comfort upgrade, not a picture or "
                                "sound technology.",
                                "CinemaView; existing theatre data"),
    "art_direction": Format("INSIGNIA", LUXURY, INDIAN,
                            "INOX's luxury auditorium brand: leather recliners, gourmet food and butler service.",
                            "Mediainfoline; Business Today"),
    "costume_design": Format("MB LUXE", LUXURY, INDIAN,
                             "AMB Cinemas' luxury auditorium, named as such in its screen listings.",
                             "HyderabadTheatres.com, via existing theatre data"),
    "makeup": Format("VIP", SEATING, INDIAN,
                     "A theatre-specific premium seating label, not a cinema technology; what a VIP screen "
                     "offers varies from theatre to theatre.",
                     "HyderabadTheatres.com, via existing theatre data"),
    "hair": Format("Director's Cut", LUXURY, INDIAN,
                   "PVR's most luxurious cinema format: small auditoriums with recliners and dining.",
                   "Business Standard; The South First"),
    "set_design": Format("IMAX", SCREEN, INTERNATIONAL,
                         "IMAX's licensed auditorium: a floor-to-ceiling screen, steeply raked seating, IMAX "
                         "laser projection and IMAX's own multichannel sound.",
                         "IMAX theatre pages (IMAX with Laser)"),
    "casting": Format("Dolby Cinema", SCREEN, INTERNATIONAL,
                      "Dolby's licensed auditorium: Dolby Vision laser projection and Dolby Atmos sound in a "
                      "dark room designed against stray light.",
                      "Business Today; Onmanorama; Pathé"),
    "color_grading": Format("CINITY", PROJECTION, INTERNATIONAL,
                            "A projection system from China's Huaxia Film: dual laser projectors for 4K, up to "
                            "120 frames per second, HDR and 3D.",
                            "Boxoffice Pro; GDC Technology"),
    "sound": Format("Dolby Atmos", SOUND, INTERNATIONAL,
                    "Dolby's object-based cinema sound: up to 128 tracks placed across as many as 64 "
                    "speakers, including overhead. A sound system, not a screen.",
                    "Dolby Professional, Dolby Atmos for cinema playback"),
    "music": Format("DTS:X", SOUND, INTERNATIONAL,
                    "DTS's object-based cinema sound, not tied to a fixed speaker layout. A sound system, "
                    "not a screen.",
                    "DTS; GDC Technology"),
    "sound_mixing": Format("Auro 11.1", SOUND, INTERNATIONAL,
                           "Barco's immersive sound format with a layer of height speakers; India's first film "
                           "in it was Vishwaroopam (2013). A sound system, not a screen.",
                           "Barco; Wikipedia; PR Newswire"),
    "lighting": Format("RealD 3D", THREE_D, INTERNATIONAL,
                       "Single-projector 3D: the two eyes' images are circularly polarised and sorted by "
                       "passive glasses, so tilting your head keeps the effect.",
                       "HowStuffWorks, How RealD 3-D Works"),
    "visual_effects": Format("ScreenX", SCREEN, INTERNATIONAL,
                             "CJ 4DPLEX's multi-projection format: for selected scenes the picture extends onto "
                             "the left and right side walls.",
                             "CJ 4DPLEX, via Boxoffice Pro"),
    "special_effects": Format("4DX", MOTION, INTERNATIONAL,
                              "CJ 4DPLEX's motion seats, with effects such as wind, water and scent timed to "
                              "the film.",
                              "CJ 4DPLEX, via Boxoffice Pro and PR Newswire"),
    "choreography": Format("MX4D", MOTION, INTERNATIONAL,
                           "MediaMation's pneumatic motion seats, with optional effects such as air and water, "
                           "programmed to match the film.",
                           "MediaMation; Blooloop"),
    "stunts": Format("D-BOX", MOTION, INTERNATIONAL,
                     "D-BOX's (Canada) motion seats: they move on three axes and vibrate, driven by a motion "
                     "code synchronised with the film.",
                     "Residential Systems; Cinemark"),
}


def _norm(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


#: Format names TicketRadar's verified Hyderabad theatre data uses — read only.
IN_HYDERABAD_DATA = frozenset(_norm(f) for _page, screens in PREMIUM_SCREENS.values() for f in screens)


def in_hyderabad_data(fmt: Format) -> bool:
    return _norm(fmt.match or fmt.name) in IN_HYDERABAD_DATA


def availability(fmt: Format) -> str:
    """One honest line: in TicketRadar's Hyderabad data, or not."""
    if in_hyderabad_data(fmt):
        return "Named in TicketRadar's Hyderabad theatre data"
    if fmt.category == LABEL:
        return "Listed by BookMyShow · not in TicketRadar's theatre data"
    if fmt.group == INTERNATIONAL:
        return "Available internationally · not in TicketRadar's Hyderabad data"
    return "In India · not in TicketRadar's Hyderabad data"


assert len({f.name for f in BY_CRAFT.values()}) == len(BY_CRAFT) == 24, "24 distinct formats"
assert all(f.category in CATEGORIES for f in BY_CRAFT.values())

__all__ = [
    "BY_CRAFT",
    "CATEGORIES",
    "INDIAN",
    "INTERNATIONAL",
    "IN_HYDERABAD_DATA",
    "Format",
    "availability",
    "in_hyderabad_data",
]
