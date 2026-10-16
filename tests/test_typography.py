"""One type system: Manrope for everything read, DM Mono for a short list of roles.

The theme sets every element in ``.stApp`` to Manrope with ``!important``
and then lists the classes that are DM Mono (eyebrows, field labels,
badges, timers, step numbers and pips, technical metadata). A rule that
declares ``--tr-mono`` but is missing from that list renders in Manrope —
the cause of the same role appearing in two faces ("Coming soon" in two
fonts, two avatar initials, two ✓ pips). These tests keep the declaration
and the rendering in agreement; the browser audit that found the cases is
described in docs/design-system.md §3.
"""

from __future__ import annotations

import re

from ui import avatar, detail, home, login, restoring, theme


def _declarations(css: str) -> list[tuple[str, str]]:
    """(selector, body) for every plain rule, comments removed."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [(m.group(1).strip(), m.group(2)) for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)]


def _allow_list() -> set[str]:
    css = re.sub(r"/\*.*?\*/", "", theme.CSS, flags=re.S)
    block = css.split(".stApp :is(.tr-eyebrow")[1].split(") {")[0]
    return {s.strip() for s in (".tr-eyebrow" + block).split(",")}


def _bare(selector: str) -> str:
    return re.sub(r":not\([^)]*\)", "", selector).strip()


def test_the_two_faces_are_the_ones_loaded():
    assert "family=Manrope:wght@500;600;700;800" in theme.FONTS and "DM+Mono:wght@400;500" in theme.FONTS
    assert "--tr-sans:'Manrope'" in theme.CSS and "--tr-mono:'DM Mono'" in theme.CSS


def test_every_mono_declaration_in_the_theme_actually_renders_mono():
    """Inside .stApp a --tr-mono rule only wins if its selector is listed."""
    allowed = {_bare(s) for s in _allow_list()}
    missing = []
    for selector, body in _declarations(theme.CSS):
        if "var(--tr-mono)" not in body or "!important" in body.split("var(--tr-mono)")[1][:14]:
            continue
        for sel in (s.strip() for s in selector.split(",")):
            if not sel or sel.startswith("@") or "::" in sel:      # pseudo-elements are never reached by the Manrope rule
                continue
            if _bare(sel) in allowed or sel.split()[-1] in allowed:  # e.g. "… label code" — code is listed
                continue
            missing.append(sel)
    assert not missing, f"declared DM Mono but rendered Manrope: {missing}"


def test_the_roles_the_audit_found_in_two_faces_now_have_one():
    allowed = _allow_list()
    for role in (".tr-loc.disabled .s",          # "Coming soon", as on the platform cards
                 ".tr-summary-strip .n",         # the summary chip's ✓, as the step rail's
                 ".tr-craft .tr-sub .by",        # the crafts' attribution eyebrow
                 ".tr-admin-tag"):               # ADMIN, in the chip and in the account menu
        assert role in allowed, role
    # the choice descriptions ("Every date on sale") are read text, declared Manrope
    assert ".tr-interval:not(.choice) .unit" in allowed and ".tr-interval .unit" not in allowed
    assert ".tr-interval.choice .unit { font-family:var(--tr-sans);" in theme.CSS


def test_avatar_initials_are_one_face_in_the_chip_and_the_menu():
    """The menu is a pop-over outside .stApp, so only its own rule decides."""
    for selector in (".tr-acct-chip .av {", ".tr-acct-menu .av {"):
        body = theme.CSS.split(selector)[1].split("}")[0]
        assert "font-family:var(--tr-sans)" in body and "font-weight:500" in body, selector


def test_no_dm_mono_text_asks_for_a_weight_the_face_does_not_have():
    """DM Mono ships 400 and 500; anything heavier is a faux bold."""
    for name, css in (("theme", theme.CSS), ("home", home.CSS), ("detail", detail.CSS),
                      ("avatar", avatar.CSS), ("restoring", restoring.CSS)):
        for selector, body in _declarations(css):
            if "var(--tr-mono" in body:
                weight = re.search(r"font-weight:\s*(\d+)", body)
                assert not weight or int(weight.group(1)) <= 500, f"{name}: {selector}"
    # the rail's count sits in a 700 title; it must not inherit that weight
    assert "font-weight:500" in theme.CSS.split(".tr-count {")[1].split("}")[0]


def test_the_detail_dialog_eyebrow_is_mono_outside_stapp():
    """st.dialog renders outside .stApp, where the theme's bare `span` rule
    sets Manrope; the eyebrow's own rule must claim its label."""
    assert ".tr-det-eyebrow > span { font-family:var(--tr-mono); }" in detail.CSS
    assert ".tr-det-eyebrow { min-height:17px; }" in detail.CSS      # the row keeps its height


def test_the_login_page_typography_is_left_as_approved():
    """The restored login page renders its labels in Manrope (the theme's
    rule outranks login.py's mono declarations); that look is the approved
    one, so no login class joins the mono list."""
    assert {s for s in _allow_list() if s.startswith(".tr-auth")} == {".tr-auth-count", ".tr-auth-diag"}
    assert "font-family:var(--tr-mono)" in login.CSS            # login.py itself is untouched
