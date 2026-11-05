"""Home's hero: one rotating line of cinema, under the copy.

The only addition to Home. One dialogue at a time, in its original language
(romanised, in the spelling the marks and the Explorer already use), with its
English translation underneath, labelled, and the film, year and language.
It reuses approved lines only and changes none of the marks' own.
"""

from __future__ import annotations

import re

from ui import explorer, home


def test_each_line_is_an_approved_one_with_its_translation_film_year_and_language():
    approved = {q for q, _by, _h in home.LINES.values()} | {q for q, _f, _l in explorer.LINES}
    for quote, english, film, year, lang in home.HERO_LINES:
        assert quote in approved, quote                                     # reused, not rewritten
        assert english and english != quote and film and 1900 < year <= 2026
        assert lang in {"Telugu", "Hindi", "Tamil", "Malayalam"}           # never English shown as the original
    assert home.HERO_LINES[0][4] == "Telugu"


def test_the_markup_labels_the_translation_and_escapes_everything(monkeypatch):
    html = home.hero_lines()
    assert html.startswith('<div class="tr-hero-lines" aria-hidden="true">')
    assert html.count('class="ln"') == len(home.HERO_LINES)
    assert html.count("<b>English</b>") == len(home.HERO_LINES)
    assert "Sholay (1975) · Hindi" in html and "How many men were there?" in html
    delays = [int(d) for d in re.findall(r"animation-delay:(\d+)s", html)]
    assert delays == [i * home.HERO_LINE_SECONDS for i in range(len(home.HERO_LINES))]
    monkeypatch.setattr(home, "HERO_LINES", (("<b>x</b>", "<i>y</i>", "<s>f</s>", 2000, "Telugu"),))
    evil = home.hero_lines()
    assert "<i>" not in evil and "<s>" not in evil and "&lt;b&gt;x" in evil


def test_css_only_a_fixed_slot_and_still_under_reduced_motion():
    css = home.CSS
    slot = re.search(r"\.tr-hero-lines \{([^}]*)\}", css).group(1)
    assert "height:54px" in slot and "overflow:hidden" in slot                  # no layout shift
    keyframes = re.search(r"@keyframes tr-hero-line \{(.*)\}", css).group(1)
    props = {d.split(":")[0].strip() for d in re.findall(r"\{([^}]*)\}", keyframes) for d in d.split(";") if ":" in d}
    assert props <= {"opacity", "transform"}
    assert (".tr-hero-lines .ln { animation:none; }\n  .tr-hero-lines .ln:first-child { opacity:1; }") in css
    assert "<script" not in home.hero_lines()


def test_the_marks_and_their_lines_are_the_approved_ones():
    import subprocess

    old = subprocess.run(["git", "show", "080ce6e:ui/home.py"], capture_output=True, text=True, check=True).stdout
    for name in ("LINES", "TELUGU_SIX", "HOME_LAYOUT"):
        start = old.index(f"\n{name}")
        block = old[start:old.index("\n}\n", start) + 3] if name != "TELUGU_SIX" else old[start:old.index(")\n", start) + 2]
        src = open("ui/home.py", encoding="utf-8").read()
        assert block in src, name                                           # byte for byte
