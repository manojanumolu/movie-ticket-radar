"""The rail's film mechanism — two reels and the film between them, behind it all.

The rail's decoration is Home's reel drawing (``ui.home.reel_svg``) twice —
one behind the navigation rows, one in the empty rail under the version line
— joined by a strip of film (``ui.home.rail_film_svg``): a celluloid band with
sprocket perforations down both edges and picture frames between, leaving
each reel tangent to its right-hand rim. ``ui.theme.RAIL_RIG`` places all
three; the strip runs down the rail's right-hand gutter, which is clear of
every word at every width (measured in a browser: content ends at x≈197 on a
244px rail, x≈253 on a phone's 300px one). The reels turn and the film runs at
one rim speed. The markup rides in the footer's html block (no extra element,
no gap); the theme fixes it behind the navigation and clips it to the rail.
"""

from __future__ import annotations

import re

import pytest

from tests.test_app import run, seeded  # noqa: F401
from ui import home, theme

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

#: Where the rail's words end, measured in Chromium (logo, navigation rows,
#: quote, version line): x on a 244px rail, and the rows' vertical extent.
CONTENT_RIGHT = 197
LOGO_BOTTOM = 136
VERSION_BOTTOM = 563
RAIL = 244
PHONE_RAIL = 300


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


def rim_x(reel: dict) -> float:
    """The x of a reel's right-hand rim: ``reel_svg`` draws the rim at r=190 of 200."""
    return reel["left"] + reel["size"] / 2 * (1 + 0.95)


# ──────────────────────────────────────────────────────────────────────────
# 1 · The markup: two of Home's reels and one strip of film, in the footer block
# ──────────────────────────────────────────────────────────────────────────
def test_the_rail_carries_two_home_reels_and_a_strip_of_film(seeded):
    app = run()
    side = sidebar_html(app)
    assert side.count('<div class="tr-side-ambience" aria-hidden="true">') == 1
    assert home.reel_svg("top") in side and home.reel_svg("bottom") in side
    reel = home.reel_svg("top")
    # a real reel: rim, sheen, ring, six cut-outs, hub, pin
    assert reel.count("<circle") == 6 + 5 and 'class="sheen"' in reel and 'class="hub"' in reel
    rig = side[side.index('<div class="tr-side-ambience"'):]
    assert rig.count('<svg class="tr-reel') == 2 and rig.count('<div class="tr-film">') == 1
    assert rig.count("<svg") == 3
    # it rides in the footer's block: no element of its own in the rail
    foot = next(m.value for m in app.sidebar.markdown if "tr-side-foot" in m.value)
    assert "tr-side-ambience" in foot and "tr-film" in foot


def test_the_strip_is_film_frames_and_sprocket_holes_not_lines():
    film = theme.RAIL_RIG["film"]
    svg = home.rail_film_svg(film["width"], film["height"], film["pitch"])
    tile = re.search(r'<pattern id="tr-rail-film"[^>]*>(.*?)</pattern>', svg, re.S).group(1)
    perfs = re.findall(r'<rect class="perf" x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"', tile)
    frames = re.findall(r'<rect class="frame" x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"', tile)
    # four perforations per frame down each edge, one picture frame between them
    assert len(perfs) == 8 and len(frames) == 1
    xs = sorted({float(x) for x, *_ in perfs})
    assert len(xs) == 2 and xs[0] < 3 and xs[1] + float(perfs[0][2]) > film["width"] - 3    # at both edges
    fx, _, fw, fh = map(float, frames[0])
    assert fx > xs[0] + float(perfs[0][2]) and fx + fw < xs[1]                              # between them
    assert fw > 2 * float(perfs[0][2]) and fh > 2 * float(perfs[0][3])                      # a frame, not a hole
    assert fw / fh > 0.9                                                                    # wider than a slit
    # the band itself and the tile repeated down it; one pitch longer, so the loop has no seam
    assert '<rect class="base"' in svg and 'fill="url(#tr-rail-film)"' in svg
    assert f'height="{film["height"] + film["pitch"]}"' in svg
    assert "<image" not in svg and "href=" not in svg


# ──────────────────────────────────────────────────────────────────────────
# 2 · Placement: clear of every word, tangent to both reels
# ──────────────────────────────────────────────────────────────────────────
def test_the_mechanism_is_placed_from_the_rig_and_clear_of_the_words():
    block = rules(ambience_block())
    top, bottom, film = theme.RAIL_RIG["top"], theme.RAIL_RIG["bottom"], theme.RAIL_RIG["film"]
    for name, reel in (("top", top), ("bottom", bottom)):
        assert (f"left: {reel['left']}px; top: {reel['top']}px; width: {reel['size']}px; height: {reel['size']}px"
                in block[f".tr-side-ambience .tr-reel.{name}"])
        assert reel["left"] + reel["size"] <= RAIL                                     # inside the rail
    assert (f"left: {film['left']}px; top: {film['top']}px; width: {film['width']}px; height: {film['height']}px"
            in block[".tr-side-ambience .tr-film"])
    # the strip keeps to the right-hand gutter: past every word, inside the rail
    assert film["left"] > CONTENT_RIGHT + 10 and film["left"] + film["width"] <= RAIL - 2
    shift = theme.RAIL_RIG["phone_shift"]
    assert film["left"] + shift > CONTENT_RIGHT + shift and film["left"] + film["width"] + shift <= PHONE_RAIL
    # it leaves each reel at the rim, tangent, as film does
    centre = film["left"] + film["width"] / 2
    assert abs(rim_x(top) - centre) <= 1.5 and abs(rim_x(bottom) - centre) <= 1.5
    top_cy, bottom_cy = top["top"] + top["size"] / 2, bottom["top"] + bottom["size"] / 2
    assert film["top"] <= top_cy and film["top"] + film["height"] >= bottom_cy        # from reel to reel
    assert film["top"] >= top_cy - 12 and film["top"] + film["height"] <= bottom_cy + 12
    # the upper reel is below the logo; the lower one below the version line
    assert top["top"] > LOGO_BOTTOM and bottom["top"] > VERSION_BOTTOM + 10
    assert bottom["size"] < top["size"] <= 180
    assert "color: #FF8CA0" in block[".tr-side-ambience"]
    for part in ("rim", "ring", "holes", "hub"):
        opacity = float(re.search(r"opacity: (\.\d+)", block[f".tr-side-ambience .tr-reel .{part}"]).group(1))
        assert opacity <= 0.15, part                                                    # subtle
    film_style = re.search(r"\.tr-side-ambience \.tr-film \{ position: absolute;[^}]*\}", ambience_block()).group()
    assert float(re.search(r"opacity: (\.\d+)", film_style).group(1)) <= 0.7


