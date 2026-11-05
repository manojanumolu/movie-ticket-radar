"""The Explorer's atmosphere: a line of Indian cinema, and the formats explained.

Under the hero's copy, one fixed slot shows a short line from Indian cinema —
from the audited collection in ``ui.dialogues`` — with its film, year and
language, one at a time by CSS alone. Below the results, the decorative craft
marks became an educational section: premium formats in two separate groups
(Hyderabad's screen brands; international formats), each a focusable chip
whose card opens on hover, keyboard focus or a tap. It is reference only —
nothing in it reaches monitoring, the wizard's Formats step or the provider.
"""

from __future__ import annotations

import itertools
import re
from pathlib import Path

from tests.test_explorer_by_theatre import amb_page, ok, run  # noqa: F401 - fixture
from ui import dialogues, explorer as ux, formats

INDIAN = {"Telugu", "Hindi", "Tamil", "Malayalam", "Kannada · said in English", "Bengali"}


def main_markup(app) -> str:
    return re.sub(r"<style>.*?</style>", "", " ".join(m.value for m in app.main.markdown), flags=re.S)


# ── the line under the hero ────────────────────────────────────────────────
def test_the_tribute_lines_are_indian_short_attributed_and_varied():
    entries = [dialogues.BY_KEY[k] for k in ux.ROTATION]
    langs = {d.language for d in entries}
    assert langs <= INDIAN and {"Telugu", "Hindi", "Tamil", "Malayalam", "Bengali"} <= langs
    for d in entries:
        assert dialogues.words(d.line) <= dialogues.MAX_WORDS and d.film and d.year
    for a, b in itertools.pairwise(entries):
        assert a.language != b.language                                                  # a change at every turn
    markup = dialogues.rotation_markup(ux.ROTATION, "tr-dlg-sx")
    assert markup.count('class="ln"') == len(ux.ROTATION) and 'aria-hidden="true"' in markup
    delays = [int(d) for d in re.findall(r"animation-delay:(\d+)s", markup)]
    assert delays == [i * dialogues.LINE_SECONDS for i in range(len(ux.ROTATION))]


def test_the_rotation_is_css_only_fixed_height_and_still_when_asked():
    css = ux.AMBIENCE_CSS
    assert "<script" not in css and "setInterval" not in css
    slot = re.search(r"\.tr-dlg \{([^}]*)\}", css).group(1)
    assert "height:46px" in slot and "overflow:hidden" in slot                           # no layout shift
    assert f"animation: tr-dlg-sx-turn {len(ux.ROTATION) * dialogues.LINE_SECONDS}s linear infinite" in css
    keyframes = re.search(r"@keyframes tr-dlg-sx-turn \{(.*?)\n\}", css, re.S).group(1)
    props = {d.split(":")[0].strip() for d in re.findall(r"\{([^}]*)\}", keyframes) for d in d.split(";") if ":" in d}
    assert props <= {"opacity", "transform"}
    reduced = css[css.index("@media (prefers-reduced-motion: reduce) {\n  .tr-dlg-sx"):]
    assert ".tr-dlg-sx .ln { animation:none; }" in reduced and ".tr-dlg-sx .ln:first-child { opacity:1; }" in reduced
    phone = dialogues.CSS[dialogues.CSS.index("@media (max-width: 768px)"):]
    assert ".tr-dlg { height:82px;" in phone                                             # wrapped, still fixed


def test_every_line_is_escaped(monkeypatch):
    evil = dialogues.Dialogue("x", '<b>"x"</b>', "<i>t</i>", "<i>f</i>", 2000, "Telugu", "s", "India")
    monkeypatch.setitem(dialogues.BY_KEY, "pushpa", evil)
    markup = dialogues.rotation_markup(("pushpa",), "tr-dlg-sx")
    assert "<b>" not in markup and "<i>" not in markup and "&lt;b&gt;" in markup and "&lt;i&gt;" in markup


# ── the formats, explained ─────────────────────────────────────────────────
def test_two_separate_groups_each_format_once():
    a = [f.name for f in formats.FORMATS if f.group == "A"]
    b = [f.name for f in formats.FORMATS if f.group == "B"]
    assert {"P[XL]", "PCX", "EPIQ", "Infinity Vision", "MB LUXE", "VIP"} <= set(a)
    assert {"IMAX", "Dolby Cinema", "4DX", "ScreenX", "MX4D", "D-BOX", "HDR by Barco"} <= set(b)
    assert not set(a) & set(b) and len(a) + len(b) == len(formats.FORMATS)
    html = formats.section()
    assert html.count('class="tr-fx-group a"') == 1 and html.count('class="tr-fx-group b"') == 1
    assert html.index('tr-fx-group a') < html.index('tr-fx-group b')
    assert html.count('<li class="tr-fx" tabindex="0">') == len(formats.FORMATS)
    for f in formats.FORMATS:
        assert len(f.blurb) <= 170 and f.blurb.endswith("."), f.name                     # short, a sentence or two


