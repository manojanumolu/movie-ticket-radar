"""Two of Home's reels behind the rail — and nothing across its words.

The rail's decoration is Home's reel drawing (``ui.home.reel_svg``) twice —
one behind the navigation, a small one in the empty rail below the footer
— placed from one set of numbers (``ui.theme.RAIL_RIG``), both turning the
same way, the smaller faster. No film joins them: the navigation, the
quote and the footer fill the column between, and every word in the rail
keeps a clear background. The markup rides in the footer's html block (no
extra element, no gap) and the theme fixes it behind the navigation and
clips it to the rail. These tests pin the markup, the placement, the
stylesheet contract and the reduced-motion behaviour; the browser checks
(overflow, z-order, hit-testing, the animations actually running) were run
at nine widths.
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
# 1 · The markup: Home's reel, twice, and nothing else — inside the footer block
# ──────────────────────────────────────────────────────────────────────────
def test_the_rail_carries_two_home_reels_and_nothing_else(seeded):
    app = run()
    side = sidebar_html(app)
    assert side.count('<div class="tr-side-ambience" aria-hidden="true">') == 1
    assert home.reel_svg("top") in side and home.reel_svg("bottom") in side
    # the same drawing Home uses: rim, sheen, ring, six holes, hub, pin
    reel = home.reel_svg("top")
    assert reel.count("<circle") == 6 + 5 and 'class="sheen"' in reel and 'class="hub"' in reel
    # exactly two reels; no film, no path, no other shape in the rig
    rig = side[side.index('<div class="tr-side-ambience"'):]
    assert rig.count('<svg class="tr-reel') == 2 and rig.count("<svg") == 2
    assert "<path" not in rig and "film" not in rig
    # it rides in the footer's block: no element of its own in the rail
    foot = next(m.value for m in app.sidebar.markdown if "tr-side-foot" in m.value)
    assert "tr-side-ambience" in foot


def test_no_film_no_grid_no_projector_no_extra_shapes():
    block = ambience_block()
    assert "repeating-" not in theme.CSS
    for word in ("conic-gradient", "lens", "beam", "projector", "grain", "linear-gradient", "film"):
        assert word not in block, word
    assert "url(" not in block, "no image, no asset"
    assert "::before" not in block and "::after" not in block
    assert not hasattr(home, "rail_film_svg")


# ──────────────────────────────────────────────────────────────────────────
# 2 · Placement: behind the navigation, and in the empty rail below the footer
# ──────────────────────────────────────────────────────────────────────────
def test_reels_are_placed_from_the_rig_small_and_the_lower_one_below_the_footer():
    block = rules(ambience_block())
    top, bottom = theme.RAIL_RIG["top"], theme.RAIL_RIG["bottom"]
    assert (f"left: {top['left']}px; top: {top['top']}px; width: {top['size']}px; height: {top['size']}px"
            in block[".tr-side-ambience .tr-reel.top"])
    assert (f"left: {bottom['left']}px; top: {bottom['top']}px; width: {bottom['size']}px; height: {bottom['size']}px"
            in block[".tr-side-ambience .tr-reel.bottom"])
    assert bottom["size"] < top["size"] <= 180                              # small
    assert top["left"] + top["size"] <= 244 and bottom["left"] + bottom["size"] <= 244   # inside the rail
    assert 150 <= top["top"] and top["top"] + top["size"] <= 350            # behind the navigation rows only
    assert bottom["top"] >= 515                                             # below the version line, at every width
    assert bottom["left"] > top["left"] + 40                                # off the same axis
    assert "color: #FF8CA0" in block[".tr-side-ambience"]
    assert "stroke: #FFC9D4" in block[".tr-side-ambience .tr-reel .sheen"]
    for part in ("rim", "ring", "holes", "hub"):
        opacity = float(re.search(r"opacity: (\.\d+)", block[f".tr-side-ambience .tr-reel .{part}"]).group(1))
        assert opacity <= 0.15, part                                        # subtle


# ──────────────────────────────────────────────────────────────────────────
# 3 · CSS only, transform only, slow, the same way round
# ──────────────────────────────────────────────────────────────────────────
def test_the_motion_is_css_only_slow_and_the_same_way_on_both_reels():
    css = theme.CSS
    assert "<script" not in css.lower()
    assert re.search(r"@keyframes tr-side-reel \{ to \{ transform: rotate\(-360deg\); \} \}", css)
    assert "@keyframes tr-side-film" not in css
    block = rules(ambience_block())
    top = int(re.search(r"tr-side-reel (\d+)s", block[".tr-side-ambience .tr-reel.top"]).group(1))
    bottom = int(re.search(r"tr-side-reel (\d+)s", block[".tr-side-ambience .tr-reel.bottom"]).group(1))
    assert 30 <= top <= 45 and 25 <= bottom < top
    # the smaller reel turns faster in the ratio of the rims, as one film would turn them
    ratio = theme.RAIL_RIG["top"]["size"] / theme.RAIL_RIG["bottom"]["size"]
    assert abs(top / bottom - ratio) < 0.08
    assert "reverse" not in block[".tr-side-ambience .tr-reel.top"] + block[".tr-side-ambience .tr-reel.bottom"]
    for selector in block:
        assert selector.startswith((".tr-side-ambience", "[data-testid=\"stSidebar\"][aria-expanded=\"false\"]")), selector


# ──────────────────────────────────────────────────────────────────────────
# 4 · Reduced motion: everything stays, still
# ──────────────────────────────────────────────────────────────────────────
def test_reduced_motion_stops_the_reels_but_keeps_them():
    block = reduced_motion_block()
    # the per-reel rules carry an extra class; the override must outrank them
    assert ".tr-side-ambience .tr-reel.top, .tr-side-ambience .tr-reel.bottom {" in block
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
