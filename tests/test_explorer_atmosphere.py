"""The Explorer's atmosphere: Home's twenty-four crafts and a line of cinema.

Home places its twenty-four craft marks in the page's negative space; the
Explorer does the same with the same marks — eight in the band above the hero
(Home's own band coordinates), sixteen in a ledge under the results — as
decoration only. Under the hero's copy, one fixed slot shows a short line from
Indian cinema, with its film and language, one at a time by CSS alone.
"""

from __future__ import annotations

import re


from tests.test_explorer_by_theatre import amb_page, ok, run  # noqa: F401 - fixture
from ui import crafts, explorer as ux, home


def main_markup(app) -> str:
    return re.sub(r"<style>.*?</style>", "", " ".join(m.value for m in app.main.markdown), flags=re.S)


def test_all_twenty_four_crafts_once_each_band_and_ledge():
    keys = [k for zone in ("band", "foot") for k, _x, _y in ux.CRAFT_ROWS[zone]]
    assert len(keys) == 24 and set(keys) == set(crafts.BY_KEY)
    assert [(k, x, y) for k, x, y in ux.CRAFT_ROWS["band"]] == [
        (k, x, y) for k, (z, x, y) in home.HOME_LAYOUT.items() if z == "band"]          # Home's own band
    for zone in ("band", "foot"):
        for _k, x, y in ux.CRAFT_ROWS[zone]:
            assert 0 <= x <= 100 and 0 <= y <= 100


def test_the_crafts_are_homes_marks_and_purely_decorative():
    band = ux.craft_zone("band")
    assert band.count('class="tr-craft"') == 8 and 'aria-hidden="true"' in band
    assert "tabindex" not in band and "tr-sub" not in band and "data-tip" not in band   # no tooltip, no focus
    for key, _x, _y in ux.CRAFT_ROWS["band"]:
        assert home._icon(crafts.BY_KEY[key]) in band                                   # Home's icon, as is
    css = ux.AMBIENCE_CSS
    assert ".tr-sx-crafts .tr-craft { opacity:.42; pointer-events:none; }" in css        # Home's opacity
    assert ".tr-sx-crafts { position:relative; pointer-events:none; }" in css


def test_the_tribute_lines_are_short_attributed_and_varied():
    langs = {lang for _q, _f, lang in ux.LINES}
    assert {"Telugu", "Hindi", "Tamil", "Malayalam", "Kannada"} <= langs
    for quote, film, lang in ux.LINES:
        assert len(quote.split()) <= 10 and film and lang
    markup = ux.lines_markup()
    assert markup.count('class="ln"') == len(ux.LINES) and 'aria-hidden="true"' in markup
    delays = [int(d) for d in re.findall(r"animation-delay:(\d+)s", markup)]
    assert delays == [i * ux.LINE_SECONDS for i in range(len(ux.LINES))]


def test_the_rotation_is_css_only_fixed_height_and_still_when_asked():
    css = ux.AMBIENCE_CSS
    assert "<script" not in css and "setInterval" not in css
    slot = re.search(r"\.tr-sx-lines \{([^}]*)\}", css).group(1)
    assert "height:20px" in slot and "overflow:hidden" in slot                           # no layout shift
    assert f"animation: tr-sx-line {len(ux.LINES) * ux.LINE_SECONDS}s linear infinite" in css
    keyframes = re.search(r"@keyframes tr-sx-line \{(.*?)\}\s*\n", css, re.S).group(1)
    props = {d.split(":")[0].strip() for d in re.findall(r"\{([^}]*)\}", keyframes) for d in d.split(";") if ":" in d}
    assert props <= {"opacity", "transform"}
    reduced = css[css.index("@media (prefers-reduced-motion: reduce)"):]
    assert ".tr-sx-lines .ln" in reduced and ".tr-sx-lines .ln:first-child { opacity:1; }" in reduced
    phone = css[css.index("@media (max-width: 768px)"):]
    assert ".tr-sx-hero-radar, .tr-sx-crafts { display:none; }" in phone                 # a phone stays uncluttered
    assert ".tr-sx-lines { height:54px;" in phone                                        # three wrapped lines, fixed


def test_the_page_draws_its_atmosphere_and_keeps_its_controls(amb_page):
    app = ok(run())
    html = main_markup(app)
    assert html.count('class="tr-sx-crafts band"') == 1 and html.count('class="tr-sx-crafts foot"') == 1
    assert html.count('class="tr-craft"') == 24
    assert 'class="tr-sx-lines"' in html and "Thaggede le!" in html and "Pushpa: The Rise · Telugu" in html
    assert "<h1>Showtime Explorer</h1>" in html and "tr-hero-mark" in html
    # nothing of the interface moved
    for key in ("sxmode_movie", "sxmode_theatre"):
        assert app.button(key=key) is not None
    assert app.selectbox(key="sx_movie") is not None
    assert {b.key for b in app.button if b.key and b.key.startswith("sxpre_")} == {
        "sxpre_morning", "sxpre_afternoon", "sxpre_evening", "sxpre_night", "sxpre_all_day"}


def test_every_line_is_escaped(monkeypatch):
    monkeypatch.setattr(ux, "LINES", (('<b>"x"</b>', "<i>f</i>", "Telugu"),))
    markup = ux.lines_markup()
    assert "<b>" not in markup and "&lt;b&gt;" in markup and "&lt;i&gt;" in markup
