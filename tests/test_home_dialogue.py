"""Home's cinema dialogue: what the six opening marks say, and where the
subtitle card opens so it is never cut off.

Two contracts live here.

**The lines.** The first six marks a person meets on Home — the band's
first six, left to right, which are also the phone strip's first six —
carry six Telugu lines, each naming its hero and the film, in a fixed
order. None of those six heroes appears anywhere else among the other
eighteen, which are Marvel, Hollywood and exactly two Batman lines.

**The placement.** The card is CSS: a hover and a focus reveal a child
element, with no script, no timer and no rerun. What changed is only
*which way it opens* — the band hangs above the hero at the very top of
the document, so its cards open downward, where a card opening upward was
cut off by the top of the browser window.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

from ui import crafts, home

pytestmark = pytest.mark.usefixtures("signed_in")

#: The six, exactly as they must be said, in the order they are met.
SIX: tuple[tuple[str, str, str], ...] = (
    ("direction", "Thokkukuntu povaale!", "Jr NTR · RRR"),
    ("cinematography", "vandha mandhini okesari Ramannu!", "Ram Charan · Magadheera"),
    ("screenwriting", "Please... I kindly request!", "Prabhas · Salaar"),
    ("producing", "Pushpa... Pushpa Raj... Thaggede Le!", "Allu Arjun · Pushpa: The Rise"),
    ("acting", "Okka sari commit aithe... naa maata nene vinanu!", "Mahesh Babu · Pokiri"),
    ("editing", "Naakkonchem thikka undi... kaani daaniko lekkundi!", "Pawan Kalyan · Gabbar Singh"),
)

HEROES = ("Jr NTR", "Ram Charan", "Prabhas", "Allu Arjun", "Mahesh Babu", "Pawan Kalyan")
FILMS = ("RRR", "Magadheera", "Salaar", "Pushpa", "Pokiri", "Gabbar Singh")


def _marks(markup: str) -> list[str]:
    """Each mark's line, in the order the markup draws it."""
    return re.findall(r'<span class="tr-craft[^"]*"[^>]*aria-label="([^"]+)"', markup)


def _rule(css: str, selector: str) -> str:
    """The declarations of the first rule whose selector list holds ``selector``."""
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        if selector in m.group(1):
            return m.group(2)
    raise AssertionError(f"no rule for {selector!r}")


# ──────────────────────────────────────────────────────────────────────────
# The six, in order
# ──────────────────────────────────────────────────────────────────────────
def test_the_first_six_marks_drawn_on_home_are_the_six_heroes_in_order():
    """Not the dictionary's order — the order the page actually renders."""
    band = [k for k, (zone, _x, _y) in home.HOME_LAYOUT.items() if zone == "band"]
    assert band[:6] == [key for key, _q, _by in SIX]
    # …and the band is laid out left to right, so reading order is visual order
    xs = [home.HOME_LAYOUT[k][1] for k in band]
    assert xs == sorted(xs), xs
    # the band is the first zone drawn on the page
    import app

    page = __import__("inspect").getsource(app.page_home)
    assert page.index("home.band()") < page.index("home.rail_foot()") < page.index("home.foot()")
    # the phone's eight open with the same six, so 390px reads what 1600px reads
    assert home.STRIP[:6] == tuple(key for key, _q, _by in SIX)
    assert len(home.STRIP) == 8

    for markup in (home.crafts_zone("band"), home.crafts_strip()):
        drawn = _marks(markup)
        assert len(drawn) == 8
        for got, (key, quote, by) in zip(drawn, SIX):
            craft = crafts.BY_KEY[key]
            assert got == f"{craft.label} — “{quote}” — {by}", got


def test_the_six_strings_are_carried_through_exactly_as_written():
    for key, quote, by in SIX:
        assert home.LINES[key] == (quote, by, "Tollywood"), key
    body = home.crafts_zone("band") + home.crafts_strip()
    for _key, quote, by in SIX:
        assert f'<span class="q">“{quote}”</span><span class="by">— {by}</span>' in body, quote
    # transliterated, never Telugu script, and each names a hero and a film
    for _key, quote, by in SIX:
        assert quote.isascii(), quote
        hero, _, film = by.partition(" · ")
        assert hero in HEROES and film, by


# ──────────────────────────────────────────────────────────────────────────
# …and nowhere else
# ──────────────────────────────────────────────────────────────────────────
def test_none_of_the_six_heroes_speaks_again_lower_down():
    rest = {k: v for k, v in home.LINES.items() if k not in home.TELUGU_SIX}
    assert len(rest) == 18
    for key, (quote, by, house) in rest.items():
        assert house != "Tollywood", key
        for hero in HEROES:
            assert hero not in by, (key, by)
        for film in FILMS:
            assert film not in by and film not in quote, (key, by)


def test_the_houses_keep_their_balance_with_marvel_in_front():
    houses = Counter(house for _q, _by, house in home.LINES.values())
    assert sum(houses.values()) == 24 and len(home.LINES) == 24
    assert houses["Tollywood"] == 6
    assert houses["DC"] == 2
    assert houses["Marvel"] == 9 and houses.most_common(1)[0][0] == "Marvel"
    assert houses["Marvel"] > max(v for k, v in houses.items() if k != "Marvel")
    assert set(houses) == {"Marvel", "Tollywood", "DC", "Hollywood"}


