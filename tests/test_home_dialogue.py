"""Home's cinema dialogue: what each mark says, and where the subtitle card
opens so it is never cut off.

Two contracts live here.

**The lines.** Every mark carries one entry of the audited collection
(``ui.dialogues``): the line, its English translation when it is not in
English, and the speaker, film, year and language. The band — the first zone
drawn, and the first eight of the phone's strip — is Indian cinema, Telugu
first; the rail is world cinema in its own languages; the foot is Hollywood.
A subtitle or a paraphrase says so on the card.

**The placement.** The card is CSS: a hover, a focus and a tap (which
focuses the mark) reveal a child element, with no script, no timer and no
rerun. The band hangs above the hero at the very top of the document, so its
cards open downward.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ui import crafts, dialogues, home

pytestmark = pytest.mark.usefixtures("signed_in")

INDIAN = {"Telugu", "Hindi", "Tamil", "Malayalam", "Kannada · said in English", "Bengali"}


def _marks(markup: str) -> list[str]:
    """Each mark's line, in the order the markup draws it."""
    return re.findall(r'<span class="tr-craft[^"]*"[^>]*aria-label="([^"]+)"', markup)


def _rule(css: str, selector: str) -> str:
    """The declarations of the first rule whose selector list holds ``selector``."""
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        if selector in m.group(1):
            return m.group(2)
    raise AssertionError(f"no rule for {selector!r}")


def _zone(name: str) -> list[str]:
    return [k for k, (z, _x, _y) in home.HOME_LAYOUT.items() if z == name]


# ──────────────────────────────────────────────────────────────────────────
# The lines: India first, then the world, then Hollywood
# ──────────────────────────────────────────────────────────────────────────
def test_every_mark_carries_one_audited_entry_and_none_repeats():
    assert set(home.LINES) == set(crafts.BY_KEY)
    assert len(set(home.LINES.values())) == 24                       # 24 different lines
    assert set(home.LINES.values()) <= set(dialogues.BY_KEY)


def test_the_band_is_indian_cinema_telugu_first_and_the_strip_reads_the_same():
    band = _zone("band")
    langs = [dialogues.BY_KEY[home.LINES[k]].language for k in band]
    assert set(langs) <= INDIAN and langs[:4] == ["Telugu"] * 4, langs
    xs = [home.HOME_LAYOUT[k][1] for k in band]
    assert xs == sorted(xs), xs                                      # reading order is visual order
    import app

    page = __import__("inspect").getsource(app.page_home)
    assert page.index("home.band()") < page.index("home.rail_foot()") < page.index("home.foot()")
    assert home.STRIP[:8] == tuple(band) and len(home.STRIP) == 24


def test_the_rail_is_world_cinema_in_its_own_languages_and_the_foot_is_hollywood():
    rail = {dialogues.BY_KEY[home.LINES[k]].language for k in _zone("rail")}
    assert "English" not in rail and len(rail) >= 6, rail
    assert {"French", "Italian", "Japanese", "Korean", "Spanish", "Mandarin"} <= rail
    foot = [dialogues.BY_KEY[home.LINES[k]] for k in _zone("foot")]
    assert all(d.language == "English" and d.region == "World" for d in foot)


def test_a_card_carries_line_translation_attribution_and_its_label():
    mark = home._mark(crafts.BY_KEY["acting"], 50, 50, zone="band")         # Sholay
    assert ('<span class="q">“Kitne aadmi the?”</span><span class="en">How many men were there?</span>'
            '<span class="by">— Gabbar Singh (Amjad Khan) · Sholay (1975) · Hindi</span>') in mark
    assert 'aria-label="Acting — “Kitne aadmi the?” (How many men were there?) — Gabbar Singh (Amjad Khan), Sholay (1975) · Hindi"' in mark
    sub = home._mark(crafts.BY_KEY["producing"], 50, 50, zone="band")       # Baahubali 2: a subtitle
    assert '<span class="note">English subtitle</span>' in sub and "[English subtitle]" in sub
    english = home._mark(crafts.BY_KEY["sound"], 50, 50, zone="foot")       # Casablanca: no translation
    assert 'class="en"' not in english and "Casablanca (1942) · English" in english


