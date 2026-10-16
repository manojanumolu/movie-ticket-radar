"""Phase 1 — the Login page's cinematic room, pinned.

The room is built from three things that did not exist before: the radar
(``ui.radar``, one SVG, states by attribute), the six fixed posters served
from ``static/`` (``ui.assets_registry``, never the catalogue), and the
twenty-four crafts with their tooltips (``ui.crafts``). These tests pin
what the redesign promised — no Charminar, no BookMyShow artwork, no
base64 in the page, one CSS payload, a wait that is honest — and they run
without a browser; the browser audit (``tools/``) covers the rest.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ui import assets_registry as art
from ui import crafts, login, radar
from tests.test_auth import body, run, visitor  # noqa: F401 - fixture

ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / "ui" / "login.py").read_text(encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────────
# What is gone
# ──────────────────────────────────────────────────────────────────────────
def test_the_charminar_and_the_catalogue_wall_are_gone():
    for relic in ("CHARMINAR", "tr-auth-skyline", "tr-auth-wall", "catalogue_view", "Charminar"):
        assert relic not in SOURCE, relic
    scene = login._scene() + login._backdrop()
    assert "skyline" not in scene and "charminar" not in scene.lower()


def test_the_left_box_is_gone_the_room_is_the_whole_screen():
    """The old visual was a box with ``overflow:hidden`` in the left column:
    its radar, sweep and strip stopped at a hard edge, a gutter away from
    the panel. None of that markup survives."""
    for relic in ("tr-auth-visual", "tr-auth-strip", "tr-auth-reel", "tr-auth-seats", 'class="scan"', "tr-radar hero"):
        assert relic not in SOURCE, relic


# ──────────────────────────────────────────────────────────────────────────
# Layer one: the room behind everything
# ──────────────────────────────────────────────────────────────────────────
def _rule(selector: str) -> str:
    return login.CSS.split(selector + " {")[1].split("}")[0]


def test_the_room_is_one_fixed_layer_the_size_of_the_screen():
    rule = _rule(".tr-auth-bg")
    assert "position:fixed" in rule and "inset:0" in rule and "z-index:0" in rule
    assert "pointer-events:none" in rule and "overflow:hidden" in rule
    bg = login._backdrop()
    assert bg.startswith('<div class="tr-auth-bg" aria-hidden="true">')
    # it rides in the top bar's block, ahead of everything it sits behind
    render = SOURCE.split("def render()")[1]
    assert "C.html(_backdrop() + _topbar())" in render


def test_the_room_is_layered_back_to_front():
    bg = login._backdrop()
    order = [bg.index(s) for s in ('class="glow"', 'class="rings"', 'class="sweep"', 'class="blip"',
                                    'class="core"', 'class="craft"', 'class="vignette"')]
    assert order == sorted(order)


def test_the_rings_are_static_vector_and_only_the_sweep_moves():
    svg = login._dish_svg()
    assert svg.count("<svg") == 1 and "<img" not in svg and "url(" not in svg
    assert svg.count("<circle r=") == len(login.BG_RINGS)
    assert svg.count('class="ticks"') == len(login.BG_TICKS) and 'class="orbit"' in svg
    assert "animation" not in _rule(".tr-auth-bg .rings")
    sweep = _rule(".tr-auth-bg .sweep")
    assert "tr-radar-sweep 6.5s linear infinite" in sweep and "will-change:transform" in sweep
    assert "--trr-sweep:6.5s" in radar.CSS and login.SWEEP_S == 6.5          # one clock for both dishes
    assert "mask-image" in sweep                                            # it fades before the panel


def test_contacts_light_as_the_sweep_reaches_them():
    bg = login._backdrop()
    delays = [float(d) for d in re.findall(r'class="blip" style="[^"]*animation-delay:([\d.]+)s"', bg)]
    assert delays == [round(deg / 360 * login.SWEEP_S, 2) for _, deg in login.BG_BLIPS]


def test_the_crafts_are_faint_decoration_in_the_room_not_controls():
    bg = login._backdrop()
    assert bg.count('class="craft"') == 24 == len(crafts.CRAFTS)
    assert "tabindex" not in bg and "data-tip" not in bg and "<button" not in bg and "<a " not in bg
    assert float(re.search(r"opacity:([\d.]+)", _rule(".tr-auth-bg .craft")).group(1)) <= .3


def test_reduced_motion_leaves_a_still_radar_not_a_blank_one():
    reduced = login.CSS.split("@media (prefers-reduced-motion: reduce) {")[-1].split("\n}")[0]
    assert "animation: none !important" in reduced
    assert re.search(r"\.tr-auth-bg \.sweep \{ transform: rotate\(-?\d+deg\); \}", reduced)
    assert ".tr-auth-bg .blip { opacity:" in reduced


# ──────────────────────────────────────────────────────────────────────────
# Layer two: posters and panel as one stage
# ──────────────────────────────────────────────────────────────────────────
def test_the_stage_is_one_centred_composition_with_no_gutter():
    shell = _rule('[class*="st-key-trauth_shell"]')
    for var in ("--tra-panel:460px", "--tra-gap:36px", "--tra-stage:min(1240px", "--tra-hw:min(640px"):
        assert var in shell, var
    # the panel column is fixed, the scene takes the rest, and the hand is
    # pushed to the scene's right edge — against the panel
    assert "flex:0 0 var(--tra-panel) !important" in login.CSS
    assert "margin-left:auto" in _rule(".tr-auth-posters")
    # the radar's centre is computed from the same variables
    bg = _rule(".tr-auth-bg")
    assert "var(--tra-wl)" in bg and "var(--tra-hw)" in bg


def test_the_panel_is_readable_glass_and_never_a_containing_block():
    panel = re.sub(r"/\*.*?\*/", "", _rule('[class*="st-key-trauth_panel"]'), flags=re.S)   # declarations only
    assert "backdrop-filter" not in panel and "transform" not in panel
    glass = _rule('[class*="st-key-trauth_panel"]::before')
    assert "backdrop-filter: blur(" in glass and "z-index:-1" in glass
    # the surface stays at least 88 % opaque so the sweep never crosses the text
    alphas = [float(a) for a in re.findall(r"rgba\(\d+,\d+,\d+,(\.\d+)\) \d+%", glass.split("linear-gradient(168deg")[1])]
    assert alphas and min(alphas) >= .88


def test_inputs_are_styled_on_both_streamlit_input_structures():
    """Streamlit 1.5x+ draws the field as ``stTextInputRootElement``; older
    ones as a baseweb input. The glyph, ring and eye toggle cover both."""
    for wanted in ('[data-testid="stTextInputRootElement"], [data-baseweb="input"]', ":focus-within", "button:focus-visible"):
        assert wanted in login.CSS, wanted


def test_the_forgot_link_never_wears_the_cta_gloss():
    assert '[class*="st-key-auth_forgot"] .stFormSubmitButton > button::before { display:none; }' in login.CSS


def test_every_poster_shows_most_of_itself():
    """Corners may overlap; nothing may cover a poster's middle."""
    boxes = dict(login.FAN)
    for a, (ax, ay, aw, ah, *_a) in boxes.items():
        for b_, (bx, by, bw, bh, *_b) in boxes.items():
            if a == b_:
                continue
            ox = max(0, min(ax + aw, bx + bw) - max(ax, bx))
            oy = max(0, min(ay + ah, by + bh) - max(ay, by))
            assert ox * oy <= .12 * aw * ah, f"{b_} covers {ox * oy / (aw * ah):.0%} of {a}"
    assert boxes["avengers_endgame"][5] == max(v[5] for v in boxes.values())   # the lead is in front
    for key, (x, y, w, h, *_rest) in boxes.items():
        assert 0 <= x and x + w <= login.HAND_W and 0 <= y and y + h <= login.HAND_H, key


