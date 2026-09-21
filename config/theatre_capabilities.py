"""What premium screens each Hyderabad theatre *has* — its capability.

A theatre's capability is not a movie's listing. BookMyShow's showtimes say
what format a film is playing in at a theatre on a date; this table says
which named premium screens the theatre owns, so that a theatre which has
not listed a film yet can still be watched for a specific premium format
("PVR Lakeshore, in PXL, when it opens").

Source: HyderabadTheatres.com — each theatre's page, read on 22 Sep 2026,
where a screen carries a named premium branding in its own name or type
("Audi 6 (P[XL])", "Screen 6 (PCX)", "Dolby Cinema (Screen 1)", "Screen 6
(EPIQ)"). Only named premium cinema formats and experiences are kept: bare
technology (4K, laser, Atmos, DTS:X, 7.1), seating (recliners, luxury
seating), amenities, projector models, seat counts and a chain's names
for ordinary auditoria (TURBO, MYTHOS, Bellezia, Play House…) are not
formats and are left out. Infinity Vision is not verified by the source
for any theatre here and so appears nowhere in this table; when
BookMyShow lists a film in it, the listing shows it.

Keyed by BookMyShow venue code. ``aliases`` are the strings BookMyShow has
been seen to use for that screen when they do not contain the format's
name — AAA's EPIQ screen is sold as "Led Screen Dolby Atmos" — so a watch
for the format still recognises the listing.
"""

from __future__ import annotations

SOURCE = "https://www.hyderabadtheatres.com/"
VERIFIED = "2026-09-22"

#: venue code -> (theatre page, {format: aliases})
PREMIUM_SCREENS: dict[str, tuple[str, dict[str, tuple[str, ...]]]] = {
    "PRHN": ("https://www.hyderabadtheatres.com/theaters/prasads-multiplex/",
             {"PCX": ("Pcx Screen",), "HDR By Barco": ()}),                     # Screen 6 (PCX) · Screen 4 (HDR by Barco)
    "ALUC": ("https://www.hyderabadtheatres.com/theaters/allu-cinemas/",
             {"Dolby Cinema": ()}),                                              # Dolby Cinema (Screen 1)
    "AMBH": ("https://www.hyderabadtheatres.com/theaters/amb-cinemas/",
             {"HDR By Barco": (), "MB LUXE": ("M B LUXE",), "VIP": ("Vip Screen",)}),   # Screens 1, 8, 5
    # AAA: Screen 2 is "Screen 2 (EPIQ LED)" on a Luxon LED wall — BookMyShow sells it as "Led Screen
    # Dolby Atmos". Screen 1 (4K laser, Dolby Atmos, large format) carries no premium name and is not EPIQ.
    "ACAS": ("https://www.hyderabadtheatres.com/theaters/aaa-cinemas/",
             {"EPIQ": ("Led Screen Dolby Atmos", "EPIQ LED")}),
    "ACEV": ("https://www.hyderabadtheatres.com/theaters/art-cinemas/",
             {"EPIQ": ()}),                                                      # Screen 6 (EPIQ)
    "ILKS": ("https://www.hyderabadtheatres.com/theaters/pvr-lakeshore-mall-y-junction/",
             {"PXL": ("P[XL]", "Pxl")}),                                         # Audi 6 (P[XL]), 32ch Atmos, 4K
    "PIIC": ("https://www.hyderabadtheatres.com/theaters/pvr-superplex-inorbit/",
             {"PXL": ("P[XL]", "Pxl"), "LUXE": (), "4DX": ()}),                   # Screens 11, 9/10, 8
    "PVFS": ("https://www.hyderabadtheatres.com/theaters/pvr-nexus-mall/",
             {"4DX": ()}),                                                       # Audi 5 (4DX)
    "PIMH": ("https://www.hyderabadtheatres.com/theaters/pvr-irrum-manzil/",
             {"4DX": ()}),                                                       # Audi 1 (4DX)
    "CTNR": ("https://www.hyderabadtheatres.com/theaters/cinepolis-tnr-north-city/",
             {"Macro XE": ("MacroXE", "Macro Xe")}),                             # Audi 7 (Macro XE)
    "APNS": ("https://www.hyderabadtheatres.com/theaters/aparna-cinemas-satamrai/",
             {"VIP": ("Vip Screen",)}),                                          # Screen 7 (VIP)
}

#: Strings that are technology or comfort, never a format — kept here so a
#: reviewer can see what was deliberately left out.
NOT_FORMATS = ("4K", "4K Laser", "2K", "Laser", "Dolby Atmos", "Atmos", "DTS:X", "Dolby 7.1",
               "Recliner Seats", "Luxury Seating", "AC", "Parking", "Food Court", "Play House", "Playhouse")


def premium_formats(venue_code: str) -> tuple[str, ...]:
    """The named premium screens a theatre has, or nothing when the source
    names none (or the theatre is not in it)."""
    entry = PREMIUM_SCREENS.get(venue_code)
    return tuple(entry[1]) if entry else ()


def format_aliases(venue_code: str, fmt: str) -> tuple[str, ...]:
    """BookMyShow's other names for this premium screen at this theatre."""
    entry = PREMIUM_SCREENS.get(venue_code)
    if not entry:
        return ()
    key = " ".join(fmt.lower().split())
    for name, aliases in entry[1].items():
        if " ".join(name.lower().split()) == key:
            return aliases
    return ()


def source_page(venue_code: str) -> str:
    entry = PREMIUM_SCREENS.get(venue_code)
    return entry[0] if entry else SOURCE


__all__ = ["NOT_FORMATS", "PREMIUM_SCREENS", "SOURCE", "VERIFIED", "format_aliases", "premium_formats",
           "source_page"]
