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
    visual = login._visual()
    assert "skyline" not in visual and "charminar" not in visual.lower()


# ──────────────────────────────────────────────────────────────────────────
# The room
# ──────────────────────────────────────────────────────────────────────────
def test_the_room_is_layered_back_to_front():
    visual = login._visual()
    order = [visual.index(s) for s in ('class="beam"', 'class="scan"', 'class="tr-auth-strip"', 'class="tr-auth-reel"',
                                        'class="tr-radar hero"', 'class="tr-auth-posters"', 'class="tr-crafts"',
                                        'class="tr-auth-copy"', 'class="tr-auth-seats"', 'class="tr-auth-mark"')]
    assert order == sorted(order)


def test_the_film_strip_is_one_path_stroked_not_an_image():
    strip = login._strip()
    assert strip.count(f'd="{login.STRIP_PATH}"') == 5                 # halo, band, holes, film, frames
    assert "<img" not in strip and "url(" not in strip
    assert ".tr-auth-strip .holes" in login.CSS and "stroke-dasharray:7 15" in login.CSS


def test_the_seats_are_two_rows_of_css_that_fade_into_the_floor():
    seats = login._seats()
    assert seats.count("<i></i>") == 20 and 'class="row back"' in seats and 'class="row front"' in seats
    assert 'aria-hidden="true"' in seats
    rule = login.CSS.split(".tr-auth-seats {")[1].split("}")[0]
    assert "mask-image" in rule and "pointer-events:none" in rule
    assert "rgba(255,100,130,.42)" in login.CSS                          # the rim light


def test_the_sweep_continues_across_the_room_in_step_with_the_dish():
    scan = login.CSS.split(".tr-auth-visual .scan {")[1].split("}")[0]
    assert "tr-radar-sweep 6.5s linear infinite" in scan               # the same clock as radar.CSS
    assert "--trr-sweep:6.5s" in radar.CSS


def test_the_aperture_is_a_faint_vector_in_the_negative_space():
    reel = login._reel()
    assert reel.count("<circle") == 9 and "<img" not in reel
    assert "opacity:.05" in login.CSS.split(".tr-auth-reel {")[1].split("}")[0]


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
    assert boxes["spiderman_brand_new_day"][0] + boxes["spiderman_brand_new_day"][2] <= 600


def test_the_hover_lift_is_not_held_down_by_the_entrance_animation():
    card = login.CSS.split(".tr-auth-poster {")[1].split(".tr-auth-poster img")[0]
    assert "tr-auth-deal 1s var(--delay, 0s) var(--tr-ease) backwards" in card
    assert "translateY(-10px) scale(1.035)" in login.CSS.split(".tr-auth-poster:hover {")[1].split("}")[0]


def test_the_login_page_never_reads_the_catalogue_for_artwork():
    assert "cv." not in SOURCE and "catalogue" not in SOURCE.split('"""', 2)[2].lower().replace("catalogue's", "")


# ──────────────────────────────────────────────────────────────────────────
# The posters
# ──────────────────────────────────────────────────────────────────────────
def test_the_hand_holds_the_six_fixed_posters_served_from_static():
    hand = login._hand()
    srcs = re.findall(r'srcset="([^"]+)"', hand)
    assert len(srcs) == 6
    for asset, src in zip(art.posters(), srcs):
        assert src == art.static_url(asset)
        assert src.startswith("app/static/login/posters/") and "?v=" in src
    assert "bookmyshow" not in hand.lower() and "data:image/jpeg" not in hand
    # every card downloads only on the viewport that shows it
    assert hand.count('<source media="(min-width: 1101px)"') == 6
    assert hand.count(f'<img src="{login.BLANK}"') == 6
    # the lead card is the one that catches the sheen, and every card has its own slot
    assert hand.count('class="sheen"') == 1 and 'tr-auth-poster lead' in hand
    assert len(set(re.findall(r"left:(-?\d+)px;top:(-?\d+)px", hand))) == 6


def test_captions_name_the_films_readably():
    hand = login._hand()
    for caption in ("Avengers: Endgame", "Interstellar", "Avatar", "RRR", "Baahubali", "Spider-Man"):
        assert f'<div class="t">{caption}</div>' in hand


def test_an_extra_poster_in_the_folder_takes_a_back_slot(monkeypatch):
    extra = art.Asset(key="dune", kind="poster", category="login", path=art.POSTERS_DIR / "rrr.jpg")
    monkeypatch.setattr(art, "posters", lambda: art.posters.__wrapped__() if hasattr(art.posters, "__wrapped__") else list(art._posters().values()) + [extra])
    hand = login._hand()
    assert hand.count("<picture>") == 7
    assert f"left:{login.EXTRA_SLOTS[0][0]}px" in hand


def test_the_phone_strip_is_three_posters_positioned_by_css():
    strip = login._phone_strip()
    assert strip.count("<picture>") == 3
    assert strip.count('<source media="(max-width: 768px)"') == 3
    assert "style=" not in strip                                # nth-child places them
    keys = [k for k in login.PHONE_POSTERS]
    assert [art.static_url(art.poster(k)) for k in keys] == re.findall(r'srcset="([^"]+)"', strip)


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
def test_the_visual_is_radar_first_then_posters_crafts_and_the_headline():
    visual = login._visual()
    order = [visual.index(s) for s in ('class="tr-radar hero"', 'class="tr-auth-posters"', 'class="tr-crafts"', "Never Miss")]
    assert order == sorted(order)
    assert 'data-state="scanning"' in visual
    assert visual.count('class="tr-craft ') == 24 and visual.count("<picture>") == 6
    assert "HYDERABAD" in visual and "Charminar" not in visual
    assert visual.count('class="tr-craft ledge') == 3


def test_the_stylesheet_is_one_payload_that_carries_the_radar_and_the_crafts_once():
    assert login.CSS.count("<style>") == 1
    assert login.CSS.count(".tr-radar .sweep { transform-box") == 1 and login.CSS.count(".tr-craft::after { content:attr") == 1
    assert len(login.CSS) < 60_000
    panel = login.CSS.split('[class*="st-key-trauth_panel"] {')[1].split("}")[0]
    assert "backdrop-filter" not in panel and "transform" not in panel.split("/*")[0]   # charcoal, not glass; no containing block


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
    assert text.count('class="tr-craft ') == 24
    assert text.count('class="tr-radar') == 3                            # hero, phone mini, panel mark
    assert "TICKETS<br>DETECTED" in text
    assert text.count("<style>") == 2                                    # the theme and this page, once each