def test_the_hover_lift_is_not_held_down_by_the_entrance_animation():
    card = login.CSS.split(".tr-auth-poster {")[1].split(".tr-auth-poster img")[0]
    assert "tr-auth-deal 1s var(--delay, 0s) var(--tr-ease) backwards" in card
    assert "translateY(-10px) scale(1.035)" in login.CSS.split(".tr-auth-poster:hover {")[1].split("}")[0]


def test_the_login_page_never_reads_the_catalogue_for_artwork():
    assert "cv." not in SOURCE and "catalogue" not in SOURCE.split('"""', 2)[2].lower().replace("catalogue's", "")


# ──────────────────────────────────────────────────────────────────────────
# The posters
# ──────────────────────────────────────────────────────────────────────────
def test_doraemon_replaces_avatar_from_the_supplied_file():
    assert art.poster("avatar") is None and "avatar" not in login.FAN
    doraemon = art.poster("doraemon")
    assert doraemon is not None and doraemon.path.name == "doraemon.jpg"     # exact case: Linux serves it
    assert doraemon.path.parent == art.POSTERS_DIR and doraemon.mime == "image/jpeg"
    assert art.static_url(doraemon).startswith("app/static/login/posters/doraemon.jpg?v=")
    assert [a.key for a in art.posters()] == list(art.LOGIN_POSTERS)          # six, no stray extra
    assert "doraemon" in login.FAN