# ──────────────────────────────────────────────────────────────────────────
# 3 · CSS only, transform only, one rim speed, no per-frame filter
# ──────────────────────────────────────────────────────────────────────────
def test_reels_and_film_move_as_one_mechanism():
    css = theme.CSS
    assert "<script" not in css.lower()
    assert re.search(r"@keyframes tr-side-reel \{ to \{ transform: rotate\(-360deg\); \} \}", css)
    film = theme.RAIL_RIG["film"]
    assert re.search(r"@keyframes tr-side-film \{ to \{ transform: translateY\(-%dpx\); \} \}" % film["pitch"], css)
    block = rules(ambience_block())
    top = float(re.search(r"tr-side-reel ([\d.]+)s", block[".tr-side-ambience .tr-reel.top"]).group(1))
    bottom = float(re.search(r"tr-side-reel ([\d.]+)s", block[".tr-side-ambience .tr-reel.bottom"]).group(1))
    loop = float(re.search(r"tr-side-film ([\d.]+)s", block[".tr-side-ambience .tr-film .run"]).group(1))
    assert 30 <= top <= 60 and bottom < top                                           # slow
    rim = lambda reel, period: 2 * 3.14159 * reel["size"] / 2 * 0.95 / period        # noqa: E731 - px/s
    speed = rim(theme.RAIL_RIG["top"], top)
    assert abs(rim(theme.RAIL_RIG["bottom"], bottom) - speed) / speed < 0.03          # one rim speed
    assert abs(film["pitch"] / loop - speed) / speed < 0.03                           # and the film with them
    assert speed < 15                                                                 # px/s: restrained
    assert "reverse" not in block[".tr-side-ambience .tr-reel.top"] + block[".tr-side-ambience .tr-reel.bottom"]
    # nothing in the rig is filtered: a filter on a moving element repaints every frame
    assert "filter:" not in ambience_block() and "drop-shadow" not in ambience_block()
    for selector in block:
        assert selector.startswith((".tr-side-ambience", '[data-testid="stSidebar"]')), selector


# ──────────────────────────────────────────────────────────────────────────
# 4 · Reduced motion: everything stays, still
# ──────────────────────────────────────────────────────────────────────────
def test_reduced_motion_stops_the_reels_and_the_film_but_keeps_them():
    block = reduced_motion_block()
    assert ".tr-side-ambience .tr-reel.top, .tr-side-ambience .tr-reel.bottom, .tr-side-ambience .tr-film .run {" in block
    assert "animation: none" in block and "transform: none" in block
    for word in ("display", "opacity", "visibility"):
        assert word not in block


# ──────────────────────────────────────────────────────────────────────────
# 5 · No horizontal overflow: fixed to the rail's box, clipped, shifted on a phone
# ──────────────────────────────────────────────────────────────────────────
def test_the_rig_is_fixed_to_the_rail_clipped_and_hidden_when_collapsed():
    wrapper = rules(ambience_block())[".tr-side-ambience"]
    assert "position: fixed; left: 0; top: 0; width: 100%; height: 100%" in wrapper
    assert "overflow: hidden" in wrapper
    block = ambience_block()
    assert '[data-testid="stSidebar"][aria-expanded="false"] .tr-side-ambience' in block
    assert '[data-testid="stSidebar"][data-collapsed="true"] .tr-side-ambience' in block     # Streamlit 1.66
    phone = theme.CSS[theme.CSS.index("@media (max-width: 768px) {"):]
    phone = phone[:phone.index("\n}\n")]
    assert ".tr-side-ambience .tr-side-rig { transform: translateX(%dpx); }" % theme.RAIL_RIG["phone_shift"] in phone


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


def test_homes_big_reel_glows_without_a_per_frame_filter():
    """Measured in Chromium: the drop-shadow on Home's turning 820px reel cost
    ~40% of the frame rate. Its glow is now a static halo behind it."""
    assert ".tr-home-ambience .tr-reel.big { right:-6%; bottom:-140px; width:min(64vw, 820px); animation:" in home.CSS
    big = re.search(r"\.tr-home-ambience \.tr-reel\.big \{[^}]*\}", home.CSS).group()
    assert "filter" not in big
    assert ".tr-home-ambience .tr-halo-big" in home.CSS and "radial-gradient" in home.CSS
