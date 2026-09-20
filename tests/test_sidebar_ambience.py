"""The projector behind the rail — cinematic, not technical.

The first ambience (21 Sep 2026) drew its grain as hairlines on two axes —
two ``repeating-linear-gradient`` layers — and on the deployed site that
read as a graph grid behind the navigation. It is gone. What is there now is
drawn only with discs, bars and glows: a projector silhouette (two reels, a
body, a lit lens), a warm bloom, a vignette, and one blurred conic wedge for
the beam, which is the only thing that moves. These tests pin that contract
in the stylesheet; ``tools``-style browser checks (overflow, z-order, the
animation actually running, reduced motion) were run by hand at nine widths
and are described in the change's report.
"""

from __future__ import annotations

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


# ──────────────────────────────────────────────────────────────────────────
# 1 · No graph, no grid
# ──────────────────────────────────────────────────────────────────────────
def test_no_repeating_gradient_anywhere_in_the_sidebar_ambience():
    block = ambience_block()
    assert "repeating-" not in block
    # …and no hairline: nothing in the block is a 1px stripe of any kind.
    assert not re.search(r"\b0(?:px)? 1px, transparent 1px", block)
    # Nor anywhere else in the theme: the sidebar is the only ambience the
    # theme draws, and it draws no stripes at all.
    assert "repeating-linear-gradient" not in theme.CSS


def test_the_ambience_is_only_discs_bars_and_glows():
    block = ambience_block()
    kinds = set(re.findall(r"\b(?:repeating-)?(?:linear|radial|conic)-gradient", block))
    assert kinds == {"radial-gradient", "linear-gradient", "conic-gradient"}
    # exactly one linear gradient — the projector's body bar — and no more
    assert block.count("linear-gradient(") == 1


# ──────────────────────────────────────────────────────────────────────────
# 2 · A projector is there
# ──────────────────────────────────────────────────────────────────────────
def test_the_projector_silhouette_lens_and_beam_are_drawn():
    block = rules(ambience_block())
    after = block['[data-testid="stSidebar"]::after']
    before = block['[data-testid="stSidebar"]::before']
    # the lens: a hot core and a halo, warm
    assert "rgba(255,205,212,.78) 0 3px" in after and "rgba(255,120,140,.22) 0" in after
    # the reels: two discs with a rim and a hub, one larger than the other
    assert "0 40px, rgba(255,255,255,.085) 41px 43px" in after
    assert "0 27px, rgba(255,255,255,.08) 28px 30px" in after
    assert after.count("rgba(8,8,10,.34)") == 2
    # the body: a bar with rounded ends
    assert "124px 50px" in after and after.count("0 25px, transparent 26px") == 2
    # anchored to the bottom edge, under the navigation, whatever the height
    assert "calc(100% - 138px)" in after and "calc(100% - 226px)" in after
    # the beam: one conic wedge from the lens
    assert "conic-gradient(from 292deg at calc(50% + 60px) calc(100% - 138px)" in before
    assert "transform-origin: calc(50% + 60px) calc(100% - 138px)" in before
    # the room stays charcoal: a vignette darkens the edges
    assert "rgba(0,0,0,.45) 100%" in after


# ──────────────────────────────────────────────────────────────────────────
# 3 · CSS only
# ──────────────────────────────────────────────────────────────────────────
def test_the_animation_is_css_only_and_moves_nothing_that_lays_out():
    css = theme.CSS
    assert "<script" not in css.lower() and "javascript" not in css.lower()
    assert "url(" not in ambience_block(), "no image, no asset"
    frames = re.search(r"@keyframes tr-side-beam \{(.*?)\n\}", css, flags=re.S).group(1)
    # opacity and a rotation about the lens — compositor work, no layout
    props = set(re.findall(r"([a-z-]+):", frames))
    assert props == {"opacity", "transform"}
    assert "translate" not in frames and "rotate(" in frames
    before = rules(ambience_block())['[data-testid="stSidebar"]::before']
    assert "animation: tr-side-beam 26s ease-in-out infinite" in before
    assert 15 <= 26 <= 30


# ──────────────────────────────────────────────────────────────────────────
# 4 · Reduced motion: still, and still lit
# ──────────────────────────────────────────────────────────────────────────
def test_reduced_motion_stops_the_beam_but_keeps_the_room():
    block = reduced_motion_block()
    assert '[data-testid="stSidebar"]::before' in block
    assert "animation: none" in block and "transform: none" in block
    assert "opacity: .8" in block
    assert "display" not in block and "content" not in block, "the atmosphere stays"
    assert "::after" not in block, "the projector, lens and bloom never moved; untouched"


# ──────────────────────────────────────────────────────────────────────────
# 5 · No horizontal overflow
# ──────────────────────────────────────────────────────────────────────────
def test_the_layers_stay_inside_the_rail():
    block = rules(ambience_block())
    before = block['[data-testid="stSidebar"]::before']
    after = block['[data-testid="stSidebar"]::after']
    assert "inset: 0 12px" in before, "the beam layer is inset, never wider than the rail"
    assert "inset: 0" in after
    frames = re.search(r"@keyframes tr-side-beam \{(.*?)\n\}", theme.CSS, flags=re.S).group(1)
    assert "translate" not in frames, "the beam pivots in place; it never slides sideways"
    assert "background-repeat: no-repeat" in after


# ──────────────────────────────────────────────────────────────────────────
# 6 · Navigation above, and untouchable through it
# ──────────────────────────────────────────────────────────────────────────
def test_navigation_sits_above_the_ambience_and_clicks_pass_through():
    block = rules(ambience_block())
    for pseudo in ("::before", "::after"):
        decl = block[f'[data-testid="stSidebar"]{pseudo}']
        assert "z-index: 0" in decl and "pointer-events: none" in decl
    css = theme.CSS
    lifted = css[css.index("/* the rail's own content stays above the room"):]
    lifted = lifted[:lifted.index("}") + 1]
    assert '[data-testid="stSidebarUserContent"]' in lifted and "z-index: 1" in lifted
    assert 'isolation: isolate' in css[css.index('[data-testid="stSidebar"] {'):
                                       css.index('[data-testid="stSidebar"] {') + 600]