def test_the_hand_holds_the_six_fixed_posters_served_from_static():
    hand = login._hand()
    srcs = re.findall(r'srcset="([^"]+)"', hand)
    assert len(srcs) == 6
    for asset, src in zip(art.posters(), srcs):
        assert src == art.static_url(asset)
        assert src.startswith("app/static/login/posters/") and "?v=" in src
    assert "bookmyshow" not in hand.lower() and "data:image/jpeg" not in hand
    # every card downloads only on the viewport that shows it
    assert hand.count(f'<source media="{login.DESKTOP_MEDIA}"') == 6
    assert hand.count(f'<img src="{login.BLANK}"') == 6
    # the lead card is the one that catches the sheen, and every card has its own slot
    assert hand.count('class="sheen"') == 1 and 'tr-auth-poster lead' in hand
    assert len(set(re.findall(r"left:(-?[\d.]+)%;top:(-?[\d.]+)%", hand))) == 6
    assert 'TICKETS<br>DETECTED' in hand


def test_the_hand_scales_by_percentages_not_a_transform():
    hand = login._hand()
    assert "px;" not in hand.split('class="hand">')[1].split('class="detect"')[0].replace("translateY", "")
    left, top, width, height, *_ = login.FAN["avengers_endgame"]
    assert (f"left:{left / login.HAND_W * 100:.2f}%;top:{top / login.HAND_H * 100:.2f}%;"
            f"width:{width / login.HAND_W * 100:.2f}%;height:{height / login.HAND_H * 100:.2f}%") in hand
    assert "aspect-ratio:640 / 440" in _rule(".tr-auth-posters")


def test_captions_name_the_films_readably():
    hand = login._hand()
    for caption in ("Avengers: Endgame", "Interstellar", "Doraemon", "RRR", "Baahubali", "Spider-Man"):
        assert f'<div class="t">{caption}</div>' in hand
    assert "Avatar" not in hand


def test_an_extra_poster_in_the_folder_takes_a_back_slot(monkeypatch):
    extra = art.Asset(key="dune", kind="poster", category="login", path=art.POSTERS_DIR / "rrr.jpg")
    monkeypatch.setattr(art, "posters", lambda: list(art._posters().values()) + [extra])
    hand = login._hand()
    assert hand.count("<picture>") == 7
    assert f"left:{login._pct(login.EXTRA_SLOTS[0][0], login.HAND_W)}" in hand


def test_the_phone_strip_is_three_posters_positioned_by_css():
    strip = login._phone_strip()
    assert strip.count("<picture>") == 3
    assert strip.count(f'<source media="{login.PHONE_MEDIA}"') == 3
    assert "style=" not in strip                                # nth-child places them
    keys = [k for k in login.PHONE_POSTERS]
    assert [art.static_url(art.poster(k)) for k in keys] == re.findall(r'srcset="([^"]+)"', strip)


def test_the_desktop_and_phone_breakpoints_meet_exactly():
    assert login.DESKTOP_MEDIA == "(min-width: 901px)" and login.PHONE_MEDIA == "(max-width: 900px)"
    stack = login.CSS.split("@media (max-width: 900px) {")[1].split("\n}")[0]
    assert '[data-testid="stColumn"]:first-child { display:none !important; }' in stack
    assert ".tr-auth-mhead { display:flex;" in stack
    assert "font-size:16px !important" in stack                         # no iOS zoom on focus