def test_the_two_batman_lines_survived_the_swap():
    dc = [(q, by) for q, by, house in home.LINES.values() if house == "DC"]
    assert len(dc) == 2
    assert ("Why so serious?", "The Joker · The Dark Knight") in dc
    assert ("I'm Batman.", "Batman") in dc
    for quote, by in dc:
        assert len(quote) <= 30                                   # short and recognisable
        assert "Batman" in by or "Dark Knight" in by


# ──────────────────────────────────────────────────────────────────────────
# Placement: the card cannot be cut off
# ──────────────────────────────────────────────────────────────────────────
def test_every_band_card_opens_downward_because_the_band_is_above_the_hero():
    """The bug in the screenshot: a card opening upward from the band ran
    off the top of the browser window. The band sits at ``top:-108px``,
    above the hero — there is nothing above it to open into."""
    css = home.CSS
    assert ".tr-home-zone.band { position:absolute; left:0; right:30px; top:-108px;" in css
    band = home.crafts_zone("band")
    marks = re.findall(r'<span class="(tr-craft[^"]*)"', band)
    assert len(marks) == 8
    assert all("tip-b" in cls for cls in marks), marks
    # tip-b is what moves the card below the mark
    assert ".tr-craft.tip-b .tr-sub { bottom:auto; top:calc(100% + 12px); }" in css
    # the rail is in normal flow: only a mark near its own top edge drops below
    rail = re.findall(r'<span class="(tr-craft[^"]*)"', home.crafts_zone("rail"))
    below = [c for c in rail if "tip-b" in c]
    assert 0 < len(below) < 8, rail                               # some, not all
    for key, (z, x, y) in home.HOME_LAYOUT.items():
        if z == "rail":
            assert ("tip-b" in home._side(x, y, z, positioned=True)) == (y < 40), key


def test_no_foot_card_opens_downward_because_the_version_line_is_under_it():
    """The foot is the last thing on the page. A card opening downward there
    ran under ``.tr-version.tr-page-foot``, which comes later in the document
    and paints over it — so every foot card opens upward, where the page has
    room."""
    classes = re.findall(r'<span class="(tr-craft[^"]*)"', home.crafts_zone("foot"))
    assert len(classes) == 8
    assert not any("tip-b" in cls for cls in classes), classes
    for key, (zone, x, y) in home.HOME_LAYOUT.items():
        if zone == "foot":
            assert "tip-b" not in home._side(x, y, zone, positioned=True), key
    # …including the marks near the zone's top edge, which the rail would drop
    assert any(y < 40 for z, _x, y in home.HOME_LAYOUT.values() if z == "foot")


def test_a_mark_near_either_end_anchors_its_card_to_its_own_edge():
    assert home._side(4, 60, "band", positioned=True) == "tip-r tip-b"
    assert home._side(90, 60, "band", positioned=True) == "tip-l tip-b"
    assert home._side(50, 60, "foot", positioned=True) == ""
    for zone in ("band", "foot"):
        for key, (z, x, y) in home.HOME_LAYOUT.items():
            if z != zone:
                continue
            side = home._side(x, y, z, positioned=True)
            assert ("tip-r" in side) == (x < 22), key
            assert ("tip-l" in side) == (x > 78), key
            assert not ("tip-r" in side and "tip-l" in side), key
    css = home.CSS
    assert ".tr-craft.tip-r .tr-sub { left:-4px;" in css           # opens rightward from the mark
    assert ".tr-craft.tip-l .tr-sub { left:auto; right:-4px;" in css


def test_every_rail_card_opens_leftward_because_the_rail_hugs_the_window():
    """The rail is a narrow column against the right edge of the window
    (``minmax(320px, 1.1fr)``). A card centred on a mark there reached past
    the page — at 1600px the widest ran 46px past it — so they all open
    leftward, into the room the page already has."""
    assert f"minmax({home.RAIL_MIN}px, 1.1fr)" in home.CSS and home.RAIL_MIN >= 320
    classes = re.findall(r'<span class="(tr-craft[^"]*)"', home.crafts_zone("rail"))
    assert len(classes) == 8
    assert all("tip-l" in cls for cls in classes), classes
    assert not any("tip-r" in cls for cls in classes), classes
    for key, (zone, x, y) in home.HOME_LAYOUT.items():
        if zone == "rail":
            assert home._side(x, y, zone, positioned=True).startswith("tip-l"), key
    # the band and the foot run the width of their column and keep centring
    for zone in ("band", "foot"):
        classes = re.findall(r'<span class="(tr-craft[^"]*)"', home.crafts_zone(zone))
        centred = [c for c in classes if "tip-l" not in c and "tip-r" not in c]
        assert centred, zone