def test_each_card_names_the_format_its_group_and_what_it_is():
    html = formats.section()
    chip = re.search(r'<li class="tr-fx" tabindex="0">(?:(?!</li>).)*IMAX(?:(?!</li>).)*</li>', html).group()
    assert '<span class="tip" role="tooltip"><span class="t">IMAX</span><span class="g">International formats</span>' in chip
    assert "Dolby Vision HDR laser projection" in html and "Hyderabad&#x27;s screen brands" in html
    assert 'aria-label="Premium formats, explained"' in html


def test_the_note_follows_the_verified_data_and_claims_nothing_more():
    from config.theatre_capabilities import PREMIUM_SCREENS

    named = {formats._norm(f) for _p, screens in PREMIUM_SCREENS.values() for f in screens}
    for f in formats.FORMATS:
        assert formats.in_hyderabad_data(f) == (formats._norm(f.match or f.name) in named), f.name
    assert formats.in_hyderabad_data(next(f for f in formats.FORMATS if f.name == "P[XL]"))
    assert not formats.in_hyderabad_data(next(f for f in formats.FORMATS if f.name == "IMAX"))
    assert not formats.in_hyderabad_data(next(f for f in formats.FORMATS if f.name == "Infinity Vision"))
    html = formats.section()
    for word in ("available", "now showing", "book now", "currently"):
        assert word not in html.lower(), word
    assert html.count('class="data"') == sum(formats.in_hyderabad_data(f) for f in formats.FORMATS)


def test_the_section_is_reference_only():
    src = Path("ui/formats.py").read_text(encoding="utf-8")
    for forbidden in ("import streamlit", "st.", "monitor", "platforms", "requests", "PREMIUM_SCREENS[", "<script"):
        assert forbidden not in src.split('"""', 2)[2].replace("monitoring", ""), forbidden
    for path in ("ui/flow.py", "monitor", "platforms", "explorer"):
        for p in Path(path).rglob("*.py") if Path(path).is_dir() else [Path(path)]:
            assert "ui.formats" not in p.read_text(encoding="utf-8") and "from ui import formats" not in p.read_text(encoding="utf-8"), p


def test_hover_focus_and_tap_open_the_card_with_one_beat_of_motion():
    css = formats.CSS
    assert ".tr-fx:hover .tip, .tr-fx:focus .tip, .tr-fx:focus-within .tip { opacity:1; visibility:visible;" in css
    assert ".tr-fx:focus-visible { box-shadow:0 0 0 2px" in css                         # a visible keyboard ring
    assert "animation: tr-fx-nod .5s var(--tr-ease) 1;" in css                           # once, never a loop
    assert "infinite" not in css and "filter" not in css
    assert "max-width:min(300px, calc(100vw - 40px))" in css                             # never wider than the window
    reduced = css[css.index("@media (prefers-reduced-motion: reduce)"):]
    assert ".tr-fx:hover .ic, .tr-fx:focus .ic { animation:none; }" in reduced
    phone = css[css.index("@media (max-width: 768px)"):]
    assert "min-height:44px" in phone                                                    # a finger-sized target


def test_the_page_draws_its_atmosphere_and_keeps_its_controls(amb_page):
    app = ok(run())
    html = main_markup(app)
    assert 'class="tr-sx-crafts' not in html and 'class="tr-craft"' not in html         # the decoration is gone
    assert html.count('class="tr-fx-wrap"') == 1 and html.count('class="tr-fx"') == len(formats.FORMATS)
    assert 'class="tr-dlg tr-dlg-sx"' in html and "Thaggedhe le!" in html and "Pushpa: The Rise (2021) · Telugu" in html
    assert "<h1>Showtime Explorer</h1>" in html and "tr-hero-mark" in html
    # nothing of the interface moved
    for key in ("sxmode_movie", "sxmode_theatre"):
        assert app.button(key=key) is not None
    assert app.selectbox(key="sx_movie") is not None
    assert {b.key for b in app.button if b.key and b.key.startswith("sxpre_")} == {
        "sxpre_morning", "sxpre_afternoon", "sxpre_evening", "sxpre_night", "sxpre_all_day"}
