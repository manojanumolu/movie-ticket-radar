"""The audited dialogue collection, and the Login page's key to the symbols.

The collection (``ui.dialogues``) is the one source of every film line the
app shows. Each entry is short, attributed — speaker, film, year, language,
and where it was checked — and says what it is: the line as spoken, its
published English subtitle, or our own paraphrase. The Login page gains a
small key to the app's symbols under the sign-in panel; the form is still
the first thing in the panel and nothing of it changed.
"""

from __future__ import annotations

import re

from tests.test_auth import run, visitor  # noqa: F401 - fixture
from ui import dialogues, login


# ── the collection ─────────────────────────────────────────────────────────
def test_every_entry_is_short_attributed_and_says_what_it_is():
    assert len(dialogues.DIALOGUES) >= 30
    for d in dialogues.DIALOGUES:
        assert dialogues.words(d.line) <= dialogues.MAX_WORDS, d.key
        assert d.film and 1900 < d.year <= 2026 and d.language and d.speaker and d.source, d.key
        assert d.kind in dialogues.KINDS and d.region in {"India", "World"}, d.key
        if d.language not in {"English", "Kannada · said in English"} and d.kind == "quote":
            assert d.english, f"{d.key}: a line not in English carries its translation"
        if d.kind == "subtitle":
            assert not d.english and d.note == "English subtitle", d.key
        if d.kind == "paraphrase":
            assert d.note.startswith("Paraphrase"), d.key


def test_india_and_the_world_are_both_covered():
    langs = {d.language for d in dialogues.DIALOGUES}
    assert {"Telugu", "Hindi", "Tamil", "Malayalam", "Bengali"} <= langs
    assert any(lang.startswith("Kannada") for lang in langs)
    assert {"English", "Japanese", "Korean", "French", "Spanish", "Italian", "German", "Mandarin", "Cantonese"} <= langs


def test_the_audit_corrections_hold():
    by_line = {d.line: d for d in dialogues.DIALOGUES}
    assert by_line["I love you 3000."].film == "Avengers: Endgame"                      # not Iron Man
    assert "Dread it. Run from it. Destiny arrives all the same." in by_line            # whole, not cut
    lines = " ".join(by_line)
    for gone in ("Hulk smash", "Thokkukuntu", "Ramannu", "I kindly request", "Powerful people come"):
        assert gone not in lines, gone


def test_romanised_lines_stay_in_latin_script():
    for d in dialogues.DIALOGUES:
        assert all(ord(ch) < 0x0370 or ch in "…“”«»’ōū" or "À" <= ch <= "ɏ" for ch in d.line), d.key


def test_the_markup_escapes_every_field():
    d = dialogues.Dialogue("x", "<a>", "<b>", "<c>", 2000, "<d>", "<e>", "India", kind="paraphrase")
    html = dialogues.line_markup(d)
    assert re.search(r"<[a-e]>", html) is None and "&lt;a&gt;" in html and "&lt;b&gt;" in html
    assert "Paraphrase, inspired by the film" in html


def test_both_rotations_name_known_entries_only():
    for keys in (dialogues.HOME_ROTATION, dialogues.EXPLORER_ROTATION):
        assert set(keys) <= set(dialogues.BY_KEY) and len(keys) == len(set(keys))


# ── the Login page's key ───────────────────────────────────────────────────
def test_the_key_explains_the_five_symbols():
    html = login._legend()
    names = re.findall(r"<b>([^<]+)</b><span>([^<]+)</span>", html)
    assert names == [("Film reel", "Cinema and movie discovery"),
                     ("Projector", "Watching for showtime releases"),
                     ("Radar", "Monitoring ticket availability"),
                     ("Premium formats", "Discovering special cinema experiences"),
                     ("Film dialogue", "Celebrating world cinema")]
    assert html.count('aria-hidden="true"') == 5                                        # icons are decoration
    assert "<script" not in html and "onclick" not in html and "<button" not in html


def test_the_key_sits_under_the_form_and_stays_quiet():
    import inspect

    src = inspect.getsource(login.render)
    panel = src.index('st.container(key="trauth_panel")')
    assert panel < src.index("_signin()") < src.index("C.html(_legend())") < src.index("C.html(_bottom())")
    css = login.CSS
    legend = re.search(r"\.tr-auth-legend \{([^}]*)\}", css).group(1)
    assert "max-width:560px" in legend and "position" not in legend                     # in flow, never over the form
    phone = css[css.index("@media (max-width: 768px)"):]
    assert ".tr-auth-legend ul { grid-template-columns:repeat(2, minmax(0, 1fr));" in phone


def test_the_rendered_login_page_carries_the_key_and_its_form(visitor):
    app = run()
    assert not app.exception, [str(e) for e in app.exception]
    text = " ".join(m.value for m in app.markdown)
    assert text.count('class="tr-auth-legend"') == 1 and "What our symbols mean" in text
    assert len(app.text_input) >= 2 and len(app.button) >= 1                     # the sign-in form is drawn
    assert text.index("tr-auth-brand") < text.index("tr-auth-legend")              # the panel first, then the key