# ──────────────────────────────────────────────────────────────────────────
# The radar
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("state", radar.STATES)
def test_the_radar_is_one_svg_with_its_state_on_the_wrapper(state):
    svg = radar.svg(size=200, state=state, cls="x")
    assert svg.startswith(f'<div class="tr-radar x" data-state="{state}" style="width:200px"')
    assert svg.count("<svg") == 1 and 'viewBox="0 0 400 400"' in svg
    for part in ("dish", "ticks", "ring r4", "ring r1", "axis", "orbit", "sweep", "blip", "core"):
        assert part in svg, part
    assert f'.tr-radar[data-state="{state}"]' in radar.CSS or state == "scanning"


def test_an_unknown_state_falls_back_to_scanning_and_a_tag_is_optional():
    assert 'data-state="scanning"' in radar.svg(state="bogus")
    assert '<div class="tag">' not in radar.svg()
    assert '<div class="tag">TICKETS<br>DETECTED</div>' in radar.svg(tag="TICKETS<br>DETECTED")


def test_two_radars_on_one_page_never_share_a_paint_server():
    hero, mini = radar.svg(size=340, cls="hero"), radar.svg(size=124, cls="mini")
    ids = lambda s: set(re.findall(r'id="([^"]+)"', s))  # noqa: E731
    assert ids(hero) and ids(mini) and not ids(hero) & ids(mini)
    for s in (hero, mini):
        for i in ids(s):
            assert f"url(#{i})" in s


def test_the_radar_animates_transform_and_opacity_only():
    for line in radar.CSS.splitlines():
        if "@keyframes" in line:
            body_ = line.split("{", 1)[1]
            props = {p.split(":")[0].strip() for p in re.findall(r"([a-z-]+):", body_)}
            assert props <= {"transform", "opacity"}, line
    assert "prefers-reduced-motion" in radar.CSS
    assert "no image" in radar.__doc__.lower() or "no radar *image*" in radar.__doc__


def test_the_small_mark_drops_the_fine_detail():
    assert ".tr-radar.mark .ticks" in radar.CSS and "display:none" in radar.CSS
    assert 'cls="mark brandmark"' in SOURCE


# ──────────────────────────────────────────────────────────────────────────
# The crafts
# ──────────────────────────────────────────────────────────────────────────
def test_there_are_twenty_four_crafts_each_with_a_line_of_its_own():
    assert len(crafts.CRAFTS) == 24 == len(crafts.LAYOUT)
    assert len({c.key for c in crafts.CRAFTS}) == 24
    for c in crafts.CRAFTS:
        assert c.label and c.blurb.endswith(".") and c.tip == f"{c.label} — {c.blurb}"
        assert "<path" in c.paths or "<circle" in c.paths or "<rect" in c.paths
    for x, y in crafts.LAYOUT:
        assert 0 <= x <= 100 and (y is None or 0 <= y <= 100)
    assert sum(y is None for _, y in crafts.LAYOUT) == 3          # the ledge under the radar


def test_the_brief_s_crafts_are_all_there():
    names = {c.label.lower() for c in crafts.CRAFTS}
    for wanted in ("direction", "cinematography", "screenwriting", "producing", "acting", "editing",
                   "production design", "art direction", "costume design", "makeup", "hair", "sound", "music",
                   "sound mixing", "lighting", "set design", "visual effects", "special effects", "choreography",
                   "stunts", "casting", "colour grading", "projection", "distribution"):
        assert wanted in names, wanted


def test_the_constellation_has_tooltips_that_work_without_script():
    html = crafts.constellation()
    assert html.count('class="tr-craft ') == 24
    assert html.count('tabindex="0"') == 24                    # keyboard reaches every one
    assert 'data-tip="Cinematography — The visual language of a film."' in html
    assert 'data-tip="Sound — Everything you hear beyond the image."' in html
    assert "content:attr(data-tip)" in crafts.CSS and ":focus-visible::after" in crafts.CSS
    assert "<script" not in html
    # the ones near an edge open inward
    assert re.search(r'class="tr-craft ledge tip-r" style="left:17%"', html)        # no top: the ledge places it
    assert re.search(r'class="tr-craft tip-l" style="left:97%;top:65%"', html)
    assert re.search(r'class="tr-craft tip-l tip-b" style="left:90%;top:4%"', html)


