"""One piece of film equipment behind the rail — Home's reel, twice, and the
film wound off one onto the other.

The rail's decoration is Home's reel drawing (``ui.home.reel_svg``) twice —
one behind the navigation, a smaller one below everything, to the right —
and a thin strip of film (``ui.home.rail_film_svg``) that hugs the first
reel's rim, leaves its left side on a tangent, runs down the rail's margin
clear of every word in the rail, and below the footer sweeps right onto
the second reel's rim on a tangent. Both are placed from one set of
numbers (``ui.theme.RAIL_RIG``), so the film sits on the rims wherever the
rail is drawn; both reels turn counter-clockwise, the smaller faster, as
one length of film would turn them; the film itself does not move. The markup rides in the footer's html block (no
extra element, no gap) and the theme fixes it behind the navigation and
clips it to the rail. These tests pin the markup, the geometry, the
stylesheet contract and the reduced-motion behaviour; the browser checks
(overflow, z-order, hit-testing, the animations actually running) were run
at nine widths.
"""

from __future__ import annotations

import math
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


def film_path() -> list[tuple[str, list[float]]]:
    """The film's one path, as (command, numbers) segments."""
    d = re.search(r'<path class="halo" d="([^"]+)"', home.rail_film_svg()).group(1)
    return [(m.group(1), [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", m.group(2))])
            for m in re.finditer(r"([MACLQS])([^MACLQS]*)", d)]


def on_rim(point, reel, tol=0.15) -> bool:
    cx, cy, r = reel
    return abs(math.dist(point, (cx, cy)) - r) < tol


# ──────────────────────────────────────────────────────────────────────────
# 1 · The markup: Home's reel, twice, then the film — inside the footer block
# ──────────────────────────────────────────────────────────────────────────
def test_the_rail_carries_two_home_reels_and_the_film(seeded):
    app = run()
    side = sidebar_html(app)
    assert side.count('<div class="tr-side-ambience" aria-hidden="true">') == 1
    assert home.reel_svg("top") in side and home.reel_svg("bottom") in side
    assert home.rail_film_svg() in side
    # the film is drawn after the reels, so it lies on their rims
    assert side.index(home.reel_svg("bottom")) < side.index('<svg class="tr-side-film"')
    # the same drawing Home uses: rim, sheen, ring, six holes, hub, pin
    reel = home.reel_svg("top")
    assert reel.count("<circle") == 6 + 5 and 'class="sheen"' in reel and 'class="hub"' in reel
    # exactly two reels, one film, nothing else in the rig
    rig = side[side.index('<div class="tr-side-ambience"'):]
    assert rig.count('<svg class="tr-reel') == 2 and rig.count('<svg class="tr-side-film"') == 1
    assert rig.count("<svg") == 3
    # it rides in the footer's block: no element of its own in the rail
    foot = next(m.value for m in app.sidebar.markdown if "tr-side-foot" in m.value)
    assert "tr-side-ambience" in foot


def test_no_grid_no_projector_no_extra_shapes():
    block = ambience_block()
    assert "repeating-" not in theme.CSS
    for word in ("conic-gradient", "lens", "beam", "projector", "grain", "linear-gradient"):
        assert word not in block, word
    assert "url(" not in block, "no image, no asset"
    assert "::before" not in block and "::after" not in block


# ──────────────────────────────────────────────────────────────────────────
# 2 · One geometry: the stylesheet places the reels where the film expects
# ──────────────────────────────────────────────────────────────────────────
def test_reels_are_placed_from_the_rig_and_the_bottom_one_is_smaller_and_offset():
    block = rules(ambience_block())
    top, bottom = theme.RAIL_RIG["top"], theme.RAIL_RIG["bottom"]
    assert (f"left: {top['left']}px; top: {top['top']}px; width: {top['size']}px; height: {top['size']}px"
            in block[".tr-side-ambience .tr-reel.top"])
    assert (f"left: {bottom['left']}px; top: {bottom['top']}px; width: {bottom['size']}px; height: {bottom['size']}px"
            in block[".tr-side-ambience .tr-reel.bottom"])
    assert bottom["size"] < top["size"] <= 180                              # small
    assert top["left"] + top["size"] <= theme.RAIL_RIG["width"]             # inside the rail: nothing to clip
    assert bottom["left"] + bottom["size"] <= theme.RAIL_RIG["width"]
    cx_top, cy_top, _ = theme.rail_reel("top")
    cx_bottom, cy_bottom, _ = theme.rail_reel("bottom")
    assert cy_bottom > cy_top + top["size"] / 2 and cx_bottom > cx_top + 40   # lower, and off the same axis
    assert bottom["top"] >= 515                                             # below the version line, at every width
    film = block[".tr-side-film"]
    assert (f"position: absolute; left: 0; top: 0; width: {theme.RAIL_RIG['width']}px; "
            f"height: {theme.RAIL_RIG['height']}px" in film)
    assert '<svg class="tr-side-film" viewBox="0 0 244 700" width="244" height="700"' in home.rail_film_svg()


def test_the_film_is_wound_on_both_rims_and_leaves_and_meets_them_on_a_tangent():
    top, bottom = theme.rail_reel("top"), theme.rail_reel("bottom")
    segs = film_path()
    assert [c for c, _ in segs] == ["M", "A", "L", "C", "A"],         "arc on the first rim, straight down the margin, one curve, arc on the second"
    start = tuple(segs[0][1])
    arc1, run, curve, arc2 = segs[1][1], segs[2][1], segs[3][1], segs[4][1]
    leave, meet, end = tuple(arc1[-2:]), tuple(curve[-2:]), tuple(arc2[-2:])
    # wound on the first reel's rim, from near its top round its left side
    assert on_rim(start, top) and on_rim(leave, top)
    assert abs(arc1[0] - top[2]) < 0.1 and arc1[4] == 0 and leave[0] < top[0]       # counter-clockwise
    # off it straight down: the run is the tangent at the left rim, and stays there
    assert abs(leave[1] - top[1]) < 0.1 and run[0] == leave[0] and run[1] > leave[1]
    # wound on the second reel's rim, from its left side round to its bottom
    assert on_rim(meet, bottom) and on_rim(end, bottom)
    assert abs(arc2[0] - bottom[2]) < 0.1 and arc2[4] == 0 and meet[0] < bottom[0] and end[1] > bottom[1]
    # tangent continuity into and out of the curve: the first control leg
    # continues the run straight down, the second lies along the rim
    c1, c2 = curve[0:2], curve[2:4]
    assert c1[0] == run[0] and c1[1] > run[1]
    radial = (meet[0] - bottom[0], meet[1] - bottom[1])
    leg = (c2[0] - meet[0], c2[1] - meet[1])
    cos = (radial[0] * leg[0] + radial[1] * leg[1]) / (math.hypot(*radial) * math.hypot(*leg))
    assert abs(cos) < 0.02 and leg[1] < 0, "arriving from above, moving down the left rim"


def test_the_film_crosses_nothing_in_the_rail():
    """The rail's words live in a column that starts at x=46 on a desktop
    and x=36 on a phone, and end at the version line (y≈512). The film's
    run keeps to the margin left of that column, and the film is right of
    it only below the footer."""
    segs = film_path()
    arc1, run, curve = segs[1][1], segs[2][1], segs[3][1]
    half = 8 / 2 + 1                                       # the widest stroke, and a pixel
    assert run[0] + half < 36, "the margin run clears a phone's content column"
    # the wound arc on the first reel sits behind the navigation, which is fine —
    # the reel does too — but its wound end fades before the logo's line
    assert min(tuple(segs[0][1])[1], arc1[-1]) > 150
    p0 = (run[0], run[1])
    c1, c2, p3 = curve[0:2], curve[2:4], curve[4:6]
    for i in range(101):
        t = i / 100
        x = (1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * c1[0] + 3 * (1 - t) * t ** 2 * c2[0] + t ** 3 * p3[0]
        y = (1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * c1[1] + 3 * (1 - t) * t ** 2 * c2[1] + t ** 3 * p3[1]
        if x + half >= 36:
            assert y - half > 515, (x, y)


def test_the_film_is_drawn_as_homes_strip_with_faded_wound_ends():
    svg = home.rail_film_svg()
    for cls in ("halo", "edges", "band", "holes", "film", "frames"):
        assert f'<path class="{cls}"' in svg
    assert len(set(re.findall(r'<path class="\w+" d="([^"]+)"', svg))) == 1, "one path, six strokes"
    assert svg.count("<linearGradient") == 2 and svg.count('fill="url(#tr-side-fade') == 2
    assert 'mask="url(#tr-side-spool)"' in svg
    block = rules(ambience_block())
    assert "stroke-dasharray: 1.5 3" in block[".tr-side-film .holes"]      # the sprocket ticks
    assert "stroke-width: 7" in block[".tr-side-film .band"] and "stroke-width: 4" in block[".tr-side-film .film"]
    assert "stroke-width: 8" in block[".tr-side-film .edges"]               # very thin: 8px edge to edge
    assert "rgba(255,140,160" in block[".tr-side-film .edges"] and "rgba(255,140,160" in block[".tr-side-film .holes"]
    assert "color: #FF8CA0" in block[".tr-side-ambience"]
    assert "stroke: #FFC9D4" in block[".tr-side-ambience .tr-reel .sheen"]


# ──────────────────────────────────────────────────────────────────────────
# 3 · CSS only, transform only, slow, the same way round; the film still
# ──────────────────────────────────────────────────────────────────────────
def test_the_motion_is_css_only_slow_and_the_same_way_on_both_reels():
    css = theme.CSS
    assert "<script" not in css.lower() and "<script" not in home.rail_film_svg().lower()
    # one keyframe, counter-clockwise: the left rims run downward, as the film does
    assert re.search(r"@keyframes tr-side-reel \{ to \{ transform: rotate\(-360deg\); \} \}", css)
    assert "@keyframes tr-side-film" not in css
    block = rules(ambience_block())
    top = int(re.search(r"tr-side-reel (\d+)s", block[".tr-side-ambience .tr-reel.top"]).group(1))
    bottom = int(re.search(r"tr-side-reel (\d+)s", block[".tr-side-ambience .tr-reel.bottom"]).group(1))
    assert 30 <= top <= 45 and 25 <= bottom < top
    # one film speed: the smaller reel turns faster in the ratio of the rims
    ratio = theme.rail_reel("top")[2] / theme.rail_reel("bottom")[2]
    assert abs(top / bottom - ratio) < 0.08
    assert "reverse" not in block[".tr-side-ambience .tr-reel.top"] + block[".tr-side-ambience .tr-reel.bottom"]
    for selector, body in block.items():
        if selector.startswith(".tr-side-film"):
            assert "animation" not in body and "transform" not in body, selector


# ──────────────────────────────────────────────────────────────────────────
# 4 · Reduced motion: everything stays, still
# ──────────────────────────────────────────────────────────────────────────
def test_reduced_motion_stops_the_reels_but_keeps_the_rig():
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
