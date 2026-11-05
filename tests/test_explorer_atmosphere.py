"""The Explorer's atmosphere: Home's twenty-four crafts, explaining formats,
and a line of recent Indian cinema.

Home places its twenty-four craft marks in the page's negative space; the
Explorer does the same with the same marks — eight in the band above the hero
(Home's own band coordinates), sixteen in a ledge under the results. Each mark
explains one cinema format (``content.format_directory``) in Home's card, on
hover, keyboard focus or a tap. Under the hero's copy, one fixed slot shows a
line from recent Indian cinema (``content.explorer_dialogues``), with its
film, year, speaker and language, one at a time by CSS alone.
"""

from __future__ import annotations

import re
from pathlib import Path

from content import explorer_dialogues, format_directory, home_dialogues
from tests.test_explorer_by_theatre import amb_page, ok, run  # noqa: F401 - fixture
from ui import crafts, home
from ui import explorer as ux


def main_markup(app) -> str:
    return re.sub(r"<style>.*?</style>", "", " ".join(m.value for m in app.main.markdown), flags=re.DOTALL)


def test_all_twenty_four_crafts_once_each_band_and_ledge():
    keys = [k for zone in ("band", "foot") for k, _x, _y in ux.CRAFT_ROWS[zone]]
    assert len(keys) == 24 and set(keys) == set(crafts.BY_KEY)
    assert [(k, x, y) for k, x, y in ux.CRAFT_ROWS["band"]] == [
        (k, x, y) for k, (z, x, y) in home.HOME_LAYOUT.items() if z == "band"]          # Home's own band
    for zone in ("band", "foot"):
        for _k, x, y in ux.CRAFT_ROWS[zone]:
            assert 0 <= x <= 100 and 0 <= y <= 100


def test_the_crafts_are_homes_marks_and_now_explain_formats():
    band = ux.craft_zone("band")
    marks = re.findall(r'<span class="tr-craft[^"]*" style="left:([\d.]+)%;top:(\d+)%" tabindex="0" role="img"', band)
    assert [(float(x), int(y)) for x, y in marks] == [(x, round(y)) for _k, x, y in ux.CRAFT_ROWS["band"]]
    for key, _x, _y in ux.CRAFT_ROWS["band"]:
        assert home._icon(crafts.BY_KEY[key]) in band                                   # Home's icon, as is
        assert f'<span class="t">{format_directory.BY_CRAFT[key].name.replace("&", "&amp;")}</span>' in band
    assert "data-tip" not in band                                                       # one card, not two
    css = ux.AMBIENCE_CSS
    assert ".tr-sx-crafts .tr-craft { opacity:.42; pointer-events:auto;" in css          # Home's resting opacity
    assert ".tr-sx-crafts { position:relative; pointer-events:none; }" in css           # the zone itself stays out of the way
    for state in (":hover", ":focus", ":focus-visible"):
        assert f".tr-sx-crafts .tr-craft{state} .tr-sub" in css
    assert ".tr-sx-crafts .tr-craft.tip-b .tr-sub { bottom:auto; top:calc(100% + 12px); }" in css


def test_the_explorer_marks_keep_the_exact_positions_of_the_approved_baseline():
    import subprocess

    old = subprocess.run(["git", "show", "080ce6e:ui/explorer.py"], capture_output=True, text=True, check=True).stdout
    start = old.index("CRAFT_ROWS = {")
    assert old[start:old.index("\n}\n", start) + 2] in Path("ui/explorer.py").read_text(encoding="utf-8")


def test_the_tribute_lines_are_recent_indian_and_credited():
    langs = {d.language for d in ux.LINES}
    assert {"Telugu", "Hindi", "Tamil", "Malayalam"} <= {lang.split(" ")[0] for lang in langs}
    for d in ux.LINES:
        assert d.year >= 2022 and d.film and d.speaker and d.source and d.status == "verified"
        assert d.material in {"film", "trailer", "teaser", "glimpse", "promo"}
    assert ux.LINES is explorer_dialogues.DIALOGUES
    assert not {d.line for d in ux.LINES} & {d.line for d in home_dialogues.DIALOGUES}   # never mixed
    markup = ux.lines_markup()
    assert markup.count('class="ln"') == len(ux.LINES) and 'aria-hidden="true"' in markup
    delays = [int(d) for d in re.findall(r"animation-delay:(\d+)s", markup)]
    assert delays == [i * ux.LINE_SECONDS for i in range(len(ux.LINES))]