def test_the_strings_are_escaped_on_the_way_out(monkeypatch):
    evil = dialogues.Dialogue("x", '<b>"hi"</b>', "<i>t</i>", "<s>F</s>", 2000, "Telugu", "<u>S</u>", "India")
    monkeypatch.setitem(dialogues.BY_KEY, home.LINES["acting"], evil)
    mark = home._mark(crafts.BY_KEY["acting"], 50, 50, zone="band")
    for raw in ("<b>", "<i>", "<s>", "<u>"):
        assert raw not in mark
    assert "&lt;b&gt;" in mark and "&lt;i&gt;" in mark


def test_the_rotation_under_the_hero_alternates_india_and_the_world():
    keys = dialogues.HOME_ROTATION
    regions = [dialogues.BY_KEY[k].region for k in keys]
    assert regions == ["India", "World"] * (len(keys) // 2)
    markup = home.dialogue()
    assert markup.startswith('<div class="tr-dlg tr-dlg-home" aria-hidden="true">')
    delays = [int(d) for d in re.findall(r"animation-delay:(\d+)s", markup)]
    assert delays == [i * dialogues.LINE_SECONDS for i in range(len(keys))]
    css = home.CSS
    assert f".tr-dlg-home .ln {{ animation: tr-dlg-home-turn {len(keys) * dialogues.LINE_SECONDS}s linear infinite; }}" in css


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
    assert "bottom:auto" in strip and "top:calc(100% + 6px)" in strip   # …and never off the top
    # anchored to the box around the strip, not to the mark: the strip scrolls
    # and clips, so a card on the mark was cut off at the screen's edge
    assert "left:0; right:0;" in strip and "max-width:none" in strip
    assert ".tr-home-stripbox { position:relative;" in phone
    assert "position:static" in _rule(phone, ".tr-home-strip .tr-craft")
    assert ".tr-home-strip {{ display:flex; position:static;".replace("{{", "{") in phone
    assert home.crafts_strip().startswith('<div class="tr-home-stripbox"><div class="tr-home-strip"')
    assert "max-width:min(300px, calc(100vw - 28px))" in _rule(phone, ".tr-craft .tr-sub")
    assert ".tr-home-zone.band, .tr-home-zone.rail, .tr-home-zone.foot { display:none; }" in phone
    assert ".tr-home-strip { display:flex;" in phone               # the crafts are a strip
    assert len(re.findall(r'<span class="tr-craft', home.crafts_strip())) == 24


# ──────────────────────────────────────────────────────────────────────────
# It stays free: CSS only, no script, no rerun, nothing new to wait for
# ──────────────────────────────────────────────────────────────────────────
def test_the_card_is_revealed_by_hover_and_focus_alone():
    css = home.CSS
    assert ".tr-craft:hover .tr-sub, .tr-craft:focus-visible .tr-sub, .tr-craft:focus .tr-sub { opacity:1;" in css
    mark = home._mark(crafts.BY_KEY["acting"], 50, 50, zone="band")
    assert 'tabindex="0"' in mark and 'role="img"' in mark         # reachable by keyboard, and by a tap
    # a tap focuses the mark, and focus opens the card and answers with one nod
    assert ".tr-home-zone .tr-craft:focus, .tr-home-strip .tr-craft:focus { opacity:1;" in css
    assert "animation: tr-home-nod .5s var(--tr-ease) 1;" in css                    # once, never a loop
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
    assert len(_marks(zones)) == 24 and len(_marks(home.crafts_strip())) == 24
    assert len(zones) + len(home.crafts_strip()) < 32000      # each card now carries its translation and year
    labels = {line.split(" — ")[0] for line in _marks(zones)}
    assert labels == {c.label for c in crafts.CRAFTS}              # every craft still there, by its own name


def test_an_open_mark_and_its_card_are_fully_opaque():
    """The zone's resting ``opacity:.42`` outranked the constellation's
    ``:hover`` rule, and a card inherits its mark's opacity — so every
    desktop card was drawn at 42%, with the hero's title showing through."""
    css = home.CSS
    assert (".tr-home-zone .tr-craft:hover, .tr-home-zone .tr-craft:focus, "
            ".tr-home-zone .tr-craft:focus-visible { opacity:1;") in css
