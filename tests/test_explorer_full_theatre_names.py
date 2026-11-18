"""Showtime Explorer — a theatre's full official name on the first line, its
locality alone on the second, the way BookMyShow lists venues.

The problem: By-movie rows and group headings printed only the part of
BookMyShow's venue name before the colon — the chain ("PVR") — with the rest
of the name on the second line ("Atrium Gachibowli"). The theatre's own name
was split across two lines and the first line said only "PVR".

BookMyShow publishes one venue name ("PVR: Atrium Gachibowli, Hyderabad").
``platforms.bookmyshow.split_venue_name`` splits it at the colon into the
catalogue's ``name`` / ``area``; nothing is lost, so the full name is rebuilt
from those two fields by ``explorer.classify.venue_display`` — no ingestion
change. Only trailing place names (and the city) move to the second line.

Every venue below is spelled exactly as data/catalogue.json holds it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from explorer import catalog
from explorer import query as Q
from explorer import source as S
from explorer.classify import classify, operator_of, venue_display
from monitor.models import Availability, MovieRef, Showtime, Venue
from platforms.bookmyshow import split_venue_name
from ui import explorer as X

#: (BookMyShow's venue name as published, expected first line, expected second line)
CASES = [
    ("PVR: Preston, Gachibowli Hyderabad", "PVR: Preston", "Gachibowli"),
    ("PVR: Atrium Gachibowli, Hyderabad", "PVR: Atrium", "Gachibowli"),
    ("PVR: Next Galleria Mall, Panjagutta", "PVR: Next Galleria Mall", "Panjagutta"),
    ("PVR: Central Mall, Panjagutta", "PVR: Central Mall", "Panjagutta"),
    ("PVR: Nexus Mall Kukatpally, Hyderabad", "PVR: Nexus Mall", "Kukatpally"),
    ("PVR Lakeshore PXL 4K Laser ATMOS DTS-X Y Junction",
     "PVR Lakeshore PXL 4K Laser ATMOS DTS-X Y Junction", "Hyderabad"),
    ("PVR ICON: Hitech, Madhapur, Hyderabad", "PVR ICON: Hitech", "Madhapur"),
    ("PVR Superplex Inorbit: LUXE, PXL, 4DX: Cyberabad", "PVR Superplex Inorbit: LUXE, PXL, 4DX", "Cyberabad"),
    ("PVR: Musarambagh, Hyderabad", "PVR: Musarambagh", "Musarambagh"),
    ("PVR: Irrum Manzil, Hyderabad", "PVR: Irrum Manzil", "Irrum Manzil"),
    ("PVR: RK Cineplex, Hyderabad", "PVR: RK Cineplex", "Hyderabad"),
    # BookMyShow's own Miraj Miyapur name carries "CineTown"; it is kept.
    ("Miraj Cinemas: CineTown, Miyapur", "Miraj Cinemas: CineTown", "Miyapur"),
    ("Miraj Cinemas: Geeta, Chandanagar", "Miraj Cinemas: Geeta", "Chandanagar"),
    ("INOX: Odeon 4K, LASER, ATMOS, DTS-X: RTC X Roads", "INOX: Odeon 4K, LASER, ATMOS, DTS-X", "RTC X Roads"),
    ("INOX: SMR Vinay Metro Mall, Dolby ATMOS: Miyapur", "INOX: SMR Vinay Metro Mall, Dolby ATMOS", "Miyapur"),
    ("Cinepolis: TNR North City, Suchitra, Hyderabad", "Cinepolis: TNR North City", "Suchitra"),
    ("Cinepolis: Lulu Mall, Hyderabad", "Cinepolis: Lulu Mall", "Hyderabad"),
    ("CONNPLEX Luxuriance Cinemas: Mpm Mall, Banjara Hil", "CONNPLEX Luxuriance Cinemas: Mpm Mall",
     "Banjara Hills"),
    ("MovieMax: AMR, ECIL Secunderabad", "MovieMax: AMR", "ECIL, Secunderabad"),
    ("ROONGTA CINEMAS: NOVUM, NAMPALLY", "ROONGTA CINEMAS: NOVUM", "Nampally"),
    ("INOX GVK One, Banjara Hills: Hyderabad", "INOX GVK One, Banjara Hills", "Banjara Hills"),
    # single screens: the name is the theatre, the tail its locality
    ("Sudarshan 35MM 4k Laser & Dolby Atmos: RTC X Roads",
     "Sudarshan 35MM 4k Laser & Dolby Atmos: RTC X Roads", "RTC X Roads"),
    ("AMB Cinemas: Gachibowli", "AMB Cinemas: Gachibowli", "Gachibowli"),
    # words after the place are part of the name: nothing is cut from the middle
    ("Platinum Movietime Cinema: Gachibowli SLN Terminus",
     "Platinum Movietime Cinema: Gachibowli SLN Terminus", "Gachibowli"),
    ("Prashant Cinema: Secunderabad (Newly Renovated)",
     "Prashant Cinema: Secunderabad (Newly Renovated)", "Secunderabad"),
]


def display(full: str) -> tuple[str, str]:
    return venue_display(*split_venue_name(full))


# ── the data transformation ────────────────────────────────────────────
@pytest.mark.parametrize("full, title, where", CASES, ids=[c[1] for c in CASES])
def test_full_name_and_locality(full, title, where):
    assert display(full) == (title, where)


@pytest.mark.parametrize("full, title, where", CASES, ids=[c[1] for c in CASES])
def test_the_full_name_is_never_replaced_by_the_chain(full, title, where):
    shown, _ = display(full)
    head, tail = split_venue_name(full)
    chain = operator_of(head)
    assert shown.startswith(head)                       # the brand prefix, exactly as written
    if re.sub(r"[\s,]*hyderabad[\s.,]*$", "", tail, flags=re.IGNORECASE):
        assert shown != head and shown != chain         # more than the brand alone


def test_a_venue_with_no_distinguishing_name_keeps_what_bookmyshow_gave():
    assert venue_display("Prasads Multiplex", "Hyderabad") == ("Prasads Multiplex", "Hyderabad")
    assert venue_display("Aradhana Theatre", "Hyderabad") == ("Aradhana Theatre", "Hyderabad")
    assert venue_display("PVR", "") == ("PVR", "Hyderabad")          # nothing invented for it


def test_missing_or_incomplete_locality_is_never_invented():
    assert venue_display("Some Hall", "") == ("Some Hall", "Hyderabad")
    assert venue_display("Some Hall", "Hyderabad") == ("Some Hall", "Hyderabad")
    assert venue_display("Some Hall", "Unknown Road") == ("Some Hall: Unknown Road", "Hyderabad")
    assert venue_display("", "") == ("", "Hyderabad")
    # an unsplit name with a colon is split the same way the catalogue would
    assert venue_display("PVR: Atrium Gachibowli, Hyderabad", "") == ("PVR: Atrium", "Gachibowli")


def test_whitespace_is_tidied_without_changing_the_words():
    assert venue_display("  PVR ", "  Central   Mall,  Panjagutta ") == ("PVR: Central Mall", "Panjagutta")


def test_the_second_line_never_repeats_the_whole_address():
    for full, _, where in CASES:
        assert "," not in where or where == "ECIL, Secunderabad"
        assert "Hyderabad" not in where or where == "Hyderabad"


# ── the real synced catalogue ──────────────────────────────────────────
def catalogue_venues() -> dict[str, Venue]:
    data = json.loads(Path("data/catalogue.json").read_text(encoding="utf-8"))
    return {v["code"]: Venue(v["code"], v["name"], v["area"]) for m in data["movies"] for v in m["venues"]}


def test_every_catalogue_venue_has_a_unique_full_name():
    venues = catalogue_venues()
    if not venues:
        pytest.skip("the catalogue has no venues")
    titles: dict[str, str] = {}
    for code, v in venues.items():
        title, where = venue_display(v.name, v.area)
        assert title and where, code
        assert title.startswith(v.name), (code, title)             # the brand prefix kept
        assert titles.setdefault(title, code) == code, (title, titles[title], code)
    # the chains with many branches in one city, each told apart on line one
    pvr = [venue_display(v.name, v.area)[0] for v in venues.values() if v.name == "PVR"]
    assert pvr and all(t.startswith("PVR: ") and len(t) > len("PVR: ") for t in pvr)


# ── rendered output ────────────────────────────────────────────────────
FILM = MovieRef(platform="bookmyshow", event_code="ET00525065", title="Scene", region_code="HYD",
                region_slug="hyderabad", city="Hyderabad", language="Telugu",
                source_url="https://in.bookmyshow.com/movies/hyderabad/scene/buytickets/ET00525065/20261118")

#: Two PVRs in Gachibowli, two in Panjagutta, and the long single-name venue.
VENUES = [Venue("PVTS", *split_venue_name("PVR: Atrium Gachibowli, Hyderabad")),
          Venue("PVTP", *split_venue_name("PVR: Preston, Gachibowli Hyderabad")),
          Venue("PNNG", *split_venue_name("PVR: Next Galleria Mall, Panjagutta")),
          Venue("PVYH", *split_venue_name("PVR: Central Mall, Panjagutta")),
          Venue("ILKS", *split_venue_name("PVR Lakeshore PXL 4K Laser ATMOS DTS-X Y Junction")),
          Venue("MMCA", *split_venue_name("Miraj Cinemas: CineTown, Miyapur"))]


def shows() -> list[Q.ExplorerShow]:
    day = SimpleNamespace(venues=VENUES, showtimes=[
        Showtime(v.code, v.name, f"S{v.code}", "20261118", "", f"{10 + i:02d}00", "2D", Availability.AVAILABLE)
        for i, v in enumerate(VENUES)])
    return S.shows_from_day(day, FILM, "bookmyshow:ET00525065")


def rows(html: str) -> list[tuple[str, str]]:
    return re.findall(r'<div class="n">([^<]*)</div><div class="s">([^<]*)</div>', html)


def test_by_movie_rows_show_name_then_locality():
    html = X.time_rows_markup(shows(), "movie")
    assert rows(html) == [
        ("PVR: Atrium", "Gachibowli"), ("PVR: Preston", "Gachibowli"),
        ("PVR: Next Galleria Mall", "Panjagutta"), ("PVR: Central Mall", "Panjagutta"),
        ("PVR Lakeshore PXL 4K Laser ATMOS DTS-X Y Junction", "Hyderabad"),
        ("Miraj Cinemas: CineTown", "Miyapur"),
    ]
    assert '<div class="n">PVR</div>' not in html


def test_grouped_headings_show_name_then_locality():
    html = X.group_markup(Q.group_by_theatre(shows()), "movie")
    heads = [(n, s.split(" · ")[0]) for n, s in rows(html)]
    assert heads[:2] == [("PVR: Atrium", "Gachibowli"), ("PVR: Preston", "Gachibowli")]
    assert ("PVR Lakeshore PXL 4K Laser ATMOS DTS-X Y Junction", "Hyderabad") in heads
    assert '<div class="n">PVR</div>' not in html


def test_duplicate_chains_in_one_locality_stay_separate_with_their_own_links():
    out = shows()
    groups = Q.group_by_theatre(out)
    assert [g.key for g in groups] == [v.code for v in VENUES]
    assert len({g.title for g in groups}) == len(groups)
    urls = {s.venue_code: s.booking_url for s in out}
    assert len(set(urls.values())) == len(urls)                          # one link per theatre
    for code, url in urls.items():
        assert url.startswith("https://in.bookmyshow.com/") and code in url
    # the data fields the rest of the app reads are BookMyShow's, unchanged
    atrium = next(s for s in out if s.venue_code == "PVTS")
    assert (atrium.venue_name, atrium.area) == ("PVR", "Atrium Gachibowli, Hyderabad")
    assert (atrium.operator, atrium.locality) == ("PVR", "Gachibowli")


def test_the_area_filter_is_unchanged():
    out = shows()
    assert {s.venue_code for s in out if s.locality == "Gachibowli"} == {"PVTS", "PVTP"}
    assert {s.venue_code for s in out if s.locality == "Panjagutta"} == {"PNNG", "PVYH"}


def test_the_by_theatre_picker_uses_the_same_names():
    def theatre(code, full):
        name, area = split_venue_name(full)
        return catalog.Theatre(code=code, name=name, area=area, facts=classify(name, area))

    assert theatre("PVTS", "PVR: Atrium Gachibowli, Hyderabad").label == "PVR: Atrium · Gachibowli"
    assert theatre("PVTP", "PVR: Preston, Gachibowli Hyderabad").label == "PVR: Preston · Gachibowli"
    assert theatre("AMBH", "AMB Cinemas: Gachibowli").label == "AMB Cinemas: Gachibowli"
    assert theatre("ILKS", "PVR Lakeshore PXL 4K Laser ATMOS DTS-X Y Junction").label == (
        "PVR Lakeshore PXL 4K Laser ATMOS DTS-X Y Junction · Hyderabad")
    assert theatre("PVTS", "PVR: Atrium Gachibowli, Hyderabad").title == "PVR: Atrium"


def test_every_catalogue_picker_label_is_unique():
    venues = catalogue_venues()
    if not venues:
        pytest.skip("the catalogue has no venues")
    data = json.loads(Path("data/catalogue.json").read_text(encoding="utf-8"))
    labels = [t.label for t in catalog.theatres(data["movies"])]
    assert len(labels) == len(set(labels)) == len(venues)