def test_a_card_can_never_be_wider_than_the_window():
    """No horizontal page scroll at any width: the card is as wide as its
    longest line and no wider than the viewport, so a long quote wraps."""
    sub = _rule(home.CSS, ".tr-craft .tr-sub")
    assert "width:max-content" in sub                              # short lines stay on one line, as before
    assert "max-width:min(380px, calc(100vw - 32px))" in sub       # …and nothing reaches past the page
    assert "white-space:normal" in sub and "white-space:nowrap" not in sub
    assert "position:absolute" in sub and "pointer-events:none" in sub


def test_the_tablet_band_anchors_its_cards_and_caps_them_to_the_room_it_has():
    """Between 769 and 1149px the band is squeezed to ``right:240px`` to clear
    the account chip, leaving it about 340px wide — narrower than a card. A
    centred card there reached left of the page's content, under the sidebar,
    and a wide one reached the chip."""
    css = home.CSS
    tablet = css[css.index("@media (min-width: 769px) and (max-width: 1149px)"):css.index("@media (min-width: 1150px)")]
    assert ".tr-home-zone.band { right:240px; }" in tablet
    assert ".tr-home-zone.band .tr-sub { max-width:200px; }" in tablet
    assert ".tr-home-zone.band .tr-craft:not(.tip-l) .tr-sub { left:0; right:auto;" in tablet
    for state in (":hover", ":focus", ":focus-visible"):
        assert f".tr-home-zone.band .tr-craft:not(.tip-l){state} .tr-sub" in tablet
    assert tablet.count("{") == tablet.count("}")                 # the block still closes


def test_the_phone_card_drops_below_the_strip_and_stays_in_the_window():
    phone = home.CSS[home.CSS.index("@media (max-width: 768px)"):]
    strip = _rule(phone, ".tr-home-strip .tr-craft .tr-sub")
    assert "right:0 !important" in strip                           # never off the right edge
    assert "bottom:auto !important" in strip and "top:calc(100% + 8px)" in strip   # …and never off the top
    assert "max-width:min(300px, calc(100vw - 28px))" in _rule(phone, ".tr-craft .tr-sub")
    assert ".tr-home-zone.band, .tr-home-zone.rail, .tr-home-zone.foot { display:none; }" in phone
    assert ".tr-home-strip { display:flex;" in phone               # the eight are still a strip
    assert len(re.findall(r'<span class="tr-craft', home.crafts_strip())) == 8


# ──────────────────────────────────────────────────────────────────────────
# It stays free: CSS only, no script, no rerun, nothing new to wait for
# ──────────────────────────────────────────────────────────────────────────
def test_the_card_is_revealed_by_hover_and_focus_alone():
    css = home.CSS
    assert ".tr-craft:hover .tr-sub, .tr-craft:focus-visible .tr-sub, .tr-craft:focus .tr-sub { opacity:1;" in css
    mark = home._mark(crafts.BY_KEY["acting"], 50, 50, zone="band")
    assert 'tabindex="0"' in mark and 'role="img"' in mark         # reachable by keyboard
    assert "onmouseover" not in mark and "onclick" not in mark and "<script" not in mark
    src = Path("ui/home.py").read_text(encoding="utf-8")
    for forbidden in ("<script", "setTimeout", "setInterval", "requestAnimationFrame",
                      "time.sleep", "sleep(", "st.rerun", "import streamlit", "requests", "urlopen"):
        assert forbidden not in src.split('"""', 2)[2], forbidden


def test_the_line_is_carried_once_not_three_times():
    """Home turns the constellation's ``data-tip`` pseudo-element off, so a
    ``data-tip`` copy of the sentence would be markup nobody ever reads."""
    css = home.CSS
    assert (".tr-home-zone .tr-craft::after, .tr-home-zone .tr-craft::before, "
            ".tr-home-strip .tr-craft::after, .tr-home-strip .tr-craft::before { display:none; }") in css
    for markup in (home.crafts_zone("band"), home.crafts_zone("rail"),
                   home.crafts_zone("foot"), home.crafts_strip()):
        assert "data-tip" not in markup
    # the icon's shared attributes moved to the stylesheet too
    assert ".tr-craft .tr-ic { width:17px; height:17px; fill:none; stroke:currentColor; stroke-width:1.7;" in css
    icon = home._icon(crafts.BY_KEY["acting"])
    assert icon.startswith('<svg class="tr-ic" viewBox="0 0 24 24" aria-hidden="true">')
    for attr in ("stroke-width=", "stroke-linecap=", "fill=", "width=", "height="):
        assert attr not in icon, attr
    assert crafts.BY_KEY["acting"].paths in icon                   # the drawing itself is untouched
    # Login still writes them out: it has no stylesheet of Home's
    assert 'stroke-width="1.7"' in crafts.icon(crafts.BY_KEY["acting"])


def test_the_whole_layer_is_smaller_than_it_was_and_draws_the_same_marks():
    zones = home.crafts_zone("band") + home.crafts_zone("rail") + home.crafts_zone("foot")
    assert len(_marks(zones)) == 24 and len(_marks(home.crafts_strip())) == 8
    assert len(zones) + len(home.crafts_strip()) < 18000           # was ~21.5 KB on every rerun
    labels = {line.split(" — ")[0] for line in _marks(zones)}
    assert labels == {c.label for c in crafts.CRAFTS}              # every craft still there, by its own name
