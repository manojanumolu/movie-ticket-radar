"""The stylesheets go to the browser without their comments, and nothing else.

Streamlit 1.67 sends an element of 10 KB or more in full once per browser
session; on every later run the server sends only a reference to the copy the
browser holds (``streamlit.runtime.forward_msg_cache``, measured in Phase 6:
the 134 KB theme crosses the websocket once per session, as 74 bytes after
that). The websocket is not compressed by default, so what a first load and a
reload carry is the stylesheets as written — comments included, about a tenth
of them. ``ui.theme.sheet`` strips the comments where a sheet is rendered; the
module constants keep them (they are the documentation, and tests read them).

Checked against tinycss2 while this was written (every page sheet tokenises
to exactly the original); here, without adding a dependency, by an independent
regular-expression implementation and by the cases that would break a naive
one.
"""

from __future__ import annotations

import re

import pytest

from ui import avatar, detail, explorer, home, login, restoring, theme

SHEETS = {
    "theme": theme.CSS, "login": login.CSS, "home": home.CSS, "explorer": explorer.AMBIENCE_CSS,
    "restoring": restoring.CSS, "detail": detail.CSS, "avatar": avatar.CSS,
}

#: Strings are kept whole; a comment is anything else between /* and */.
_TOKENS = re.compile(r"""("(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')|/\*.*?(?:\*/|\Z)""", re.S)


def reference(css: str) -> str:
    return _TOKENS.sub(lambda m: m.group(1) or "", css)


@pytest.mark.parametrize("name", list(SHEETS))
def test_every_page_sheet_loses_its_comments_and_nothing_else(name):
    css = SHEETS[name]
    out = theme.sheet(css)
    assert "/*" in css                                  # the source keeps its documentation
    assert out == reference(css)
    assert "/*" not in reference(out) and reference(out) == out
    assert out.count("{") == css.count("{") - sum(c.count("{") for c in re.findall(r"/\*.*?\*/", css, re.S))
    assert len(out) < len(css)


@pytest.mark.parametrize("css, expected", [
    ('a{content:"/* kept */"}', 'a{content:"/* kept */"}'),
    ("a{background:url('x/*y*/z.svg')}", "a{background:url('x/*y*/z.svg')}"),
    ('a{content:"say \\"/*\\""} /* gone */b{}', 'a{content:"say \\"/*\\""} b{}'),
    ("a{}/* a { comment } */b{}", "a{}b{}"),
    ("a{color:red}/* never closed", "a{color:red}"),
    ("a{x:1}/**/b{y:2}", "a{x:1}b{y:2}"),
])
def test_strings_are_copied_whole_and_only_comments_go(css, expected):
    assert theme.strip_css_comments(css) == expected == reference(css)


def test_each_sheet_is_stripped_once_per_process():
    theme.sheet.cache_clear()
    first = theme.sheet(theme.CSS)
    assert theme.sheet(theme.CSS) is first
    assert theme.sheet.cache_info().hits >= 1


def test_what_is_rendered_is_the_stripped_sheet(monkeypatch):
    drawn = []
    monkeypatch.setattr(theme.st, "markdown", lambda body, **k: drawn.append(body))
    theme.inject()
    assert theme.sheet(theme.CSS) in drawn and theme.CSS not in drawn
    assert restoring.markup().startswith(theme.sheet(restoring.CSS))


def test_the_rules_the_suite_relies_on_survive():
    out = theme.sheet(theme.CSS)
    for rule in ('[class*="st-key-trauth_shell"] { display:none !important; }',
                 '.stApp:has([class*="st-key-tr_page"] [data-stale="true"]) [class*="st-key-tr_app"] { display:none !important; }',
                 theme.APP_ENTRANCE.strip()):
        assert rule in out
    assert "tr-home-ambience-in" in theme.sheet(home.CSS)
    assert 'display: flex !important' in theme.sheet(login.CSS)


@pytest.mark.parametrize("name", list(SHEETS))
def test_no_unquoted_url_carries_a_comment_opener(name):
    """The one CSS construct where ``/*`` is not a comment and is not in quotes:
    an unquoted ``url(…)``. None exists; this keeps it that way, since both the
    stripper and the reference above would misread one."""
    unquoted = re.findall(r"url\(\s*([^'\"\s][^)]*)\)", SHEETS[name])
    assert not [u for u in unquoted if "/*" in u]
