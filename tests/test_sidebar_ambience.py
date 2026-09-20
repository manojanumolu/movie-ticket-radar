"""Two reels and the film between them, behind the rail — Home's own reel.

The rail's decoration is Home's reel drawing (``ui.home.reel_svg``) twice —
one behind the first navigation buttons, a smaller one low in the rail —
with a strip of film running down their left rims, its sprocket holes
moving. The markup rides in the footer's html block (no extra element, no
gap) and the theme fixes it behind the navigation and clips it to the
rail. These tests pin the markup, the stylesheet contract and the
reduced-motion behaviour; the browser checks (overflow, z-order, the
animations actually running) were run at nine widths.
"""

from __future__ import annotations

import re

import pytest

from tests.test_app import run, seeded  # noqa: F401
from ui import home, theme

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest


def ambience_block() -> str:
    css = theme.CSS
    start = css.index("/* Sidebar ambience")
    end = css.index("/* the rail's own content stays above the room")
    return re.sub(r"/\*.*?\*/", "", css[start:end], flags=re.S)


def reduced_motion_block() -> str:
    css = theme.CSS
    start = css.index("@media (prefers-reduced-motion: reduce)")
    return css[start:css.index("}\n}", start) + 3]


def rules(css: str) -> dict[str, str]:
    out = {}
    for m in re.finditer(r"([^{}@]+)\{([^{}]*)\}", css):
        selector = m.group(1).strip()
        if selector and not selector.startswith(("0%", "50%", "100%", "from", "to")):
            out[selector] = m.group(2)
    return out


def sidebar_html(app) -> str:
    return "".join(m.value for m in app.sidebar.markdown)


# ──────────────────────────────────────────────────────────────────────────
# 1 · The markup: Home's reel, twice, and the film — inside the footer block
# ──────────────────────────────────────────────────────────────────────────
def test_the_rail_carries_two_home_reels_and_a_film(seeded):
    app = run()
    side = sidebar_html(app)
    assert side.count('<div class="tr-side-ambience" aria-hidden="true">') == 1
    assert home.reel_svg("top") in side and home.reel_svg("bottom") in side
    assert '<div class="tr-side-film"></div>' in side
    # the same drawing Home uses: rim, sheen, ring, six holes, hub, pin
    reel = home.reel_svg("top")
    assert reel.count("<circle") == 6 + 5 and 'class="sheen"' in reel and 'class="hub"' in reel
    # it rides in the footer's block: no element of its own in the rail
    foot = next(m.value for m in app.sidebar.markdown if "tr-side-foot" in m.value)
    assert "tr-side-ambience" in foot


def test_no_grid_no_projector_no_extra_shapes():
    block = ambience_block()
    assert "repeating-" not in theme.CSS
    for word in ("conic-gradient", "lens", "beam", "projector", "grain"):
        assert word not in block, word
    assert "url(" not in block, "no image, no asset"


# ──────────────────────────────────────────────────────────────────────────
# 2 · Placement and look
# ──────────────────────────────────────────────────────────────────────────
def test_reels_sit_behind_the_navigation_in_homes_colours():
    block = rules(ambience_block())
    top = block[".tr-side-ambience .tr-reel.top"]
    bottom = block[".tr-side-ambience .tr-reel.bottom"]
    assert "left: 40px; top: 65px; width: 300px" in top          # behind Home / My Monitors / History
    assert "left: 40px; bottom: 40px; width: 220px" in bottom    # the smaller one, low
    assert "color: #FF8CA0" in block[".tr-side-ambience"]
    assert "stroke: #FFC9D4" in block[".tr-side-ambience .tr-reel .sheen"]
    film = block[".tr-side-film"]
    assert "left: 40px; width: 26px; top: 215px; bottom: 150px" in film   # rim to rim


# ──────────────────────────────────────────────────────────────────────────
# 3 · CSS only, transform only, slow
# ──────────────────────────────────────────────────────────────────────────
def test_the_motion_is_css_only_and_slow():
    css = theme.CSS
    assert "<script" not in css.lower()
    assert re.search(r"@keyframes tr-side-reel \{ to \{ transform: rotate\(360deg\); \} \}", css)
    assert re.search(r"@keyframes tr-side-film \{ to \{ transform: translate3d\(0, 36px, 0\); \} \}", css)
    block = rules(ambience_block())
    top = int(re.search(r"tr-side-reel (\d+)s", block[".tr-side-ambience .tr-reel.top"]).group(1))
    bottom = int(re.search(r"tr-side-reel (\d+)s", block[".tr-side-ambience .tr-reel.bottom"]).group(1))
    assert 25 <= bottom < top <= 90
    assert "animation: tr-side-film" in block[".tr-side-film::before"]


# ──────────────────────────────────────────────────────────────────────────
# 4 · Reduced motion: everything stays, still
# ──────────────────────────────────────────────────────────────────────────
def test_reduced_motion_stops_reels_and_film_but_keeps_them():
    block = reduced_motion_block()
    # the per-reel rules carry an extra class; the override must outrank them
    assert ".tr-side-ambience .tr-reel.top, .tr-side-ambience .tr-reel.bottom, .tr-side-film::before" in block
    assert "animation: none" in block and "transform: none" in block
    for word in ("display", "opacity", "visibility"):
        assert word not in block


# ──────────────────────────────────────────────────────────────────────────
# 5 · No horizontal overflow: fixed to the rail's box, and clipped there
# ──────────────────────────────────────────────────────────────────────────
def test_the_rig_is_fixed_to_the_rail_and_clipped():
    wrapper = rules(ambience_block())[".tr-side-ambience"]
    assert "position: fixed; left: 0; top: 0; width: 100%; height: 100%" in wrapper
    assert "overflow: hidden" in wrapper
    assert '[data-testid="stSidebar"][aria-expanded="false"] .tr-side-ambience { display: none; }' in ambience_block()


# ──────────────────────────────────────────────────────────────────────────
# 6 · Navigation above, and untouchable through it
# ──────────────────────────────────────────────────────────────────────────
def test_navigation_sits_above_and_clicks_pass_through():
    wrapper = rules(ambience_block())[".tr-side-ambience"]
    assert "z-index: 0" in wrapper and "pointer-events: none" in wrapper
    css = theme.CSS
    lifted = css[css.index("/* the rail's own content stays above the room"):]
    lifted = lifted[:lifted.index("}") + 1]
    assert '[data-testid="stSidebarUserContent"]' in lifted and "z-index: 1" in lifted