def test_the_rotation_is_css_only_fixed_height_and_still_when_asked():
    css = ux.AMBIENCE_CSS
    assert "<script" not in css and "setInterval" not in css
    slot = re.search(r"\.tr-sx-lines \{([^}]*)\}", css).group(1)
    assert "height:50px" in slot and "overflow:hidden" in slot                           # no layout shift
    assert f"animation: tr-sx-line {len(ux.LINES) * ux.LINE_SECONDS}s linear infinite" in css
    keyframes = re.search(r"@keyframes tr-sx-line \{(.*)\}", css).group(1)
    props = {d.split(":")[0].strip() for d in re.findall(r"\{([^}]*)\}", keyframes) for d in d.split(";") if ":" in d}
    assert props <= {"opacity", "transform"}
    reduced = css[css.index("@media (prefers-reduced-motion: reduce)"):]
    assert ".tr-sx-lines .ln" in reduced and ".tr-sx-lines .ln:first-child { opacity:1; }" in reduced
    phone = css[css.index("@media (max-width: 768px)"):]
    assert ".tr-sx-hero-radar, .tr-sx-crafts { display:none; }" in phone                 # the positioned zones rest on a phone
    assert ".tr-sx-lines { height:90px;" in phone                                        # wrapped lines, fixed


def test_a_phone_gets_the_same_24_marks_as_a_strip_with_unclipped_cards():
    strip = ux.format_strip()
    assert strip.count('tabindex="0"') == 24
    phone = ux.AMBIENCE_CSS[ux.AMBIENCE_CSS.index("@media (max-width: 768px)"):]
    assert ".tr-sx-fxstrip { display:block; position:relative;" in phone
    assert ".tr-sx-fxstrip .tr-craft { position:static;" in phone                        # the card anchors to the box
    assert "left:0; right:0; top:auto; bottom:calc(100% + 6px); width:auto; max-width:none;" in phone   # opens upward, over the page


def test_the_page_draws_its_atmosphere_and_keeps_its_controls(amb_page):
    app = ok(run())
    html = main_markup(app)
    assert html.count('class="tr-sx-crafts band"') == 1 and html.count('class="tr-sx-crafts foot"') == 1
    assert html.count('<span class="tr-craft') == 48                                      # 24 placed, 24 in the phone strip
    assert 'class="tr-sx-lines"' in html and "Devara: Part 1 · 2024 · Devara (Jr NTR) · Telugu" in html
    assert "<h1>Showtime Explorer</h1>" in html and "tr-hero-mark" in html
    # nothing of the interface moved
    for key in ("sxmode_movie", "sxmode_theatre"):
        assert app.button(key=key) is not None
    assert app.selectbox(key="sx_movie") is not None
    assert {b.key for b in app.button if b.key and b.key.startswith("sxpre_")} == {
        "sxpre_morning", "sxpre_afternoon", "sxpre_evening", "sxpre_night", "sxpre_all_day"}


def test_every_line_and_card_is_escaped(monkeypatch):
    from content import Dialogue

    monkeypatch.setattr(ux, "LINES", (Dialogue('<b>"x"</b>', "<i>f</i>", 2024, "s", "Telugu", "src"),))
    markup = ux.lines_markup()
    assert "<b>" not in markup and "&lt;b&gt;" in markup and "&lt;i&gt;" in markup
    evil = format_directory.Format("<b>X</b>", format_directory.SOUND, format_directory.INTERNATIONAL, "<i>d</i>", "s")
    monkeypatch.setitem(format_directory.BY_CRAFT, "direction", evil)
    mark = ux.craft_zone("band")
    assert "<b>X</b>" not in mark and "&lt;b&gt;X" in mark and "<i>d</i>" not in mark
