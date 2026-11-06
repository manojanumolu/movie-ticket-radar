"""The three content collections: Home's world cinema, the Explorer's recent
Indian cinema, and the 24-format directory.

Lines are the original words in Roman letters with credits, never a
translation or a regional script; the two dialogue collections never mix and
never repeat one of Home's 24 dialogue marks; the format directory keeps its
categories honest and reads the Hyderabad data without changing it.
"""

from __future__ import annotations

import subprocess

from config.theatre_capabilities import PREMIUM_SCREENS
from content import explorer_dialogues, home_dialogues
from content import format_directory as fd
from ui import crafts, home

HOME = home_dialogues.DIALOGUES
EXPLORER = explorer_dialogues.DIALOGUES


def _latin(text: str) -> bool:
    """Roman letters only (with Latin accents and punctuation): no Telugu,
    Devanagari, Tamil, Malayalam, Kannada, Bengali, Hangul or kana."""
    return all(ord(ch) < 0x0250 or ch in "…“”«»’" or 0x2000 <= ord(ch) <= 0x206F for ch in text)


def test_every_line_is_roman_letters_with_full_credits():
    for d in HOME + EXPLORER:
        assert _latin(d.line), d.line
        assert d.film and 1930 < d.year <= 2026 and d.speaker and d.language and d.source, d
        assert d.status == "verified", d
        assert d.credit == f"{d.film} · {d.year} · {d.speaker} · {d.language}"


def test_no_translation_field_exists_or_is_shown():
    from content import Dialogue

    assert "english" not in Dialogue.__dataclass_fields__ and "translation" not in Dialogue.__dataclass_fields__
    for html in (home.hero_lines(),):
        assert "English</b>" not in html and 'class="en"' not in html


def test_six_telugu_stars_six_different_stars_none_on_the_24_marks():
    six = home_dialogues.TELUGU_STARS
    assert len(six) == 6 and all(d.language == "Telugu" for d in six)
    stars = {d.speaker.split("(")[-1].rstrip(")") for d in six}
    assert len(stars) == 6
    marked = {q for q, _by, _h in home.LINES.values()}
    assert not {d.line for d in six} & marked
    assert HOME[:6] == six                                                    # they open the rotation


def test_home_is_world_cinema_and_explorer_is_recent_indian_and_they_never_mix():
    home_langs = {d.language for d in HOME}
    assert {"Telugu", "Hindi", "Tamil", "Malayalam", "Bengali", "English", "Korean", "French", "Spanish", "Italian"} <= home_langs
    marvel = {"Avengers: Endgame", "Captain America: The First Avenger", "Doctor Strange", "The Avengers",
              "Avengers: Infinity War"}
    assert sum(d.film in marvel for d in HOME) >= 5
    marked = {q for q, _by, _h in home.LINES.values()}
    assert not {d.line for d in HOME} & marked                                # never a mark's line
    assert all(d.year >= 2022 for d in EXPLORER)
    assert not {d.line for d in HOME} & {d.line for d in EXPLORER}
    assert not {d.film for d in HOME} & {d.film for d in EXPLORER}
    import ui.explorer as ux

    assert ux.LINES is EXPLORER and home.HERO_LINES is HOME


def test_the_directory_covers_the_24_marks_with_24_distinct_formats():
    assert set(fd.BY_CRAFT) == set(crafts.BY_KEY)
    assert len({f.name for f in fd.BY_CRAFT.values()}) == 24
    for f in fd.BY_CRAFT.values():
        assert f.category in fd.CATEGORIES and f.group in {fd.INDIAN, fd.INTERNATIONAL}
        assert f.description.endswith(".") and len(f.description) <= 200 and f.source


def test_the_categories_say_what_each_thing_is():
    by_name = {f.name: f for f in fd.BY_CRAFT.values()}
    for name in ("Dolby Atmos", "DTS:X", "Auro 11.1"):
        assert by_name[name].category == fd.SOUND and "not a screen" in by_name[name].description
    for name in ("4DX", "MX4D", "D-BOX"):
        assert by_name[name].category == fd.MOTION
    assert by_name["RealD 3D"].category == fd.THREE_D
    for name in ("HDR by Barco", "ONYX", "CINITY"):
        assert by_name[name].category == fd.PROJECTION
    for name in ("LUXE", "INSIGNIA", "MB LUXE", "Director's Cut"):
        assert by_name[name].category == fd.LUXURY
    assert by_name["VIP"].category == fd.SEATING and "theatre-specific" in by_name["VIP"].description
    assert by_name["Infinity Vision"].category == fd.LABEL
    assert "BookMyShow" in by_name["Infinity Vision"].description and "not a physical screen" in by_name["Infinity Vision"].description


def test_hyderabad_is_claimed_only_where_the_theatre_data_says_so():
    named = {fd._norm(f) for _p, screens in PREMIUM_SCREENS.values() for f in screens}
    for f in fd.BY_CRAFT.values():
        claimed = fd.availability(f) == "Named in TicketRadar's Hyderabad theatre data"
        assert claimed == (fd._norm(f.match or f.name) in named), f.name
    by_name = {f.name: f for f in fd.BY_CRAFT.values()}
    assert not fd.in_hyderabad_data(by_name["IMAX"]) and not fd.in_hyderabad_data(by_name["Infinity Vision"])
    for f in fd.BY_CRAFT.values():
        for word in ("now showing", "currently", "book now"):
            assert word not in (f.description + fd.availability(f)).lower()


def test_the_theatre_data_and_monitoring_are_untouched():
    # explorer/ (the Explorer's own data layer) is left out: later Explorer
    # features extend it. Theatre data, monitoring, the BookMyShow provider,
    # auth, notifications, Login, theme, crafts, flow and the workflows are not.
    # pinned to the content feature's own range (f6b73dd..e0e4661): what that change touched, for
    # good — later fixes to monitoring or the provider are reviewed on their own
    changed = subprocess.run(["git", "diff", "--name-only", "f6b73dd", "e0e4661", "--", "config/", "monitor/", "platforms/",
                              "auth/", "notifications/", "ui/login.py", "ui/theme.py", "ui/crafts.py",
                              "ui/flow.py", ".github/"], capture_output=True, text=True, check=True).stdout
    assert changed.strip() == ""
