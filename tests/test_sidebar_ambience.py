"""One film reel behind the rail — and nothing else.

Two earlier ambiences were rejected on the deployed site: a grain drawn as
hairlines on two axes read as a graph grid, and a gradient-built projector
read as random translucent shapes. What is there now is a single object: a
large faint film reel — a disc with a brighter rim, six holes and a hub cut
out with a mask so the rail shows through — turning once every forty
seconds, with the rail's own background carrying a soft glow centred on it.
These tests pin that contract in the stylesheet; the browser checks
(overflow, z-order, the rotation actually running, reduced motion) were run
at nine widths and are described in the change's report.
"""

from __future__ import annotations

import math
import re

from ui import theme


def ambience_block() -> str:
    """The sidebar ambience rules, comments stripped."""
    css = theme.CSS
    start = css.index("/* Sidebar ambience")
    end = css.index("/* the rail's own content stays above the room")
    return re.sub(r"/\*.*?\*/", "", css[start:end], flags=re.S)


def reduced_motion_block() -> str:
    css = theme.CSS
    start = css.index("@media (prefers-reduced-motion: reduce)")
    return css[start:css.index("}\n}", start) + 3]


def rules(css: str) -> dict[str, str]:
    """selector → declarations, keyframes excluded."""
    out = {}
    for m in re.finditer(r"([^{}@]+)\{([^{}]*)\}", css):
        selector = m.group(1).strip()
        if selector and not selector.startswith(("0%", "50%", "100%", "from", "to")):
            out[selector] = m.group(2)
    return out


def reel() -> str:
    return rules(ambience_block())['[data-testid="stSidebar"]::before']


def base_rule() -> str:
    """The rail's own rule (``[data-testid="stSidebar"] { … }``)."""
    css = theme.CSS
    start = css.index('[data-testid="stSidebar"] {')
    return css[start:css.index("}", start)]


# ──────────────────────────────────────────────────────────────────────────
# 1 · No graph, no grid, no projector — one object
# ──────────────────────────────────────────────────────────────────────────
def test_no_repeating_gradient_or_hairline_anywhere_in_the_theme():
    assert "repeating-" not in theme.CSS
    assert not re.search(r"\b0(?:px)? 1px, transparent 1px", ambience_block())


def test_the_ambience_is_exactly_one_pseudo_element():
    block = rules(ambience_block())
    assert list(block) == ['[data-testid="stSidebar"]::before',
                           '[data-testid="stSidebar"][aria-expanded="false"]::before']
    assert block['[data-testid="stSidebar"][aria-expanded="false"]::before'].strip() == "display: none;"
    assert "::after" not in ambience_block()
    for word in ("conic-gradient", "lens", "beam", "projector", "body", "grain"):
        assert word not in ambience_block(), word


# ──────────────────────────────────────────────────────────────────────────
# 2 · It is a film reel
# ──────────────────────────────────────────────────────────────────────────
def test_the_reel_is_a_disc_with_a_rim_six_holes_and_a_hub():
    decl = reel()
    assert "border-radius: 50%" in decl and "width: 168px; height: 168px" in decl
    # the disc and its rim
    assert "transparent 0 46.5%, rgba(255,235,240,.95) 47% 50%" in decl
    assert "rgba(255,110,140,.85) 7.8% 100%" in decl
    # the holes: six, cut out by the mask, evenly spaced around the hub
    mask = decl[decl.index("\n  mask-image:"):decl.index("-webkit-mask-composite")]
    holes = re.findall(r"circle at ([\d.]+)% ([\d.]+)%, #000 0 11\.5%", mask)
    assert len(holes) == 6
    angles = sorted(math.degrees(math.atan2(float(y) - 50, float(x) - 50)) % 360 for x, y in holes)
    gaps = [round(b - a) for a, b in zip(angles, angles[1:])]
    assert gaps == [60] * 5, angles
    # the hub's centre, and the disc itself as the base layer
    assert "radial-gradient(circle, #000 0 2.2%, transparent 2.6%)" in mask
    assert "radial-gradient(circle, #000 0 49.6%, transparent 50%)" in mask
    assert "mask-composite: exclude, exclude, exclude, exclude, exclude, exclude, exclude, add" in decl
    assert "-webkit-mask-composite: xor, xor, xor, xor, xor, xor, xor, source-over" in decl


def test_the_reel_is_faint_and_the_glow_is_the_rails_own_background():
    decl = reel()
    opacity = float(re.search(r"opacity: (\.\d+)", decl).group(1))
    assert 0.08 <= opacity <= 0.16
    base = base_rule()
    assert "radial-gradient(300px 300px at 50% calc(100% - 128px), rgba(255,51,85,.12), transparent 70%)" in base
    assert "linear-gradient(180deg,rgba(24,24,32,.92) 0%,rgba(14,14,20,.96) 100%)" in base, "the charcoal stays"


# ──────────────────────────────────────────────────────────────────────────
# 3 · CSS only, transform only, slow
# ──────────────────────────────────────────────────────────────────────────
def test_the_rotation_is_css_only_and_slow():
    css = theme.CSS
    assert "<script" not in css.lower() and "javascript" not in css.lower()
    assert "url(" not in ambience_block(), "no image, no asset"
    frames = re.search(r"@keyframes tr-reel-spin \{(.*?)\} \}", css).group(1)
    assert frames.strip() == "to { transform: rotate(360deg);"
    seconds = int(re.search(r"animation: tr-reel-spin (\d+)s linear infinite", reel()).group(1))
    assert 25 <= seconds <= 45


# ──────────────────────────────────────────────────────────────────────────
# 4 · Reduced motion: the reel stays, still
# ──────────────────────────────────────────────────────────────────────────
def test_reduced_motion_stops_the_turn_and_keeps_the_reel():
    block = reduced_motion_block()
    assert '[data-testid="stSidebar"]::before' in block
    assert "animation: none" in block and "transform: none" in block
    for word in ("display", "content", "opacity", "visibility"):
        assert word not in block, "the reel stays visible"


# ──────────────────────────────────────────────────────────────────────────
# 5 · No horizontal overflow, no layout
# ──────────────────────────────────────────────────────────────────────────
def test_the_turning_reel_stays_inside_the_rail():
    """A rotating square reaches side·√2/2 from its centre at 45°; centred
    in a 244px rail that must stay under 122px, and it does."""
    decl = reel()
    side = int(re.search(r"width: (\d+)px", decl).group(1))
    assert "left: calc(50% - 84px)" in decl and side == 168
    assert side * math.sqrt(2) / 2 < 122
    assert "position: absolute" in decl and "translate" not in theme.CSS[theme.CSS.index("@keyframes tr-reel-spin"):
                                                                          theme.CSS.index("@keyframes tr-reel-spin") + 80]
    assert "overflow" not in base_rule(), "the rail is not made a clipper"


# ──────────────────────────────────────────────────────────────────────────
# 6 · Navigation above, and untouchable through it
# ──────────────────────────────────────────────────────────────────────────
def test_navigation_sits_above_the_reel_and_clicks_pass_through():
    decl = reel()
    assert "z-index: 0" in decl and "pointer-events: none" in decl
    css = theme.CSS
    lifted = css[css.index("/* the rail's own content stays above the room"):]
    lifted = lifted[:lifted.index("}") + 1]
    assert '[data-testid="stSidebarUserContent"]' in lifted and "z-index: 1" in lifted
    base = css[css.index('[data-testid="stSidebar"] {'):]
    assert "isolation: isolate" in base[:base.index("}")]
