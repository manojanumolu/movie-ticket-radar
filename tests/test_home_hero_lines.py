"""Home's hero: one rotating line of world cinema, under the copy.

The lines come from ``content.home_dialogues`` — famous cinema from around
the world, the original words in Roman letters, with film, year, speaker and
language, and no translations. They are separate from Home's 24 dialogue
marks, which stay exactly as approved.
"""

from __future__ import annotations

import re
from pathlib import Path

from content import home_dialogues
from ui import home


def test_the_rotation_is_the_home_collection_with_credits_only():
    assert home.HERO_LINES is home_dialogues.DIALOGUES
    html = home.hero_lines()
    assert html.startswith('<div class="tr-hero-lines" aria-hidden="true">')
    assert html.count('class="ln"') == len(home.HERO_LINES)
    assert "Indra · 2002 · Indrasena Reddy (Chiranjeevi) · Telugu" in html
    assert 'class="en"' not in html and "English</b>" not in html                       # no translation row
    delays = [int(d) for d in re.findall(r"animation-delay:(\d+)s", html)]
    assert delays == [i * home.HERO_LINE_SECONDS for i in range(len(home.HERO_LINES))]


def test_the_markup_escapes_everything(monkeypatch):
    from content import Dialogue

    monkeypatch.setattr(home, "HERO_LINES", (Dialogue("<b>x</b>", "<s>f</s>", 2000, "<i>s</i>", "Telugu", "src"),))
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
    src = Path("ui/home.py").read_text(encoding="utf-8")
    for name in ("LINES", "TELUGU_SIX", "HOME_LAYOUT"):
        start = old.index(f"\n{name}")
        block = old[start:old.index("\n}\n", start) + 3] if name != "TELUGU_SIX" else old[start:old.index(")\n", start) + 2]
        assert block in src, name                                           # byte for byte
    for fn in ("def _mark(", "def _side(", "def crafts_zone(", "def crafts_strip("):
        start = old.index(fn)
        end = old.index("\ndef ", start + 1)
        assert old[start:end] in src, fn
