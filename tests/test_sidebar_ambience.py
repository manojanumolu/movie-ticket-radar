"""The rail's two reels — and nothing wound between them.

The composition: a large reel at the upper left (behind the logo's lower
edge and the first navigation rows) and a smaller reel at the lower right
(in the empty rail under the version line). The curved film strip that once
ran between them is gone — no line, track, ribbon or curve replaces it. The
reels are Home's own drawing (``ui.home.reel_svg``), each on a dark disc;
flat 2D, no glow, no gloss, no filter, and still: decoration in a navigation
rail does not move while a person reads it. ``ui.theme.RAIL_RIG`` places
them; the markup rides in the footer's html block.
"""

from __future__ import annotations

import re

import pytest

from tests.test_app import run, seeded  # noqa: F401
from ui import home, theme

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

#: Where the rail's words sit, measured in Chromium on a 244px rail.
LOGO_BOTTOM = 136
VERSION_BOTTOM = 563
RAIL = 244


def ambience_block() -> str:
    css = theme.CSS
    start = css.index("/* Sidebar ambience")
    end = css.index("/* the rail's own content stays above the room")
    return re.sub(r"/\*.*?\*/", "", css[start:end], flags=re.S)


def rules(css: str) -> dict[str, str]:
    out = {}
    for m in re.finditer(r"([^{}@]+)\{([^{}]*)\}", css):
        selector = m.group(1).strip()
        if selector and not selector.startswith(("0%", "50%", "100%", "from", "to")):
            out.setdefault(selector, "")
            out[selector] += m.group(2)
    return out


def sidebar_html(app) -> str:
    return "".join(m.value for m in app.sidebar.markdown)


def centre(reel: dict) -> tuple[float, float]:
    return reel["left"] + reel["size"] / 2, reel["top"] + reel["size"] / 2


# ── 1 · The markup ─────────────────────────────────────────────────────────
def test_the_rail_carries_two_reels_on_discs_and_no_film(seeded):
    app = run()
    side = sidebar_html(app)
    assert side.count('<div class="tr-side-ambience" aria-hidden="true">') == 1
    rig = side[side.index('<div class="tr-side-ambience"'):]
    assert home.reel_svg("top") in rig and home.reel_svg("bottom") in rig
    assert rig.count('<svg class="tr-reel') == 2
    assert rig.count('class="tr-disc top"') == 1 and rig.count('class="tr-disc bottom"') == 1
    assert rig.index("tr-disc top") < rig.index("tr-reel top") < rig.index("tr-reel bottom")
    foot = next(m.value for m in app.sidebar.markdown if "tr-side-foot" in m.value)
    assert "tr-side-ambience" in foot                       # no element of its own in the rail
    reel = home.reel_svg("top")
    assert reel.count("<circle") == 6 + 5 and 'class="hub"' in reel    # a real reel, with its cut-outs


def test_the_film_strip_is_gone_and_nothing_replaces_it(seeded):
    rig = home.rail_rig()
    for gone in ("tr-film", "<path", "<line", "<polyline", "perf", "frames"):
        assert gone not in rig, gone
    assert set(theme.RAIL_RIG) == {"top", "bottom", "phone_shift"}
    assert not hasattr(home, "rail_film_svg")
    block = ambience_block()
    assert "tr-film" not in block and "tr-side-film" not in theme.CSS and "stroke-dashoffset" not in block


# ── 2 · Composition: large reel upper left, small reel lower right ─────────
def test_the_composition_follows_the_reference():
    top, bottom = theme.RAIL_RIG["top"], theme.RAIL_RIG["bottom"]
    assert top["size"] > bottom["size"]
    (tx, _ty), (bx, _by) = centre(top), centre(bottom)
    assert tx < RAIL / 3 and bx > RAIL / 2                                # left, then right
    assert LOGO_BOTTOM - 10 < top["top"] < 200 and bottom["top"] > VERSION_BOTTOM + 10
    assert top["left"] + top["size"] <= RAIL and bottom["left"] + bottom["size"] <= RAIL
    block = rules(ambience_block())
    for name, reel in (("top", top), ("bottom", bottom)):
        assert (f"left: {reel['left']}px; top: {reel['top']}px; width: {reel['size']}px; height: {reel['size']}px"
                in block[f".tr-side-ambience .tr-reel.{name}"])
    for part in ("rim", "ring", "holes", "hub"):
        opacity = float(re.search(r"opacity: (\.\d+)", block[f".tr-side-ambience .tr-reel .{part}"]).group(1))
        assert opacity <= 0.22, part                                      # restrained
    assert "opacity: 0" in block[".tr-side-ambience .tr-reel .sheen"]    # flat: no gloss


# ── 3 · Still, flat, no filter ─────────────────────────────────────────────
def test_the_reels_stand_still_and_flat():
    block = ambience_block()
    assert "animation" not in block and "@keyframes" not in block and "will-change" not in block
    assert "tr-side-reel" not in theme.CSS
    assert "filter" not in block and "drop-shadow" not in block and "box-shadow" not in block
    for selector in rules(block):
        assert selector.startswith((".tr-side-ambience", '[data-testid="stSidebar"]')), selector


# ── 4 · Out of the way: behind every word, fixed, clipped, click-through ───
def test_the_rig_is_behind_every_word_and_out_of_the_way():
    wrapper = rules(ambience_block())[".tr-side-ambience"]
    assert "position: fixed; left: 0; top: 0; width: 100%; height: 100%" in wrapper
    assert "overflow: hidden" in wrapper and "pointer-events: none" in wrapper
    # -1, not 0: a positioned layer at 0 paints over the rail's unpositioned words
    assert "z-index: -1" in wrapper
    block = ambience_block()
    assert '[data-testid="stSidebar"][data-collapsed="true"] .tr-side-ambience' in block
    css = theme.CSS
    lifted = css[css.index("/* the rail's own content stays above the room"):]
    lifted = lifted[:lifted.index("}") + 1]
    assert '[data-testid="stSidebarUserContent"]' in lifted and "z-index: 1" in lifted
    phone = css[css.index("@media (max-width: 768px) {"):]
    phone = phone[:phone.index("\n}\n")]
    assert ".tr-side-ambience .tr-side-rig { transform: translateX(%dpx); }" % theme.RAIL_RIG["phone_shift"] in phone


def test_homes_big_reel_glows_without_a_per_frame_filter():
    big = re.search(r"\.tr-home-ambience \.tr-reel\.big \{[^}]*\}", home.CSS).group()
    assert "filter" not in big
    assert ".tr-home-ambience .tr-halo-big" in home.CSS