def test_the_phone_strip_is_decoration_only():
    strip = crafts.strip()
    assert strip.count("<svg") == 8 and "data-tip" not in strip and 'aria-hidden="true"' in strip


# ──────────────────────────────────────────────────────────────────────────
# The page
# ──────────────────────────────────────────────────────────────────────────
def test_the_scene_is_posters_then_the_headline():
    scene = login._scene()
    order = [scene.index(s) for s in ('class="tr-auth-posters"', 'class="detect"', "Never Miss", 'class="tr-auth-feats"')]
    assert order == sorted(order)
    assert scene.count("<picture>") == 6 and "HYDERABAD" in scene and "Charminar" not in scene
    assert "tr-craft" not in scene and "<svg" not in scene.split('class="tr-auth-feats"')[0]


def test_the_stylesheet_is_one_payload_that_carries_the_radar_once():
    assert login.CSS.count("<style>") == 1
    assert login.CSS.count(".tr-radar .sweep { transform-box") == 1
    assert ".tr-craft::after" not in login.CSS              # the crafts are decoration now: no tooltip CSS rides along
    assert len(login.CSS) < 60_000


def test_the_wait_is_a_line_at_once_and_a_veil_only_if_it_drags(monkeypatch):
    rendered = []
    placeholder = type("P", (), {"markdown": lambda self, m, **k: rendered.append(m)})()
    login._busy(placeholder, "TUNING INTO THE RADAR…")
    (html,) = rendered
    assert '<div class="tr-auth-busy">' in html and '<div class="tr-auth-veil"' in html
    assert html.count('data-state="detecting"') == 2
    assert "TUNING INTO THE RADAR…" in html and "<script" not in html
    assert not any(line.startswith("    ") for line in html.splitlines())   # never a Markdown code block
    # the veil is invisible until the delay has run, purely in CSS — no sleep anywhere here
    assert re.search(r"\.tr-auth-veil \{[^}]*opacity:0; animation: tr-auth-fade \.35s \.6s", login.CSS)
    assert "time.sleep" not in SOURCE.split("def _busy")[1].split("def _attempt")[0]
    assert "position:fixed" in login.CSS.split(".tr-auth-veil {")[1].split("}")[0]


def test_the_busy_copy_is_cinematic():
    for line in ("TUNING INTO THE RADAR…", "CONNECTING TO TICKETRADAR…", "SENDING YOUR RESET LINK…"):
        assert line in SOURCE, line


def test_the_success_beat_is_the_radar_in_mint(monkeypatch):
    rendered = []
    placeholder = type("P", (), {"markdown": lambda self, m, **k: rendered.append(m)})()
    monkeypatch.setattr(login.time, "sleep", lambda s: None)
    monkeypatch.setattr(login.st, "rerun", lambda: None)
    user = type("U", (), {"first_name": "Ravi"})()
    login._welcome(placeholder, user)
    assert 'data-state="success"' in rendered[0] and "WELCOME, RAVI" in rendered[0]


def test_static_serving_is_on_so_the_browser_caches_the_posters():
    config = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert re.search(r"^enableStaticServing = true$", config, flags=re.M)
    assert (ROOT / "static" / "login" / "posters").is_dir()


def test_the_rendered_login_page_carries_the_room_and_no_base64(visitor):
    app = run()
    assert not app.exception, [str(e) for e in app.exception]
    text = " ".join(m.value for m in app.markdown)
    assert text.count("app/static/login/posters/") == 9                 # six in the hand, three for the phone
    assert "data:image/jpeg" not in text and "data:image/png" not in text
    assert text.count('class="craft"') == 24                             # on the dish, behind everything
    assert text.count('class="tr-auth-bg"') == 1
    assert text.count('class="tr-radar') == 1                            # the panel mark; the room draws its own dish
    assert "TICKETS<br>DETECTED" in text
    assert text.count("<style>") == 2                                    # the theme and this page, once each
