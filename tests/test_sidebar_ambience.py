"""The rail's film mechanism — two reels and the film wound between them.

The composition is the reference one: a large reel at the upper left (behind
the logo's lower edge and the first navigation rows), a smaller reel at the
lower right (in the empty rail under the version line), and one strip of film
wound between them — out of the large reel to the lower right, a gentle bulge
through the rail's right-hand gutter, back across behind the quote, and into
the small reel. The reels are Home's own drawing (``ui.home.reel_svg``); the
film is drawn as Home draws its own strip — one curve stroked in layers (band,
perforations, a narrower band that leaves them only at the edges, frames).
Each reel stands on a dark disc the film's ends run under, so the film is seen
to leave one reel and enter the other. Flat 2D: no glow, no gloss, no filter.
The reels turn and the film runs at one rim speed. ``ui.theme.RAIL_RIG``
places all of it; the markup rides in the footer's html block.
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


def reduced_motion_block() -> str:
    css = theme.CSS
    start = css.index("@media (prefers-reduced-motion: reduce)")
    return css[start:css.index("}\n}", start) + 3]


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


def curve_points(d: str, steps: int = 40) -> list[tuple[float, float]]:
    """Points along an M … C … C … path of cubic Béziers."""
    nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", d)]
    x0, y0, rest = nums[0], nums[1], nums[2:]
    pts = [(x0, y0)]
    for i in range(0, len(rest), 6):
        x1, y1, x2, y2, x3, y3 = rest[i:i + 6]
        for k in range(1, steps + 1):
            t = k / steps
            a, b, c, e = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t ** 2, t ** 3
            pts.append((a * x0 + b * x1 + c * x2 + e * x3, a * y0 + b * y1 + c * y2 + e * y3))
        x0, y0 = x3, y3
    return pts


# ── 1 · The markup ─────────────────────────────────────────────────────────
def test_the_rail_carries_two_reels_on_discs_and_one_strip_of_film(seeded):
    app = run()
    side = sidebar_html(app)
    assert side.count('<div class="tr-side-ambience" aria-hidden="true">') == 1
    rig = side[side.index('<div class="tr-side-ambience"'):]
    assert home.reel_svg("top") in rig and home.reel_svg("bottom") in rig
    # the strip between the reels is left out of the rail at the owner's request
    assert rig.count('<svg class="tr-reel') == 2 and "tr-film" not in rig
    assert rig.count('class="tr-disc top"') == 1 and rig.count('class="tr-disc bottom"') == 1
    # each disc, then its reel
    assert rig.index("tr-disc top") < rig.index("tr-reel top") < rig.index("tr-reel bottom")
    foot = next(m.value for m in app.sidebar.markdown if "tr-side-foot" in m.value)
    assert "tr-side-ambience" in foot                       # no element of its own in the rail
    reel = home.reel_svg("top")
    assert reel.count("<circle") == 6 + 5 and 'class="hub"' in reel    # a real reel, with its cut-outs


def test_the_strip_is_perforated_film_on_one_curve():
    film = theme.RAIL_RIG["film"]
    svg = home.rail_film_svg(film["d"], film["width"], film["pitch"])
    layers = re.findall(r'<path class="(\w+)" d="([^"]+)"/>', svg)
    assert [c for c, _ in layers] == ["edge", "band", "perf", "film", "frames"]
    assert {d for _, d in layers} == {film["d"]}                        # one curve, every layer on it
    block = rules(ambience_block())
    width = lambda c: float(re.search(r"stroke-width: ([\d.]+)", block[f".tr-side-ambience .tr-film .{c}"]).group(1))  # noqa: E731
    # perforations show only at the edges: the band covers the middle of their dashes
    assert width("band") == film["width"] and width("perf") < width("band")
    assert width("film") < width("perf") - 4 and width("frames") < width("film")
    perf = re.search(r"stroke-dasharray: ([\d.]+) ([\d.]+)", block[".tr-side-ambience .tr-film .perf"]).groups()
    frames = re.search(r"stroke-dasharray: ([\d.]+) ([\d.]+)", block[".tr-side-ambience .tr-film .frames"]).groups()
    assert sum(map(float, perf)) * 3 == pytest.approx(film["pitch"])      # three holes per frame
    assert sum(map(float, frames)) == pytest.approx(film["pitch"])        # one frame per pitch
    assert float(frames[0]) > 2 * float(frames[1]) and float(frames[0]) > width("frames")   # frames, not slits
    assert "<image" not in svg and "href=" not in svg


# ── 2 · Composition: large reel upper left, small reel lower right, film between ─
def test_the_composition_follows_the_reference():
    top, bottom, film = theme.RAIL_RIG["top"], theme.RAIL_RIG["bottom"], theme.RAIL_RIG["film"]
    assert top["size"] > bottom["size"]
    (tx, ty), (bx, by) = centre(top), centre(bottom)
    assert tx < RAIL / 3 and bx > RAIL / 2                                # left, then right
    assert top["top"] < 200 and bottom["top"] > VERSION_BOTTOM + 10       # upper, then below the version line
    assert top["left"] + top["size"] <= RAIL and bottom["left"] + bottom["size"] <= RAIL
    pts = curve_points(film["d"])
    # attached: the film runs from one hub to the other
    assert pts[0] == pytest.approx((tx, ty), abs=2) and pts[-1] == pytest.approx((bx, by), abs=2)
    # it bulges into the right-hand gutter, then sweeps back left, gently
    mid = [p for p in pts if ty + top["size"] / 2 < p[1] < by - bottom["size"] / 2]
    assert max(x for x, _ in mid) > 200 and min(x for x, _ in mid) < 130
    half = film["width"] / 2
    assert all(half <= x <= RAIL - half for x, _ in pts)                  # never past the rail's edges
    assert all(y >= LOGO_BOTTOM for _, y in pts)                          # never across the logo
    block = rules(ambience_block())
    for name, reel in (("top", top), ("bottom", bottom)):
        assert (f"left: {reel['left']}px; top: {reel['top']}px; width: {reel['size']}px; height: {reel['size']}px"
                in block[f".tr-side-ambience .tr-reel.{name}"])
    for part in ("rim", "ring", "holes", "hub"):
        opacity = float(re.search(r"opacity: (\.\d+)", block[f".tr-side-ambience .tr-reel .{part}"]).group(1))
        assert opacity <= 0.22, part                                      # restrained
    assert "opacity: 0" in block[".tr-side-ambience .tr-reel .sheen"]    # flat: no gloss


# ── 3 · Motion: CSS only, one rim speed, no filter ─────────────────────────
def test_reels_and_film_move_as_one_mechanism():
    css = theme.CSS
    assert "<script" not in css.lower()
    assert re.search(r"@keyframes tr-side-reel \{ to \{ transform: rotate\(360deg\); \} \}", css)
    film = theme.RAIL_RIG["film"]
    assert re.search(r"@keyframes tr-side-film \{ to \{ stroke-dashoffset: -%d; \} \}" % film["pitch"], css)
    block = rules(ambience_block())
    top = float(re.search(r"tr-side-reel ([\d.]+)s", block[".tr-side-ambience .tr-reel.top"]).group(1))
    bottom = float(re.search(r"tr-side-reel ([\d.]+)s", block[".tr-side-ambience .tr-reel.bottom"]).group(1))
    loop = float(re.search(r"tr-side-film ([\d.]+)s", css).group(1))
    rim = lambda reel, period: 2 * 3.14159 * reel["size"] / 2 * 0.95 / period    # noqa: E731
    speed = rim(theme.RAIL_RIG["top"], top)
    assert abs(rim(theme.RAIL_RIG["bottom"], bottom) - speed) / speed < 0.03    # the reels at one rim speed
    assert abs(film["pitch"] / loop - speed) / speed < 0.03                     # and the film with them
    assert 40 <= top <= 60 and speed < 10                                        # slow and subtle
    assert "filter" not in ambience_block() and "drop-shadow" not in ambience_block() and "box-shadow" not in ambience_block()
    for selector in block:
        assert selector.startswith((".tr-side-ambience", '[data-testid="stSidebar"]')), selector


def test_reduced_motion_stills_the_reels_and_the_film():
    block = reduced_motion_block()
    for part in (".tr-side-ambience .tr-reel.top", ".tr-side-ambience .tr-reel.bottom",
                 ".tr-side-ambience .tr-film .perf", ".tr-side-ambience .tr-film .frames"):
        assert part in block
    assert "animation: none" in block
    for word in ("display", "opacity", "visibility"):
        assert word not in block


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
