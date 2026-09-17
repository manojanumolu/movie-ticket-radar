"""The TicketRadar look, as one CSS injection.

Every value here is lifted from ``ticketradar-ui-design-system-2`` —
``DESIGN-SPEC.md`` for the tokens and rules, ``TicketRadar.dc.html`` for the
literal sizes. Streamlit's own chrome is overridden rather than themed around,
because the brief is that this must not look like a Streamlit app.

Two fonts do all the work: Manrope for everything the user reads, DM Mono —
sparingly — for the tiny technical labels (eyebrows, badges, timers, step
numbers). Both come from Google Fonts with ``display=swap`` and a system
fallback stack, so a slow font never delays the first paint.

Selectable things (posters, theatre rows, interval tiles, city tiles) are
rendered as design-exact HTML inside a keyed ``st.container`` whose only other
child is an ``st.button``. The CSS stretches that button over the tile and
makes it transparent, so the *whole tile* is the hit target — exactly as in
the design — while Streamlit still owns the click, the rerun and the tests.
"""

from __future__ import annotations

from urllib.parse import quote

import streamlit as st

TOKENS = {
    "bg": "#08080A",
    "surface": "#101014",
    "sunken": "#0A0A0D",
    "shell": "#0B0B0E",
    "border": "rgba(255,255,255,.07)",
    "border_strong": "rgba(255,255,255,.14)",
    "text": "#F2F2F4",
    "text_2": "#B0B0BA",
    "text_3": "#8E8E98",
    "text_4": "#6E6E7A",
    "accent": "#FF3355",
    "accent_soft": "#FF6B85",
    "accent_deep": "#D4123F",
    "success": "#3ED598",
    "warning": "#E8B25C",
}


# ── nav icons (DESIGN-SPEC §5 "Nav item"): 18px line icons as data URIs ────
def _svg_uri(paths: str, stroke: str, width: str = "1.7") -> str:
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' "
        f"stroke='{stroke}' stroke-width='{width}' stroke-linecap='round' "
        f"stroke-linejoin='round'>{paths}</svg>"
    )
    return f"url(\"data:image/svg+xml;utf8,{quote(svg, safe='')}\")"


NAV_ICONS = {
    "home": ("<path d='M4 10.5 12 4l8 6.5V19a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 19z'/>"
             "<path d='M9.5 20.5v-6h5v6'/>", "1.8"),
    "monitors": ("<rect x='3' y='4.5' width='18' height='13' rx='2'/><circle cx='12' cy='11' r='2.6'/>"
                 "<path d='M8 21h8'/>", "1.7"),
    "history": ("<path d='M3.5 12a8.5 8.5 0 1 0 2.6-6.1'/><path d='M3.2 4.6v3.9h3.9'/>"
                "<path d='M12 8v4.4l3.2 1.9'/>", "1.7"),
    "settings": ("<circle cx='12' cy='12' r='2.9'/>"
                 "<path d='M19.2 14.6a1.5 1.5 0 0 0 .3 1.65l.06.06a1.6 1.6 0 1 1-2.26 2.26l-.06-.06a1.5 1.5 0 0 0-1.65-.3 1.5 1.5 0 0 0-.91 1.37v.17a1.6 1.6 0 1 1-3.2 0v-.09a1.5 1.5 0 0 0-.98-1.37 1.5 1.5 0 0 0-1.65.3l-.06.06A1.6 1.6 0 1 1 4.5 16.4l.06-.06a1.5 1.5 0 0 0 .3-1.65 1.5 1.5 0 0 0-1.37-.91H3.3a1.6 1.6 0 1 1 0-3.2h.09a1.5 1.5 0 0 0 1.37-.98 1.5 1.5 0 0 0-.3-1.65L4.4 7.9A1.6 1.6 0 1 1 6.66 5.64l.06.06a1.5 1.5 0 0 0 1.65.3h.07a1.5 1.5 0 0 0 .91-1.37V4.5a1.6 1.6 0 1 1 3.2 0v.09a1.5 1.5 0 0 0 .91 1.37 1.5 1.5 0 0 0 1.65-.3l.06-.06a1.6 1.6 0 1 1 2.26 2.26l-.06.06a1.5 1.5 0 0 0-.3 1.65v.07a1.5 1.5 0 0 0 1.37.91h.17a1.6 1.6 0 1 1 0 3.2h-.09a1.5 1.5 0 0 0-1.37.91z'/>",
                 "1.5"),
}


def _nav_css() -> str:
    """One icon per navigation item, by position.

    Streamlit wraps each radio option in its own ``<div>``, so the labels are
    not siblings: ``label:nth-of-type(N)`` is 1 for every one of them, and
    all four items drew the home icon. The position that tells them apart
    is the wrapper's — ``:nth-child(N)`` of the radiogroup. The bare-label
    form is kept for a DOM that renders the labels as siblings.
    """
    side = '[data-testid="stSidebar"] [role="radiogroup"]'
    rules = []
    for index, (name, (paths, width)) in enumerate(NAV_ICONS.items(), start=1):
        idle = _svg_uri(paths, "#8E8E98", width)
        active = _svg_uri(paths, "#FF5573", width)
        labels = (f"{side} > :nth-child({index}) label", f"{side} > label:nth-of-type({index})")
        rules.append(
            ", ".join(f"{lb}::before" for lb in labels) + f" {{ background-image:{idle}; }}\n"
            + ", ".join(f"{lb}:has(input:checked)::before, {lb}[data-selected=\"true\"]::before" for lb in labels)
            + f" {{ background-image:{active}; }}"
        )
    return "\n".join(rules)


#: Web fonts, injected as their own block. A ``<link>`` starts a Markdown
#: HTML block that ends at the first blank line — put in front of the
#: stylesheet it would cut the ``<style>`` element in half.
FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    'family=Manrope:wght@500;600;700;800&family=DM+Mono:wght@400;500&display=swap">'
)

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@500;600;700;800&family=DM+Mono:wght@400;500&display=swap');

/* ── tokens ─────────────────────────────────────────────────────────
   Three surfaces (page · card · interactive), one accent for intent,
   and four semantic colours that only ever mean one thing each:
   green = live/success, amber = waiting/monitoring, cool grey = neutral,
   coral = a problem (distinct from the accent, which means "selected").  */
:root {
  --tr-bg:#0B0B10; --tr-surface:#14141B; --tr-raised:#1B1B24; --tr-sunken:#0F0F15; --tr-shell:#0E0E14;
  --tr-glass:rgba(255,255,255,.055); --tr-glass-2:rgba(255,255,255,.09);
  --tr-border:rgba(255,255,255,.09); --tr-border-strong:rgba(255,255,255,.17); --tr-border-hover:rgba(255,255,255,.26);
  --tr-hi:inset 0 1px 0 rgba(255,255,255,.07);
  --tr-text:#F6F6F8; --tr-text-2:#BEBFC8; --tr-text-3:#969BA8; --tr-text-4:#727786;
  --tr-accent:#FF3355; --tr-accent-soft:#FF6B85; --tr-accent-deep:#D4123F;
  --tr-success:#3ED598; --tr-warning:#E8B25C; --tr-neutral:#9AA3B5; --tr-danger:#FF5C5C;
  --tr-shadow:0 22px 48px -26px rgba(0,0,0,.95), 0 2px 6px -2px rgba(0,0,0,.5);
  --tr-sans:'Manrope','Segoe UI',system-ui,-apple-system,Helvetica,Arial,sans-serif;
  --tr-mono:'DM Mono',ui-monospace,'Cascadia Mono',Consolas,monospace;
  --tr-ease:cubic-bezier(.2,.7,.2,1); --tr-fast:.16s;
}

/* ── shell ─────────────────────────────────────────────────────────── */
html, body, [data-testid="stAppViewContainer"], .stApp {
  background:
    radial-gradient(1200px 600px at 68% -14%, rgba(255,51,85,.17), transparent 65%),
    radial-gradient(900px 520px at -8% 108%, rgba(138,70,210,.13), transparent 68%),
    radial-gradient(700px 400px at 108% 78%, rgba(255,51,85,.06), transparent 70%),
    linear-gradient(180deg,#0E0E15 0%,var(--tr-bg) 40%,#0A0A0F 100%) !important;
  background-attachment: fixed !important;
  color: var(--tr-text);
  font-family: var(--tr-sans);
  font-weight: 500;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}
[data-testid="stCaptionContainer"] p, [data-testid="stWidgetLabel"] p { font-weight:500; }
.stButton > button p { font-weight:700; }
[data-testid="stHeader"] { background: transparent !important; }
/* The toolbar itself stays: in Streamlit 1.59 it holds the "open sidebar"
   control, which is the only navigation a phone has (and the only way back
   after collapsing the sidebar on a desktop). Its actions and menu go. */
[data-testid="stDecoration"], [data-testid="stToolbarActions"], [data-testid="stAppDeployButton"],
[data-testid="stMainMenu"], #MainMenu, footer { display:none !important; }
[data-testid="stToolbar"] { background: transparent !important; }
/* The toolbar spans the top width but, with its actions hidden, only the
   sidebar-expand control (top-left) is real. Let the rest of it pass clicks
   through, so the account chip at the top-right is never swallowed. */
[data-testid="stHeader"], [data-testid="stToolbar"] { pointer-events: none !important; }
[data-testid="stHeader"] button, [data-testid="stToolbar"] button,
button[data-testid="stExpandSidebarButton"] { pointer-events: auto !important; }
button[data-testid="stExpandSidebarButton"] {
  width: 40px; height: 40px; min-height: 0; padding: 0; border-radius: 11px; display:flex; align-items:center; justify-content:center;
  background: linear-gradient(180deg,rgba(255,255,255,.12),rgba(255,255,255,.05));
  border: 1px solid rgba(255,255,255,.18); box-shadow: var(--tr-hi), 0 10px 24px -14px rgba(0,0,0,.9);
}
button[data-testid="stExpandSidebarButton"]:hover { border-color: rgba(255,51,85,.5); background: rgba(255,51,85,.10); }
button[data-testid="stExpandSidebarButton"] span, button[data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"] { color: var(--tr-text) !important; font-size: 24px; }
[data-testid="stSidebarHeader"] { padding:.55rem .6rem 0 !important; height:auto !important; min-height:0 !important; }
.block-container { padding: 1.3rem 2.2rem 3.5rem !important; max-width: 1600px !important; }
[data-testid="stMainBlockContainer"] { padding-top: 1rem !important; }
@media (max-width: 1400px) { .block-container { padding: 1.2rem 1.6rem 3rem !important; } }
@media (max-width: 1100px) { .block-container { padding: 1.1rem 1.1rem 3rem !important; } }
[data-testid="stAppViewContainer"] { overflow-x: hidden; }
[data-testid="stMainBlockContainer"], [data-testid="stVerticalBlock"] { min-width: 0; }
/* Desktop only. Streamlit stacks columns on phones with a per-column
   `min-width: calc(100% - …)` media rule; a global `min-width: 0` here sat
   later in the document and cancelled it, which is how the right rail ended
   up a 60px column with "No active monitors" wrapping letter by letter. */
@media (min-width: 769px) { [data-testid="stColumn"] { min-width: 0; } }

h1, h2, h3, h4 { font-family: var(--tr-sans); letter-spacing:-.03em; color: var(--tr-text); }
p, span, div, label, li, input, button { font-family: var(--tr-sans); }
/* Streamlit offsets a trailing <p>'s 1rem margin with margin-bottom:-16px
   on every markdown container. Our cards are raw HTML with no <p>, so that
   offset was eating the last 16px of every card — the clipped help lines and
   badges. Zero both sides of the trick instead. */
[data-testid="stMarkdownContainer"] { margin-bottom:0 !important; }
[data-testid="stMarkdownContainer"] p { margin-bottom:0; }
[data-testid="stCaptionContainer"], .stCaption, [data-testid="stCaptionContainer"] p {
  color: var(--tr-text-3) !important; font-size:12.5px !important; line-height:1.5;
}
[data-testid="stVerticalBlock"] { gap: .7rem; }
[data-testid="stColumn"] > div > [data-testid="stVerticalBlock"] { gap: .6rem; }
.tr-ic { display:inline-block; vertical-align:middle; flex:none; }

/* ── sidebar: an application rail ─────────────────────────────────── */
[data-testid="stSidebar"] {
  background: linear-gradient(180deg,rgba(24,24,32,.92) 0%,rgba(14,14,20,.96) 100%) !important;
  backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px);
  border-right: 1px solid rgba(255,255,255,.08); box-shadow: 12px 0 40px -30px rgba(0,0,0,.9);
  min-width: 244px !important; width: 244px !important;
}
[data-testid="stSidebar"] .block-container,
[data-testid="stSidebarUserContent"] { padding: 1.35rem 1rem 1.1rem !important; }
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: 1rem; }
[data-testid="stSidebarCollapseButton"] button, [data-testid="stSidebarCollapseButton"] svg { color:var(--tr-text-3); }

.tr-logo { display:flex; align-items:center; gap:12px; padding:2px 6px 12px; border-bottom:1px solid var(--tr-border); margin-bottom:4px; }
.tr-logo-mark {
  width:38px; height:38px; border-radius:11px; flex:none;
  background: linear-gradient(140deg,#FF3355,#B3123A);
  display:flex; align-items:center; justify-content:center;
  box-shadow:0 8px 22px -8px rgba(255,51,85,.85), inset 0 1px 0 rgba(255,255,255,.18);
}
.tr-logo-mark svg { display:block; }
.tr-logo-name { font-size:20px; font-weight:800; letter-spacing:-.035em; line-height:1.1; }
.tr-logo-name em { font-style:normal; color:var(--tr-accent); }
.tr-logo-sub { font-size:11px; color:var(--tr-text-4); margin-top:3px; font-weight:600; letter-spacing:.02em; }

.tr-side-foot { margin-top:18px; }
.tr-quote {
  padding:14px 16px; border-radius:14px; background:rgba(255,255,255,.025);
  border:1px solid rgba(255,255,255,.06); font-size:12.5px; line-height:1.55;
  color:#A9A9B5; font-style:italic;
}
.tr-version {
  display:flex; justify-content:space-between; gap:12px; font-family:var(--tr-mono);
  font-size:10px; color:#5A5A64; letter-spacing:.08em; margin-top:12px;
}
.tr-page-foot { max-width:1600px; margin-top:28px; }

/* ── the account control, top-right of the main content ─────────────
   Who Firebase says you are, as one compact chip on the right edge of the
   page; the chip *is* the menu's trigger (the popover button is stretched
   over it, transparent — the same "pick" pattern as the tiles), and the
   menu holds Account settings and Sign out. The sidebar carries nav only. */
[class*="st-key-tracct_top"] { position:relative; z-index:200; width:max-content !important; max-width:100%; margin:0 0 6px auto; gap:0 !important; }
.tr-acct-chip { display:flex; align-items:center; gap:9px; padding:5px 10px 5px 5px; border-radius:999px; min-width:0;
  background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.035)); border:1px solid rgba(255,255,255,.12);
  box-shadow: var(--tr-hi), 0 10px 24px -16px rgba(0,0,0,.9);
  transition:border-color var(--tr-fast) var(--tr-ease), background var(--tr-fast) var(--tr-ease), transform var(--tr-fast) var(--tr-ease); }
[class*="st-key-tracct_top"]:hover .tr-acct-chip { border-color:rgba(255,51,85,.5); background:rgba(255,51,85,.08); transform:translateY(-1px); }
.tr-acct-chip .av { width:30px; height:30px; border-radius:50%; flex:none; display:flex; align-items:center; justify-content:center;
  font-family:var(--tr-mono); font-size:12.5px; font-weight:500; color:#fff;
  background:linear-gradient(140deg,#FF3355,#B3123A); box-shadow:0 8px 20px -10px rgba(255,51,85,.9), inset 0 1px 0 rgba(255,255,255,.18); }
.tr-acct-chip .n { font-size:13px; font-weight:700; color:var(--tr-text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:160px; }
.tr-acct-chip .chev { color:var(--tr-text-4); display:flex; flex:none; }
[class*="st-key-tracct_top"] [data-testid="stPopover"] { position:absolute; inset:0; margin:0; }
[class*="st-key-tracct_top"] [data-testid="stPopover"] > button, [class*="st-key-tracct_top"] [data-testid="stPopoverButton"] {
  position:absolute; inset:0; width:100%; height:100%; min-height:0; margin:0; padding:0; opacity:0; cursor:pointer; }
/* the menu itself (portaled to <body>) */
[data-testid="stPopoverBody"] { background:var(--tr-surface) !important; border:1px solid var(--tr-border-strong) !important;
  border-radius:14px !important; padding:6px !important; min-width:248px; max-width:min(320px, calc(100vw - 24px));
  box-shadow:0 24px 60px -20px rgba(0,0,0,.9), var(--tr-hi); }
[data-testid="stPopoverBody"] [data-testid="stVerticalBlock"] { gap:.15rem; }
.tr-acct-menu { padding:8px 12px 10px; border-bottom:1px solid var(--tr-border); margin-bottom:4px; min-width:0; }
.tr-acct-menu .n { font-size:13.5px; font-weight:700; color:var(--tr-text); overflow-wrap:anywhere; }
.tr-acct-menu .m { font-size:11.5px; color:var(--tr-text-3); overflow-wrap:anywhere; margin-top:2px; }
.tr-acct-menu .k { font-family:var(--tr-mono); font-size:9px; letter-spacing:.18em; color:var(--tr-text-4); margin-top:8px; }
/* the destructive action is set apart from the ordinary ones: a quiet row in
   the same shape as the others, red only in its text, louder only on hover */
.tr-acct-sep { height:1px; background:var(--tr-border); margin:6px 4px 4px; }
[class*="st-key-acct_delete"] button { color:#FF8A8A !important; border-color:transparent !important; }
[class*="st-key-acct_delete"] button:hover { color:#FFB0B0 !important; background:rgba(255,51,85,.10) !important; border-color:rgba(255,80,80,.45) !important; }
/* the confirmation panel */
[class*="st-key-trcard_delacct"] { border-color:rgba(255,80,80,.45) !important; background:linear-gradient(180deg,rgba(255,51,85,.07),rgba(255,51,85,.02)) !important; }
.tr-danger .t { font-size:16px; font-weight:800; color:#FF8A8A; letter-spacing:-.01em; }
.tr-danger .s { font-size:13px; color:var(--tr-text-2); margin-top:6px; line-height:1.55; }
[class*="st-key-acct_delete_confirm"] button { background:#E02742 !important; border-color:#E02742 !important; }
[class*="st-key-acct_delete_confirm"] button:disabled { opacity:.45 !important; }
/* ``.stButton button`` at any depth: a button with a ``help`` tooltip (Delete
   account) sits one wrapper deeper than the others and must match too. */
[data-testid="stPopoverBody"] .stButton button { min-height:38px; font-size:13px; font-weight:600; justify-content:flex-start; gap:10px;
  padding:.4rem .75rem; border-radius:10px; box-shadow:none; background:transparent; border-color:transparent; color:var(--tr-text-2); width:100%; }
[data-testid="stPopoverBody"] .stButton [data-testid="stTooltipHoverTarget"] { width:100%; }
/* Streamlit centres a button's content in an inner flex box; the rows of a
   menu line up on the left, icon then label, every one the same. */
[data-testid="stPopoverBody"] .stButton button > div { justify-content:flex-start; width:100%; gap:10px; }
[data-testid="stPopoverBody"] .stButton button [data-testid="stIconMaterial"] { font-size:18px; width:18px; flex:none; }
[data-testid="stPopoverBody"] .stButton button p { font-size:13px; font-weight:600; }
[data-testid="stPopoverBody"] .stButton button:hover { transform:none; color:#fff; background:rgba(255,255,255,.06); border-color:var(--tr-border); }
[data-testid="stPopoverBody"] [class*="st-key-auth_signout"] .stButton button:hover { color:#fff; border-color:rgba(255,51,85,.5); background:rgba(255,51,85,.08); }

[data-testid="stSidebar"] [role="radiogroup"] { gap:4px; margin-top:2px; }
[data-testid="stSidebar"] [role="radiogroup"] label {
  position:relative; display:flex; align-items:center; gap:12px; min-height:44px; padding:0 14px; border-radius:11px;
  font-size:14px; color:#9A9FAD; border:1px solid transparent;
  transition:background var(--tr-fast) var(--tr-ease), color var(--tr-fast) var(--tr-ease), border-color var(--tr-fast) var(--tr-ease);
  cursor:pointer; margin:0; box-sizing:border-box;
}
[data-testid="stSidebar"] [role="radiogroup"] label::before {
  content:''; width:18px; height:18px; flex:none; background-repeat:no-repeat;
  background-position:center; background-size:contain; opacity:.85;
}
[data-testid="stSidebar"] [role="radiogroup"] label:hover { background:var(--tr-glass); border-color:rgba(255,255,255,.08); color:#EDEDF2; }
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked),
[data-testid="stSidebar"] [role="radiogroup"] label[data-selected="true"] {
  background: linear-gradient(180deg,rgba(255,255,255,.06),rgba(255,255,255,0)), linear-gradient(90deg,rgba(255,51,85,.22),rgba(255,51,85,.05));
  border:1px solid rgba(255,51,85,.38); color:#fff; font-weight:700;
  box-shadow: var(--tr-hi), 0 10px 24px -16px rgba(255,51,85,.8);
}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked)::after,
[data-testid="stSidebar"] [role="radiogroup"] label[data-selected="true"]::after {
  content:''; position:absolute; left:-1px; top:11px; bottom:11px; width:3px; border-radius:3px;
  background:var(--tr-accent); box-shadow:0 0 10px rgba(255,51,85,.8);
}
[data-testid="stSidebar"] [role="radiogroup"] label input[type="radio"] {
  /* Streamlit's BaseWeb radio control must remain in the DOM for keyboard
     navigation, but never occupy visual space: our ::before icon is the
     visible control.  Some Streamlit releases add an extra wrapper here;
     styling every child made that wrapper grow into the grey/red ovals. */
  position:absolute !important; width:1px !important; height:1px !important;
  margin:-1px !important; padding:0 !important; opacity:0 !important;
  clip:rect(0,0,0,0) !important; clip-path:inset(50%) !important;
}
[data-testid="stSidebar"] [role="radiogroup"] label > div > div > div:first-child:not([data-testid]) { display:none; }
[data-testid="stSidebar"] [role="radiogroup"] label [data-testid="stMarkdownContainer"] { width:100%; min-width:0; }
[data-testid="stSidebar"] [role="radiogroup"] label [data-testid="stMarkdownContainer"] p {
  font-size:14.5px; font-weight:600; letter-spacing:-.01em; color:inherit; display:flex; align-items:center; gap:10px; width:100%; line-height:1; white-space:nowrap;
}
[data-testid="stSidebar"] [role="radiogroup"] label code {
  margin-left:auto; font-family:var(--tr-mono); font-size:10.5px; padding:3px 7px; border-radius:6px;
  background:rgba(255,51,85,.14); color:#FF6B85; border:none; line-height:1;
}
__NAV_ICONS__

/* The cookie bridge is a zero-height component. Until its iframe has loaded
   Streamlit draws a skeleton bar for it — which, on a fresh load, was the
   only thing on the page. There is nothing to preview; draw nothing. */
[class*="st-key-tr_chrome"] [data-testid="stSkeleton"] { display:none !important; }
[class*="st-key-tr_chrome"] iframe { display:block; height:0 !important; min-height:0 !important; border:0; }

/* ── hero ──────────────────────────────────────────────────────────── */
.tr-hero {
  position:relative; overflow:hidden; padding:38px 40px 34px; border-radius:22px;
  background: linear-gradient(118deg,#15151F 0%,#1A0C16 48%,#2E0A1C 100%);
  border:1px solid rgba(255,255,255,.10); margin-bottom:20px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.10), 0 30px 70px -30px rgba(255,51,85,.45), var(--tr-shadow);
}
.tr-hero::before {
  content:''; position:absolute; inset:0; pointer-events:none;
  background:
    radial-gradient(620px 260px at 86% 22%, rgba(255,51,85,.30), transparent 70%),
    radial-gradient(420px 220px at 30% 120%, rgba(150,70,220,.18), transparent 70%);
}
.tr-hero::after {
  content:''; position:absolute; inset:0; pointer-events:none;
  background:
    linear-gradient(rgba(255,255,255,.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,.035) 1px, transparent 1px);
  background-size: 28px 28px; opacity:.55;
  -webkit-mask-image: radial-gradient(ellipse at 70% 40%, #000 20%, transparent 80%); mask-image: radial-gradient(ellipse at 70% 40%, #000 20%, transparent 80%);
}
.tr-hero-inner { position:relative; display:flex; justify-content:space-between; gap:28px; flex-wrap:nowrap; align-items:flex-end; min-width:0; }
.tr-hero-inner > div:first-child { min-width:0; }
.tr-hero h1 { font-size:50px; font-weight:800; letter-spacing:-.045em; line-height:1.0; margin:0; padding:0;
  background:linear-gradient(180deg,#FFFFFF 0%,#E4E4EC 60%,#B9B9C6 100%); -webkit-background-clip:text; background-clip:text; color:transparent; }
.tr-hero-lede { font-size:19px; color:#F0F0F4; margin-top:12px; font-weight:700; letter-spacing:-.02em; }
.tr-hero-sub { font-size:14px; color:var(--tr-text-3); margin-top:6px; font-weight:500; }
.tr-hero-mark {
  flex:none; display:flex; flex-direction:column; align-items:flex-end; gap:2px; font-family:var(--tr-mono); font-size:10.5px;
  letter-spacing:.24em; color:rgba(255,255,255,.34); text-transform:uppercase; padding-bottom:4px; white-space:nowrap; line-height:1.8;
}
.tr-hero-mark b { color:var(--tr-accent-soft); font-weight:400; }
@media (max-width:1200px) { .tr-hero h1 { font-size:40px; } }
@media (max-width:900px) {
  .tr-hero { padding:24px 20px; }
  .tr-hero h1 { font-size:27px; letter-spacing:-.03em; }
  .tr-hero-lede { font-size:15px; }
  .tr-hero-mark { display:none; }
}

/* ── primitives ────────────────────────────────────────────────────── */
.tr-eyebrow {
  font-family:var(--tr-mono); font-size:10px; letter-spacing:.18em;
  text-transform:uppercase; color:var(--tr-text-4);
}
.tr-rule { display:flex; align-items:center; gap:10px; margin:6px 0 6px; min-width:0; }
.tr-rule .tr-eyebrow { font-size:11px; letter-spacing:.22em; color:var(--tr-text-2); white-space:nowrap; }
.tr-rule .line { height:1px; flex:1; background:var(--tr-border); }
.tr-count { font-family:var(--tr-mono); font-size:10px; padding:2px 7px; border-radius:6px;
  background:rgba(255,255,255,.06); color:var(--tr-text-2); border:1px solid var(--tr-border); }
.tr-rail-title .tr-count { background:rgba(255,51,85,.14); color:var(--tr-accent-soft); border-color:transparent; }

.tr-step-head { display:flex; align-items:flex-start; gap:13px; min-width:0; }
.tr-step-num {
  width:32px; height:32px; flex:none; border-radius:10px; font-weight:500;
  background:rgba(255,51,85,.14); border:1px solid rgba(255,51,85,.35);
  color:var(--tr-accent-soft); font-family:var(--tr-mono); font-size:12px;
  display:flex; align-items:center; justify-content:center; margin-top:1px;
}
.tr-step-num.ic { background:rgba(255,255,255,.05); border-color:var(--tr-border-strong); color:var(--tr-text-2); }
.tr-step-text { min-width:0; }
.tr-step-title { font-size:21px; font-weight:800; letter-spacing:-.03em; line-height:1.2; }
.tr-step-help { font-size:13.5px; color:var(--tr-text-3); margin-top:3px; line-height:1.45; font-weight:500; }

/* Native bordered containers become design cards (level 2). */
[class*="st-key-trcard"] {
  background: linear-gradient(180deg,rgba(255,255,255,.035),rgba(255,255,255,0) 30%), var(--tr-surface) !important;
  border: 1px solid var(--tr-border) !important;
  border-radius: 18px !important;
  padding: 24px !important;
  box-shadow: var(--tr-hi), var(--tr-shadow);
}
[class*="st-key-trcard"] [class*="st-key-trcard"] { border:none !important; padding:0 !important; box-shadow:none; }
[class*="st-key-trpanel"] {
  background: linear-gradient(180deg,rgba(255,255,255,.055),rgba(255,255,255,.012) 45%), var(--tr-surface) !important;
  border:1px solid rgba(255,255,255,.11) !important; border-radius:16px !important; padding:18px 18px 16px !important;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.10), 0 16px 34px -22px rgba(0,0,0,.95);
}
@media (max-width:1100px) { [class*="st-key-trcard"] { padding:16px !important; } }

/* ── the pick pattern: whole tile is the button ────────────────────── */
[class*="st-key-pick_"] { position:relative; }
[class*="st-key-pick_"] [data-testid="stVerticalBlock"] { gap:0 !important; }
[class*="st-key-pick_"] > [data-testid="stElementContainer"]:has(> .stButton),
[class*="st-key-pick_"] > [class*="st-key-loc_"],
[class*="st-key-pick_"] > [class*="st-key-movie_"],
[class*="st-key-pick_"] > [class*="st-key-pop_"],
[class*="st-key-pick_"] > [class*="st-key-th_"],
[class*="st-key-pick_"] > [class*="st-key-feat_"],
[class*="st-key-pick_"] > [class*="st-key-step_"],
[class*="st-key-pick_"] > [class*="st-key-interval_"],
[class*="st-key-pick_"] > [class*="st-key-datemode_"] {
  position:absolute !important; inset:0 !important; z-index:3; margin:0 !important;
  height:auto !important; min-height:0 !important; width:100% !important;
}
[class*="st-key-pick_"] .stButton { position:absolute; inset:0; margin:0; height:100%; width:100%; }
[class*="st-key-pick_"] .stButton > button {
  position:absolute; inset:0; width:100% !important; height:100% !important; min-height:0;
  opacity:0; cursor:pointer; border-radius:13px; padding:0; margin:0;
}
[class*="st-key-pick_"]:has(button:focus-visible) { outline:2px solid var(--tr-accent); outline-offset:2px; border-radius:13px; }
[class*="st-key-pick_"]:hover .tr-poster:not(.selected),
[class*="st-key-pick_"]:hover .tr-loc:not(.selected):not(.disabled),
[class*="st-key-pick_"]:hover .tr-throw:not(.selected),
[class*="st-key-pick_"]:hover .tr-feat:not(.selected):not(.off),
[class*="st-key-pick_"]:hover .tr-interval:not(.selected) { border-color:var(--tr-border-hover); }
[class*="st-key-pick_"]:hover .tr-throw:not(.selected),
[class*="st-key-pick_"]:hover .tr-interval:not(.selected),
[class*="st-key-pick_"]:hover .tr-loc:not(.selected):not(.disabled) { background:var(--tr-raised); }
[class*="st-key-pick_"]:hover .tr-poster:not(.selected), [class*="st-key-pick_"]:hover .tr-feat:not(.selected):not(.off),
[class*="st-key-pick_"]:hover .tr-loc:not(.selected):not(.disabled) {
  transform:translateY(-2px); box-shadow:0 16px 34px -20px rgba(0,0,0,.9);
}
[class*="st-key-pick_"]:active .tr-poster, [class*="st-key-pick_"]:active .tr-throw,
[class*="st-key-pick_"]:active .tr-feat:not(.off), [class*="st-key-pick_"]:active .tr-loc:not(.disabled),
[class*="st-key-pick_"]:active .tr-step-pip.done { transform:scale(.99); }

/* ── platform lockups ──────────────────────────────────────────────── */
.tr-platforms { display:grid; grid-template-columns:1.5fr 1fr 1fr; gap:12px; margin-bottom:6px; }
@media (max-width:1100px) { .tr-platforms { grid-template-columns:1fr 1fr; } .tr-platform.live { grid-column:span 2; } }
.tr-platform {
  padding:18px 20px; border-radius:16px; border:1px dashed rgba(255,255,255,.14);
  background:linear-gradient(180deg,rgba(255,255,255,.04),rgba(255,255,255,.012)); min-height:96px; min-width:0;
  box-shadow: var(--tr-hi); transition:border-color var(--tr-fast) var(--tr-ease), transform var(--tr-fast) var(--tr-ease);
}
.tr-platform:not(.live):hover { border-color:rgba(255,255,255,.24); transform:translateY(-1px); }
.tr-platform .lockup { display:flex; align-items:center; gap:11px; }
.tr-platform .lockup img { display:block; flex:none; opacity:.85; }
.tr-platform .lockup .name { font-size:17px; font-weight:700; color:#8E93A0; letter-spacing:-.01em; }
.tr-platform .soon {
  margin-top:12px; font-family:var(--tr-mono); font-size:10px;
  letter-spacing:.16em; text-transform:uppercase; color:var(--tr-text-4);
}
.tr-platform.live {
  position:relative; overflow:hidden; border:1.5px solid rgba(255,51,85,.9);
  background: linear-gradient(180deg,rgba(255,255,255,.06),rgba(255,255,255,0) 40%), linear-gradient(135deg,rgba(255,51,85,.22),rgba(255,51,85,.04)), var(--tr-surface);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.14), 0 22px 46px -20px rgba(255,51,85,.85), 0 0 0 4px rgba(255,51,85,.06);
}
.tr-platform.live::before {
  content:''; position:absolute; top:0; left:0; width:60px; height:100%;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.06),transparent);
  animation: tr-sweep 3.8s ease-in-out infinite;
}
.tr-platform .row { position:relative; display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; }
.tr-platform .row img { height:34px; width:auto; display:block; flex:none; max-width:100%; }
.tr-platform .meta {
  margin-top:12px; display:flex; align-items:center; gap:8px; position:relative;
  font-size:13.5px; font-weight:600; color:#E6E6EC; min-width:0;
}
.tr-platform .meta svg { flex:none; }

/* ── pills, dots, spinners ─────────────────────────────────────────── */
.tr-pill {
  display:inline-flex; align-items:center; gap:6px; padding:4px 9px; border-radius:999px;
  font-family:var(--tr-mono); font-size:10px; font-weight:500; letter-spacing:.1em; white-space:nowrap;
  text-transform:uppercase; line-height:1.3;
}
.tr-pill.lg { font-size:11px; padding:6px 12px; }
.tr-pill.ok      { background:rgba(62,213,152,.12); border:1px solid rgba(62,213,152,.30); color:var(--tr-success); }
.tr-pill.warn    { background:rgba(232,178,92,.12); border:1px solid rgba(232,178,92,.32); color:var(--tr-warning); }
.tr-pill.bad     { background:rgba(255,92,92,.12);  border:1px solid rgba(255,92,92,.38);  color:var(--tr-danger); }
.tr-pill.neutral { background:rgba(255,255,255,.05);border:1px solid var(--tr-border-strong);color:var(--tr-neutral); }
.tr-pill.connected { font-family:var(--tr-sans); font-size:11.5px; font-weight:700; letter-spacing:0; text-transform:none; padding:5px 11px; border-color:rgba(62,213,152,.32); }

.tr-dot { width:7px; height:7px; border-radius:50%; flex:none; display:inline-block; }
.tr-dot.ok   { background:var(--tr-success); box-shadow:0 0 12px var(--tr-success); }
.tr-dot.bad  { background:var(--tr-danger);  box-shadow:0 0 12px var(--tr-danger); }
.tr-dot.warn { background:var(--tr-warning); }
.tr-dot.grey { background:var(--tr-text-4); }
.tr-dot.live { animation: tr-pulse 1.8s ease-in-out infinite; }
.tr-dot.big  { width:8px; height:8px; }

.tr-spin {
  width:15px; height:15px; flex:none; border-radius:50%; display:inline-block;
  border:2px solid rgba(62,213,152,.25); border-top-color:var(--tr-success);
  animation: tr-spin 1s linear infinite;
}
.tr-spin.grey { border-color:rgba(255,255,255,.14); border-top-color:var(--tr-text-3); width:12px; height:12px; animation-duration:1.1s; }
.tr-spin.amber { border-color:rgba(232,178,92,.2); border-top-color:var(--tr-warning); }

@keyframes tr-pulse { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:.35;transform:scale(.82)} }
@keyframes tr-spin  { to { transform:rotate(360deg) } }
@keyframes tr-sweep { 0%{transform:translateX(-100%)} 100%{transform:translateX(220%)} }

/* ── step rail: a journey, with a connector between pips ───────────── */
.tr-steps { display:flex; gap:8px; margin:4px 0 6px; flex-wrap:wrap; }
.tr-step-pip {
  display:flex; align-items:center; gap:10px; min-width:0; position:relative;
  padding:11px 13px; border-radius:13px; background:linear-gradient(180deg,rgba(255,255,255,.04),rgba(255,255,255,.01));
  border:1px solid var(--tr-border); box-shadow: var(--tr-hi); transition:all var(--tr-fast) var(--tr-ease);
}
.tr-step-pip .n {
  width:22px; height:22px; flex:none; border-radius:7px; display:flex;
  align-items:center; justify-content:center; font-family:var(--tr-mono);
  font-size:11px; background:rgba(255,255,255,.05); color:var(--tr-text-4);
  border:1px solid rgba(255,255,255,.10);
}
.tr-step-pip .l { font-size:13.5px; font-weight:700; color:var(--tr-text-4); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.tr-step-pip.now {
  background:linear-gradient(180deg,rgba(255,255,255,.07),rgba(255,255,255,0) 50%), linear-gradient(135deg,rgba(255,51,85,.24),rgba(255,51,85,.06)), var(--tr-surface);
  border-color:rgba(255,51,85,.6); box-shadow: inset 0 1px 0 rgba(255,255,255,.14), 0 14px 30px -16px rgba(255,51,85,.9);
}
.tr-step-pip.now .n { background:var(--tr-accent); color:#fff; border-color:var(--tr-accent);
  box-shadow:0 4px 14px -4px rgba(255,51,85,.9); }
.tr-step-pip.now .l { color:#fff; font-weight:700; }
.tr-step-pip.done { border-color:rgba(62,213,152,.30); background:linear-gradient(180deg,rgba(255,255,255,.04),rgba(255,255,255,0)), rgba(62,213,152,.06); cursor:pointer; }
.tr-step-pip.done .n { background:rgba(62,213,152,.16); color:var(--tr-success); border-color:rgba(62,213,152,.35); }
.tr-step-pip.done .l { color:var(--tr-text-2); }
[class*="st-key-pick_step_"]:hover .tr-step-pip.done { border-color:rgba(62,213,152,.55); background:rgba(62,213,152,.09); }
[class*="st-key-pick_step_"]:hover .tr-step-pip.done .l { color:#fff; }
.tr-step-mobile { display:none; font-family:var(--tr-mono); font-size:10px; letter-spacing:.16em;
  color:var(--tr-text-4); text-transform:uppercase; margin:2px 0 4px; }

.tr-selected { display:flex; align-items:center; gap:9px; font-size:13px; color:var(--tr-text-2); margin:2px 0; min-width:0; }
.tr-selected .n { width:18px; height:18px; flex:none; border-radius:5px; display:flex; align-items:center;
  justify-content:center; font-size:10px; background:var(--tr-accent); color:#fff; }
.tr-selected b { color:var(--tr-text); font-weight:700; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.tr-summary-strip { display:flex; flex-wrap:wrap; gap:6px 8px; margin:2px 0 6px; min-width:0; }
.tr-summary-strip .i {
  display:inline-flex; align-items:center; gap:8px; padding:8px 13px 8px 9px; border-radius:11px;
  background:linear-gradient(180deg,rgba(255,255,255,.05),rgba(255,255,255,.015)); border:1px solid var(--tr-border); min-width:0; max-width:100%; box-shadow: var(--tr-hi);
}
.tr-summary-strip .n { width:18px; height:18px; flex:none; border-radius:5px; display:flex; align-items:center;
  justify-content:center; font-size:10px; background:rgba(62,213,152,.16); color:var(--tr-success); border:1px solid rgba(62,213,152,.35); }
.tr-summary-strip .k { font-family:var(--tr-mono); font-size:9.5px; letter-spacing:.14em;
  text-transform:uppercase; color:var(--tr-text-4); white-space:nowrap; }
.tr-summary-strip .v { font-size:13px; font-weight:600; color:var(--tr-text); min-width:0;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:520px; }
.tr-field-label { font-family:var(--tr-mono); font-size:10px; letter-spacing:.16em;
  text-transform:uppercase; color:var(--tr-text-4); margin:2px 0 6px; }
.tr-rail-title { display:flex; align-items:center; gap:9px; margin-bottom:12px; font-size:14px; font-weight:700; }

/* Back button: small, quiet, first thing in the card. */
[class*="st-key-trback"] { margin-bottom:2px; }
[class*="st-key-trback"] .stButton { width:auto; display:inline-block; }
[class*="st-key-trback"] .stButton > button {
  width:auto !important; min-height:36px; padding:6px 13px 6px 10px; font-size:12.5px; font-weight:700;
  color:var(--tr-text-2); background:linear-gradient(180deg,rgba(255,255,255,.07),rgba(255,255,255,.02)); border:1px solid rgba(255,255,255,.12);
  border-radius:10px; box-shadow: var(--tr-hi);
}
[class*="st-key-trback"] .stButton > button:hover { color:#fff; border-color:rgba(255,51,85,.5); background:rgba(255,51,85,.08); }

/* ── location tiles ────────────────────────────────────────────────── */
.tr-loc {
  position:relative; padding:24px 18px 22px; border-radius:16px;
  background:linear-gradient(180deg,rgba(255,255,255,.05),rgba(255,255,255,.012)), var(--tr-sunken);
  border:1px solid rgba(255,255,255,.10); text-align:center; transition:all var(--tr-fast) var(--tr-ease); min-height:132px;
  box-shadow: var(--tr-hi), 0 14px 30px -22px rgba(0,0,0,.9);
}
.tr-loc .pin { width:36px; height:36px; margin:0 auto; border-radius:11px; display:flex; align-items:center; justify-content:center;
  background:rgba(255,255,255,.05); border:1px solid var(--tr-border); }
.tr-loc.selected .pin { background:rgba(255,51,85,.14); border-color:rgba(255,51,85,.4); }
.tr-loc .n { font-size:19px; font-weight:800; letter-spacing:-.03em; margin-top:11px; }
.tr-loc .s { font-size:12px; color:var(--tr-text-3); margin-top:3px; font-weight:500; }
.tr-loc.selected {
  border:1.5px solid var(--tr-accent);
  background:linear-gradient(180deg,rgba(255,255,255,.07),rgba(255,255,255,0) 45%), linear-gradient(135deg,rgba(255,51,85,.22),rgba(255,51,85,.05)), var(--tr-raised);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.16), 0 22px 50px -22px rgba(255,51,85,.9), 0 0 0 4px rgba(255,51,85,.07);
}
.tr-loc.disabled { border-style:dashed; opacity:.5; }
.tr-loc.disabled .s { font-family:var(--tr-mono); font-size:10px; letter-spacing:.14em;
  text-transform:uppercase; color:var(--tr-text-4); }
.tr-check {
  position:absolute; top:8px; right:8px; width:22px; height:22px; border-radius:50%;
  background:var(--tr-accent); color:#fff; display:flex;
  align-items:center; justify-content:center; box-shadow:0 4px 14px rgba(255,51,85,.7); z-index:1;
}

/* ── theatre rows (compact, scannable) ─────────────────────────────── */
.tr-throw {
  display:flex; align-items:flex-start; gap:11px; padding:11px 13px; border-radius:13px;
  background:linear-gradient(180deg,rgba(255,255,255,.04),rgba(255,255,255,.01)), var(--tr-sunken);
  border:1px solid var(--tr-border); min-height:64px; height:100%; box-shadow: var(--tr-hi);
  transition:all var(--tr-fast) var(--tr-ease); box-sizing:border-box; min-width:0;
}
.tr-throw.selected { background:linear-gradient(180deg,rgba(255,255,255,.06),rgba(255,255,255,0) 50%), rgba(255,51,85,.12); border:1px solid rgba(255,51,85,.6);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.12), 0 12px 28px -18px rgba(255,51,85,.85); }
.tr-throw .box {
  width:18px; height:18px; flex:none; border-radius:5px; display:flex; align-items:center;
  justify-content:center; font-size:11px; color:#fff; border:1.5px solid rgba(255,255,255,.22); margin-top:8px;
}
.tr-throw.selected .box { background:var(--tr-accent); border:1px solid var(--tr-accent); }
.tr-throw .ab {
  width:34px; height:34px; flex:none; border-radius:9px; background:rgba(255,255,255,.05);
  border:1px solid rgba(255,255,255,.08); display:flex; align-items:center;
  justify-content:center; font-family:var(--tr-mono); font-size:10.5px; color:#9A9FAD;
}
.tr-throw .body { min-width:0; flex:1; }
.tr-throw .n { font-size:14.5px; font-weight:800; letter-spacing:-.01em; line-height:1.3; overflow-wrap:anywhere;
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.tr-throw .a { font-size:11.5px; color:var(--tr-text-3); margin-top:1px; overflow-wrap:anywhere; }
.tr-throw .fmts { margin-top:6px; display:flex; flex-wrap:wrap; gap:4px; min-width:0; }
.tr-throw.coming { border-style:dashed; }
.tr-throw.coming.selected { border-style:solid; }
.tr-star { color:var(--tr-warning); font-size:11px; margin-left:6px; vertical-align:1px; }
.tr-badge {
  display:inline-block; padding:4px 8px; border-radius:7px; background:linear-gradient(180deg,rgba(232,178,92,.16),rgba(232,178,92,.07));
  border:1px solid rgba(232,178,92,.32); box-shadow: inset 0 1px 0 rgba(255,255,255,.08); font-family:var(--tr-mono); font-size:9.5px;
  letter-spacing:.08em; text-transform:uppercase; color:var(--tr-warning); white-space:nowrap;
  max-width:100%; overflow:hidden; text-overflow:ellipsis; line-height:1.4; box-sizing:border-box;
}
.tr-badge.muted { background:rgba(255,255,255,.04); border-color:var(--tr-border); color:var(--tr-text-4);
  text-transform:none; letter-spacing:.04em; }
.tr-badge.live { background:rgba(62,213,152,.12); border-color:rgba(62,213,152,.35); color:var(--tr-success); }
.tr-badge.soon { background:rgba(232,178,92,.08); border-color:rgba(232,178,92,.28); color:var(--tr-warning); }

/* ── featured theatre quick-picks (level 3 surfaces) ───────────────── */
.tr-feat {
  position:relative; padding:15px 16px 14px; border-radius:16px;
  background:linear-gradient(180deg,rgba(255,255,255,.06),rgba(255,255,255,.015) 45%), var(--tr-raised);
  border:1px solid rgba(255,255,255,.12); min-height:150px; height:100%; box-sizing:border-box;
  transition:border-color var(--tr-fast) var(--tr-ease), background var(--tr-fast) var(--tr-ease), transform var(--tr-fast) var(--tr-ease), box-shadow .2s var(--tr-ease);
  min-width:0; display:flex; flex-direction:column; box-shadow: var(--tr-hi), 0 14px 30px -20px rgba(0,0,0,.95);
}
.tr-feat .k { display:flex; gap:6px; align-items:center; min-height:20px; }
.tr-feat .n { font-size:16.5px; font-weight:800; letter-spacing:-.02em; margin-top:9px; line-height:1.2;
  overflow-wrap:anywhere; padding-right:22px; }
.tr-feat .a { font-size:12px; color:var(--tr-text-3); margin-top:2px; font-weight:500; }
.tr-feat .s { font-size:11.5px; color:var(--tr-text-2); margin-top:auto; padding-top:8px; font-weight:600; line-height:1.35;
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.tr-feat .w { font-size:11px; color:var(--tr-text-4); margin-top:4px; line-height:1.4; }
.tr-feat.selected { border:1.5px solid var(--tr-accent);
  background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,0) 45%), linear-gradient(160deg,rgba(255,51,85,.20),rgba(255,51,85,.05)), var(--tr-raised);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.16), 0 20px 44px -20px rgba(255,51,85,.9), 0 0 0 4px rgba(255,51,85,.07); }
.tr-feat.soon { background:var(--tr-sunken); border-style:dashed; }
.tr-feat.soon .n { color:var(--tr-text-2); }
.tr-feat.soon.selected { border-style:solid; background:linear-gradient(160deg,rgba(255,51,85,.10),rgba(255,51,85,.02)), var(--tr-sunken); }
.tr-feat.soon.selected .n { color:var(--tr-text); }
.tr-feat.off { border-style:dashed; opacity:.42; background:transparent; box-shadow:none; }
[class*="st-key-pick_feat_"]:hover .tr-feat.soon:not(.selected) { border-color:rgba(232,178,92,.45); }

/* "View all N theatres": prominent but secondary */
[class*="st-key-trviewall"] .stButton > button {
  min-height:52px; font-size:13px; font-weight:800; letter-spacing:.08em; text-transform:uppercase;
  background:linear-gradient(180deg,rgba(255,255,255,.09),rgba(255,255,255,.03)); border:1px solid rgba(255,255,255,.18); color:var(--tr-text); border-radius:14px;
}
[class*="st-key-trviewall"] .stButton > button:hover { background:linear-gradient(180deg,rgba(255,255,255,.14),rgba(255,255,255,.05)); border-color:rgba(255,51,85,.5); color:#fff; }
[class*="st-key-hide_all_theatres"] .stButton > button { min-height:34px; font-size:12px; color:var(--tr-text-3); }

/* ── format panels ─────────────────────────────────────────────────── */
.tr-fmt-head { min-width:0; }
.tr-fmt-head .n { font-size:15px; font-weight:800; letter-spacing:-.01em; line-height:1.3; overflow-wrap:anywhere; }
.tr-fmt-head .a { font-size:12.5px; color:var(--tr-text-3); margin-top:2px; overflow-wrap:anywhere; font-weight:500; }
.tr-fmt-head .b { margin-top:12px; min-width:0; display:flex; flex-wrap:wrap; gap:5px; }
.tr-fmt-head .w { font-size:11.5px; color:var(--tr-text-3); margin-top:8px; line-height:1.45; }
[class*="st-key-trpanel"] .stCheckbox, [class*="st-key-trpanel"] [data-testid="stCheckbox"],
[class*="st-key-trpanel"] [class*="st-key-fmt_"] { margin:0; width:100% !important; max-width:100% !important; }
[class*="st-key-trpanel"] [data-testid="stVerticalBlock"] { gap:8px; }
[class*="st-key-trpanel"] .stCheckbox label {
  display:flex; align-items:center; gap:11px; padding:10px 13px; border-radius:12px; margin:0;
  border:1px solid rgba(255,255,255,.13); background:linear-gradient(180deg,rgba(255,255,255,.06),rgba(255,255,255,.02));
  min-height:46px; cursor:pointer; box-shadow: inset 0 1px 0 rgba(255,255,255,.08);
  transition:all var(--tr-fast) var(--tr-ease); width:100% !important; box-sizing:border-box;
}
[class*="st-key-trpanel"] .stCheckbox label:hover { border-color:rgba(255,255,255,.24); background:linear-gradient(180deg,rgba(255,255,255,.10),rgba(255,255,255,.04)); transform:translateY(-1px); }
[class*="st-key-trpanel"] .stCheckbox label:has(input:checked),
[class*="st-key-trpanel"] .stCheckbox label[data-selected="true"] {
  background:linear-gradient(180deg,rgba(255,255,255,.07),rgba(255,255,255,0) 50%), linear-gradient(90deg,rgba(255,51,85,.22),rgba(255,51,85,.06));
  border-color:rgba(255,51,85,.65); box-shadow: inset 0 1px 0 rgba(255,255,255,.14), 0 10px 24px -16px rgba(255,51,85,.8);
}
[class*="st-key-trpanel"] .stCheckbox label [data-testid="stMarkdownContainer"] p {
  font-size:13px; font-weight:600; color:var(--tr-text-2);
}
[class*="st-key-trpanel"] .stCheckbox label:has(input:checked) [data-testid="stMarkdownContainer"] p,
[class*="st-key-trpanel"] .stCheckbox label[data-selected="true"] [data-testid="stMarkdownContainer"] p {
  font-weight:700; color:var(--tr-text);
}
/* "Any format" is the special row: a dashed, labelled option, not another format. */
[class*="st-key-trpanel"] [class*="st-key-fmt_"][class*="_any"] label { border-style:dashed; }
[class*="st-key-trpanel"] [class*="st-key-fmt_"][class*="_any"] label [data-testid="stMarkdownContainer"] { flex:1; min-width:0; }
[class*="st-key-trpanel"] [class*="st-key-fmt_"][class*="_any"] label [data-testid="stMarkdownContainer"] p { display:flex; align-items:center; gap:10px; width:100%; }
[class*="st-key-trpanel"] [class*="st-key-fmt_"][class*="_any"] label [data-testid="stMarkdownContainer"] p::after {
  content:'ANY'; margin-left:auto; font-family:var(--tr-mono); font-size:9px; letter-spacing:.16em;
  padding:2px 6px; border-radius:5px; background:rgba(255,255,255,.06); color:var(--tr-text-4); border:1px solid var(--tr-border);
}
[class*="st-key-trpanel"] [class*="st-key-fmt_"][class*="_any"] label:has(input:checked) [data-testid="stMarkdownContainer"] p::after { color:var(--tr-accent-soft); border-color:rgba(255,51,85,.4); }
[class*="st-key-trpanel"] .stCheckbox label > div:first-of-type {
  width:14px !important; height:14px !important; min-width:14px; border-radius:50% !important; flex:none;
  background:var(--tr-sunken) !important; border:1.5px solid rgba(255,255,255,.22) !important;
  box-shadow:none !important; margin:0 !important; box-sizing:border-box;
}
[class*="st-key-trpanel"] .stCheckbox label:has(input:checked) > div:first-of-type,
[class*="st-key-trpanel"] .stCheckbox label[data-selected="true"] > div:first-of-type {
  border:4px solid var(--tr-accent) !important; background:#fff !important; box-shadow:0 0 0 3px rgba(255,51,85,.22) !important;
}
[class*="st-key-trpanel"] .stCheckbox label > div:first-of-type svg { display:none !important; }

/* ── interval / choice tiles ───────────────────────────────────────── */
.tr-interval {
  text-align:center; padding:18px 8px; border-radius:14px;
  background:linear-gradient(180deg,rgba(255,255,255,.05),rgba(255,255,255,.012)), var(--tr-sunken);
  border:1px solid rgba(255,255,255,.10); box-shadow: var(--tr-hi); transition:all var(--tr-fast) var(--tr-ease);
}
.tr-interval.selected { background:linear-gradient(160deg,rgba(255,51,85,.16),rgba(255,51,85,.04)), var(--tr-surface);
  border:1.5px solid var(--tr-accent); box-shadow:0 14px 30px -18px rgba(255,51,85,.8); }
.tr-interval .num { font-size:30px; font-weight:800; letter-spacing:-.04em; line-height:1.05; }
.tr-interval.selected .num { color:#fff; }
.tr-interval .unit { font-family:var(--tr-mono); font-size:10px; letter-spacing:.14em;
  text-transform:uppercase; color:var(--tr-text-3); margin-top:4px; }
.tr-interval.choice { padding:13px 8px; }
.tr-interval.choice .lbl { font-size:14px; font-weight:800; letter-spacing:-.01em; }
.tr-interval.choice .unit { font-family:var(--tr-sans); text-transform:none; letter-spacing:0; font-size:11.5px; }

/* ── catalogue status banner ───────────────────────────────────────── */
.tr-banner {
  display:flex; gap:12px; align-items:flex-start; padding:12px 15px; border-radius:13px;
  background:linear-gradient(180deg,rgba(255,255,255,.03),rgba(255,255,255,0)), var(--tr-sunken); border:1px solid var(--tr-border); min-width:0; box-shadow: var(--tr-hi);
}
.tr-banner .g {
  width:24px; height:24px; flex:none; border-radius:8px; display:flex; align-items:center;
  justify-content:center; font-size:12px; background:rgba(255,255,255,.05);
  border:1px solid rgba(255,255,255,.1); color:var(--tr-text-3);
}
.tr-banner .t { font-size:13px; font-weight:700; }
.tr-banner .s { font-size:12px; color:var(--tr-text-3); margin-top:2px; line-height:1.5; overflow-wrap:anywhere; }
.tr-banner.ok { background:rgba(62,213,152,.05); border-color:rgba(62,213,152,.22); }
.tr-banner.ok .g { background:rgba(62,213,152,.14); border-color:rgba(62,213,152,.3); color:var(--tr-success); }
.tr-banner.warn { background:rgba(232,178,92,.06); border-color:rgba(232,178,92,.24); }
.tr-banner.warn .g { background:rgba(232,178,92,.14); border-color:rgba(232,178,92,.32); color:var(--tr-warning); }
.tr-banner.bad { background:#140D0E; border-color:rgba(255,92,92,.3); }
.tr-banner.bad .g { background:rgba(255,92,92,.12); border-color:rgba(255,92,92,.35); color:var(--tr-danger); }

.tr-status { display:flex; gap:9px; align-items:flex-start; font-size:12.5px; line-height:1.5; color:var(--tr-text-3); min-width:0; }
.tr-status .g { flex:none; width:16px; display:flex; justify-content:center; padding-top:2px; }
.tr-status.ok .g { color:var(--tr-success); }
.tr-status.warn .g { color:var(--tr-warning); }
.tr-status.bad .g { color:var(--tr-danger); }
.tr-status.wait .g, .tr-status.info .g { color:var(--tr-text-4); }

/* ── poster tiles: a "now showing" shelf ───────────────────────────── */
.tr-poster {
  position:relative; border-radius:14px; overflow:hidden; height:100%; box-sizing:border-box;
  border:1px solid rgba(255,255,255,.11); background:linear-gradient(180deg,rgba(255,255,255,.05),rgba(255,255,255,.01)), var(--tr-raised);
  transition:border-color var(--tr-fast) var(--tr-ease), transform var(--tr-fast) var(--tr-ease), box-shadow .2s var(--tr-ease);
  display:flex; flex-direction:column; box-shadow: var(--tr-hi), 0 16px 34px -18px rgba(0,0,0,.95);
}
.tr-poster.selected {
  border:1.5px solid var(--tr-accent); background:linear-gradient(180deg,rgba(255,255,255,.06),rgba(255,255,255,0) 40%), rgba(255,51,85,.10);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.14), 0 20px 48px -18px rgba(255,51,85,.9), 0 0 0 4px rgba(255,51,85,.07);
}
.tr-poster .art {
  position:relative; width:100%; aspect-ratio:2/3; overflow:hidden; background:#14141A; flex:none;
  display:flex; align-items:center; justify-content:center;
}
.tr-poster.selected .art { background:#1B1216; }
.tr-poster .art img { position:absolute; inset:0; width:100%; height:100%; object-fit:cover; display:block;
  transition:transform .22s var(--tr-ease); }
[class*="st-key-pick_"]:hover .tr-poster .art img { transform:scale(1.04); }
.tr-poster .art svg { opacity:.9; }
.tr-poster .body { padding:10px 11px 10px; min-width:0; height:88px; box-sizing:border-box; display:flex; flex-direction:column; }
.tr-poster .t { font-size:13.5px; font-weight:800; letter-spacing:-.01em; line-height:1.25; overflow-wrap:anywhere;
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; height:2.5em; }
.tr-poster.compact .t { font-size:12px; }
.tr-poster .m { font-family:var(--tr-mono); font-size:10px; letter-spacing:.04em; color:var(--tr-text-3); margin-top:auto; line-height:1.3;
  height:2.6em; display:flex; flex-direction:column; justify-content:flex-end; }
.tr-poster .m span { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }

/* ── Streamlit widgets ─────────────────────────────────────────────── */
.stTextInput input, .stDateInput input, .stTimeInput input, .stTextArea textarea,
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="select"] > div {
  background: linear-gradient(180deg,rgba(255,255,255,.075),rgba(255,255,255,.04)) !important; color: var(--tr-text) !important;
  border-color: rgba(255,255,255,.16) !important; border-radius:12px !important;
  font-family: var(--tr-sans) !important; font-size:14.5px !important;
}
[data-baseweb="input"] { border:1px solid rgba(255,255,255,.16) !important; box-shadow: var(--tr-hi), 0 8px 20px -14px rgba(0,0,0,.9);
  transition:border-color var(--tr-fast) var(--tr-ease), box-shadow var(--tr-fast) var(--tr-ease); }
[data-baseweb="input"] input { border:none !important; padding:12px 15px !important; font-weight:600 !important; background:transparent !important; }
.stSelectbox [data-baseweb="select"] input, .stSelectbox [data-baseweb="select"] [data-baseweb="tag"], [data-baseweb="select"] > div > div { font-weight:500; }
.stTextInput input:focus, [data-baseweb="input"]:focus-within,
.stDateInput [data-baseweb="input"]:focus-within, .stTimeInput [data-baseweb="select"] > div:focus-within,
.stSelectbox [data-baseweb="select"] > div:focus-within {
  border-color: rgba(255,51,85,.75) !important;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.10), 0 0 0 4px rgba(255,51,85,.18), 0 14px 34px -14px rgba(255,51,85,.6) !important;
}
[data-testid="stDateInputField"], .stDateInput [data-testid="stDateInputField"] { background:var(--tr-sunken); border-radius:11px; }
.stTextInput input::placeholder, .stSelectbox [data-baseweb="select"] input::placeholder { color: #A5A9B6 !important; opacity:1; font-weight:500; }
.stSelectbox [data-baseweb="select"] > div > div:first-child { color:#A5A9B6 !important; font-weight:500; }
[data-testid="stWidgetLabel"] p { font-size:12.5px !important; color:var(--tr-text-3) !important; }
[data-baseweb="select"] svg, .stDateInput svg { color: var(--tr-text-4); }
/* Search boxes: the selectbox is the autocomplete — glassy, with a magnifier. */
.stSelectbox [data-baseweb="select"] > div { min-height:54px; padding-left:46px; padding-right:8px; border:1px solid rgba(255,255,255,.18) !important; border-radius:14px !important;
  background:linear-gradient(180deg,rgba(255,255,255,.085),rgba(255,255,255,.045)) !important;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.12), 0 12px 28px -16px rgba(0,0,0,.95);
  transition:border-color var(--tr-fast) var(--tr-ease), box-shadow var(--tr-fast) var(--tr-ease), background var(--tr-fast) var(--tr-ease); }
.stSelectbox [data-baseweb="select"] > div:hover { border-color:rgba(255,255,255,.28) !important; background:linear-gradient(180deg,rgba(255,255,255,.10),rgba(255,255,255,.055)) !important; }
.stSelectbox [data-baseweb="select"] { position:relative; }
.stSelectbox [data-baseweb="select"]::before {
  content:''; position:absolute; left:16px; top:50%; width:19px; height:19px; margin-top:-9.5px; z-index:2;
  pointer-events:none; opacity:.85; background-repeat:no-repeat; background-size:contain;
  background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23F2F2F4' stroke-width='2' stroke-linecap='round'><circle cx='11' cy='11' r='7'/><path d='M20 20l-3.5-3.5'/></svg>");
  transition:opacity var(--tr-fast) var(--tr-ease);
}
.stSelectbox [data-baseweb="select"]:focus-within::before { opacity:1; }
.stSelectbox [data-baseweb="select"] > div > div { color:var(--tr-text) !important; }
.stSelectbox [data-baseweb="select"] input { color:var(--tr-text) !important; font-size:15px !important; font-weight:600 !important; }
.stSelectbox [data-baseweb="select"] svg { color:var(--tr-text-2) !important; width:22px; height:22px; }
[data-baseweb="calendar"], [data-baseweb="popover"] > div, [data-baseweb="menu"] {
  background: var(--tr-surface) !important; border:1px solid var(--tr-border-strong) !important;
  border-radius:12px !important;
}
[data-baseweb="menu"] { padding:6px !important; max-height:340px; }
[data-baseweb="menu"] li, [data-baseweb="menu"] [role="option"] { color: var(--tr-text) !important; border-radius:8px; font-size:13.5px; padding:9px 11px; }
[data-baseweb="menu"] li:hover, [data-baseweb="menu"] li[aria-selected="true"],
[data-baseweb="menu"] [role="option"]:hover, [data-baseweb="menu"] [role="option"][aria-selected="true"] { background: rgba(255,51,85,.12) !important; }
[data-baseweb="calendar"] * { color: var(--tr-text) !important; }
[data-baseweb="calendar"] [aria-selected="true"] { background: var(--tr-accent) !important; }
/* Streamlit 1.59's selectbox is a react-aria ComboBox: a [role=group] box
   holding the input and the chevron. This is the glass search field. */
.stSelectbox .react-aria-ComboBox { position:relative; }
.stSelectbox .react-aria-ComboBox > [role="group"]::after {
  content:''; position:absolute; left:1px; right:1px; top:1px; height:46%; border-radius:14px 14px 40% 40%; pointer-events:none;
  background:linear-gradient(180deg,rgba(255,255,255,.075),rgba(255,255,255,0));
}
.stSelectbox .react-aria-ComboBox > [role="group"] {
  position:relative; min-height:56px; border-radius:15px !important; padding:0 8px 0 50px !important; box-sizing:border-box;
  border:1px solid rgba(255,255,255,.20) !important;
  background:linear-gradient(180deg,rgba(255,255,255,.10),rgba(255,255,255,.05)) !important;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.14), 0 14px 32px -16px rgba(0,0,0,.95);
  transition:border-color var(--tr-fast) var(--tr-ease), box-shadow var(--tr-fast) var(--tr-ease), background var(--tr-fast) var(--tr-ease);
}
.stSelectbox .react-aria-ComboBox > [role="group"]:hover {
  border-color:rgba(255,255,255,.32) !important; background:linear-gradient(180deg,rgba(255,255,255,.13),rgba(255,255,255,.06)) !important;
}
.stSelectbox .react-aria-ComboBox > [role="group"]:focus-within {
  border-color:rgba(255,51,85,.85) !important;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.16), inset 0 0 0 1px rgba(255,51,85,.22), inset 0 0 22px rgba(255,51,85,.08),
    0 0 0 4px rgba(255,51,85,.18), 0 16px 38px -14px rgba(255,51,85,.6);
}
.stSelectbox .react-aria-ComboBox input, .stSelectbox .react-aria-ComboBox button { position:relative; z-index:1; }
.stSelectbox .react-aria-ComboBox input {
  background:transparent !important; border:none !important; box-shadow:none !important; color:var(--tr-text) !important;
  font-family:var(--tr-sans) !important; font-size:15.5px !important; font-weight:600 !important; height:54px; padding:0 !important;
}
.stSelectbox .react-aria-ComboBox input::placeholder { color:#B3B7C3 !important; opacity:1 !important; font-weight:500 !important; }
.stSelectbox .react-aria-ComboBox button { color:var(--tr-text-2) !important; background:transparent !important; }
.stSelectbox .react-aria-ComboBox button svg { width:24px !important; height:24px !important; }
.stSelectbox .react-aria-ComboBox::before {
  content:''; position:absolute; left:18px; top:50%; width:20px; height:20px; margin-top:-10px; z-index:2;
  pointer-events:none; opacity:.9; background-repeat:no-repeat; background-size:contain;
  background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23F4F4F6' stroke-width='2.2' stroke-linecap='round'><circle cx='11' cy='11' r='7'/><path d='M20 20l-3.5-3.5'/></svg>");
}
.stSelectbox .react-aria-ComboBox:focus-within::before {
  background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23FF6B85' stroke-width='2.2' stroke-linecap='round'><circle cx='11' cy='11' r='7'/><path d='M20 20l-3.5-3.5'/></svg>");
}
/* Text inputs (email) get the same glass. */
.stTextInput [data-baseweb="input"], .stTextInput [data-baseweb="base-input"] { min-height:52px; border-radius:14px !important; }
/* The selectbox's suggestion list (react-aria listbox, portaled to body). */
[role="listbox"][data-rac] {
  background: var(--tr-surface) !important; border:1px solid var(--tr-border-strong) !important;
  border-radius:12px !important; padding:6px !important; box-shadow:0 24px 60px -20px rgba(0,0,0,.85);
}
[role="listbox"][data-rac] [role="option"] { border-radius:8px; font-size:13.5px; font-weight:600; color:var(--tr-text) !important; padding:10px 12px; }
[role="listbox"][data-rac] [role="option"]:hover, [role="listbox"][data-rac] [role="option"][data-focused],
[role="listbox"][data-rac] [role="option"][data-focus-visible], [role="listbox"][data-rac] [role="option"][aria-selected="true"] {
  background: rgba(255,51,85,.12) !important;
}
[role="listbox"][data-rac] [role="option"][data-key="__creatable__"] { border-top:1px solid var(--tr-border); margin-top:4px; border-radius:0 0 8px 8px; }
[role="listbox"][data-rac] [role="option"][data-key="__creatable__"] [data-item-hl] { font-size:0 !important; line-height:0; }
[role="listbox"][data-rac] [role="option"][data-key="__creatable__"] [data-item-hl]::before {
  content:'Show every match below  ↵'; font-size:12px; line-height:1.4; color:var(--tr-text-3); font-family:var(--tr-sans); font-weight:500;
}

/* ── buttons ───────────────────────────────────────────────────────── */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button, .stLinkButton > a {
  background: linear-gradient(180deg,rgba(255,255,255,.10),rgba(255,255,255,.04)); color: var(--tr-text);
  border:1px solid rgba(255,255,255,.16); border-radius:12px;
  font-family: var(--tr-sans); font-weight:700; font-size:13.5px; letter-spacing:-.01em;
  padding:.6rem 1rem; transition: all var(--tr-fast) var(--tr-ease); width:100%; min-height:46px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.14), 0 10px 22px -16px rgba(0,0,0,.9);
}
.stButton > button:hover, .stFormSubmitButton > button:hover {
  border-color: rgba(255,255,255,.28); color: #fff; background: linear-gradient(180deg,rgba(255,255,255,.15),rgba(255,255,255,.06));
  transform:translateY(-2px); box-shadow: inset 0 1px 0 rgba(255,255,255,.2), 0 16px 30px -16px rgba(0,0,0,.95);
}
.stButton > button:active { transform:translateY(0); }
.stButton > button:focus:not(:active) { border-color: rgba(255,51,85,.5); color: var(--tr-text); box-shadow:0 0 0 3px rgba(255,51,85,.12); }
.stButton > button [data-testid="stIconMaterial"] { font-size:18px; vertical-align:middle; }
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {
  background: linear-gradient(180deg,#FF6A85 0%,#FF3355 48%,#D4123F 100%); color:#fff; border:1px solid rgba(255,140,160,.45);
  font-size:14px; font-weight:800; letter-spacing:.06em; text-transform:uppercase;
  padding:.85rem 1rem; border-radius:14px; min-height:54px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.40), inset 0 -1px 0 rgba(0,0,0,.25), 0 20px 46px -16px rgba(255,51,85,.95), 0 0 0 4px rgba(255,51,85,.08);
  text-shadow: 0 1px 0 rgba(0,0,0,.2);
}
.stButton > button[kind="primary"]:hover { transform:translateY(-2px); color:#fff;
  background: linear-gradient(180deg,#FF7A93 0%,#FF3F5F 48%,#DC1746 100%);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.5), inset 0 -1px 0 rgba(0,0,0,.25), 0 26px 56px -16px rgba(255,51,85,1), 0 0 0 5px rgba(255,51,85,.10); }
.stButton > button[kind="primary"]:active { transform:translateY(0) scale(.995); filter:brightness(.98); }
.stButton > button[kind="primary"] p { font-size:14px; font-weight:800; }
/* START MONITORING — the one CTA */
[class*="st-key-start"] .stButton > button[kind="primary"] { font-size:17px; min-height:66px; letter-spacing:.1em; border-radius:18px; }
[class*="st-key-start"] .stButton > button[kind="primary"] p { font-size:16px; }
[class*="st-key-start"] .stButton > button[kind="primary"] [data-testid="stIconMaterial"] { font-size:22px; }
[class*="st-key-save_settings"] .stButton > button[kind="primary"] { min-height:44px; font-size:13px; border-radius:11px; box-shadow:none; }
[class*="st-key-save_settings"] .stButton > button[kind="primary"] p { font-size:13px; }
/* Destructive: tinted, not solid — visible without shouting (DESIGN-SPEC §5). */
.tr-danger .stButton > button, [class*="st-key-stop_"] .stButton > button, [class*="st-key-m_stop_"] .stButton > button {
  background: rgba(255,51,85,.12); border:1px solid rgba(255,51,85,.42);
  color: var(--tr-accent-soft); font-weight:700; letter-spacing:.06em; font-size:13px;
  text-transform:uppercase; padding:.75rem 1rem; border-radius:11px;
}
.tr-danger .stButton > button:hover, [class*="st-key-stop_"] .stButton > button:hover,
[class*="st-key-m_stop_"] .stButton > button:hover { background: rgba(255,51,85,.2); color:#fff; }
[class*="st-key-m_del_"] .stButton > button, [class*="st-key-m_clear_finished"] .stButton > button {
  color:var(--tr-text-3); font-size:12.5px; min-height:40px;
}
[class*="st-key-m_del_"] .stButton > button:hover, [class*="st-key-m_clear_finished"] .stButton > button:hover {
  color:var(--tr-danger); border-color:rgba(255,92,92,.45); background:rgba(255,92,92,.08);
}
[class*="st-key-tractions_"] .stButton > button { min-height:40px; }
[class*="st-key-tractions_"] [data-testid="stVerticalBlock"] { gap:.5rem; }
/* PROBLEM OCCURRED — solid, the one thing that is allowed to shout. */
[class*="st-key-prob_"] .stButton > button {
  background: linear-gradient(135deg,#FF5C5C,#D42F2F); border:none; color:#fff;
  font-weight:800; letter-spacing:.08em; text-transform:uppercase; font-size:13px;
  border-radius:11px; padding:.85rem 1rem; box-shadow:0 14px 34px -14px rgba(255,92,92,.9);
}
[class*="st-key-prob_"] .stButton > button:hover { filter:brightness(1.08); color:#fff; }
[class*="st-key-retry_"] .stButton > button {
  background: rgba(255,92,92,.12); border:1px solid rgba(255,92,92,.42); color:var(--tr-danger);
  font-weight:700; letter-spacing:.05em; text-transform:uppercase;
}
.tr-amber .stButton > button, [class*="st-key-m_ext_"] .stButton > button {
  background: rgba(232,178,92,.12); border:1px solid rgba(232,178,92,.4);
  color: var(--tr-warning); font-weight:700; letter-spacing:.05em; text-transform:uppercase; font-size:12.5px;
}
.tr-amber .stButton > button:hover, [class*="st-key-m_ext_"] .stButton > button:hover { background: rgba(232,178,92,.22); color:var(--tr-warning); }
[class*="st-key-select_all"] .stButton > button { min-height:34px; padding:6px 11px; font-size:12px;
  color:var(--tr-text-2); border:1px solid rgba(255,255,255,.12); background:rgba(255,255,255,.03); border-radius:8px; }
[class*="st-key-select_all"] .stButton > button [data-testid="stIconMaterial"] { font-size:16px; }

/* Toggle */
[data-testid="stCheckbox"] [role="checkbox"][aria-checked="true"],
.stToggle [data-baseweb="checkbox"] div[aria-checked="true"],
[data-baseweb="checkbox"] [data-testid="stCheckbox"] [aria-checked="true"] { background: var(--tr-accent) !important; }
.stCheckbox [data-baseweb="checkbox"] > div:first-of-type[role] { background: rgba(255,255,255,.2); }
.stCheckbox [data-baseweb="checkbox"] > div[aria-checked="true"] { background: rgba(255,51,85,.9) !important; }

/* The scroll-to-top helper is a zero-height component; give it no room. */
[data-testid="stElementContainer"]:has(> iframe[height="0"]), [data-testid="stElementContainer"]:has(> [data-testid="stCustomComponentV1"][height="0"]),
[data-testid="stElementContainer"]:has(> [data-testid="stCustomComponentV1"]) {
  height:0 !important; margin:0 !important; padding:0 !important; min-height:0 !important; overflow:hidden;
}
/* Expander (the "Browse all" grid): secondary */
[data-testid="stExpander"] details {
  background: linear-gradient(180deg,rgba(255,255,255,.075),rgba(255,255,255,.03)); border:1px solid rgba(255,255,255,.16) !important;
  border-radius:14px !important; box-shadow: inset 0 1px 0 rgba(255,255,255,.12), 0 12px 26px -18px rgba(0,0,0,.95);
  transition: transform var(--tr-fast) var(--tr-ease), border-color var(--tr-fast) var(--tr-ease), background var(--tr-fast) var(--tr-ease);
}
[data-testid="stExpander"] details:hover { border-color:rgba(255,255,255,.26) !important; background: linear-gradient(180deg,rgba(255,255,255,.11),rgba(255,255,255,.045)); transform:translateY(-2px); }
[data-testid="stExpander"] summary { font-size:14px; font-weight:700; color:var(--tr-text); padding:14px 16px; min-height:54px; align-items:center; }
[data-testid="stExpander"] summary:hover { color:#fff; }
[data-testid="stExpander"] summary [data-testid="stExpanderToggleIcon"], [data-testid="stExpander"] summary svg { color:var(--tr-accent-soft); width:22px; height:22px; }
[data-testid="stExpander"] summary::after { content:'BROWSE ALL'; margin-left:auto; white-space:nowrap; flex:none; font-family:var(--tr-mono) !important; font-size:9.5px; letter-spacing:.18em;
  color:var(--tr-text-3); padding:3px 8px; border:1px solid var(--tr-border); border-radius:6px; }
[data-testid="stExpander"] details[open] summary::after { content:'COLLAPSE'; }

/* Alerts — the palette's meanings, not Streamlit's */
[data-testid="stAlert"] { border-radius:12px; border:1px solid var(--tr-border); font-size:13.5px; }
[data-testid="stAlertContainer"] { background: var(--tr-sunken) !important; color: var(--tr-text-2) !important; }

hr { border-color: var(--tr-border) !important; margin:1.2rem 0 !important; }
[data-testid="stTooltipHoverTarget"] { color: var(--tr-text-3); }

/* ── status cards (rail): a command panel ──────────────────────────── */
.tr-monitor {
  border-radius:18px; padding:20px; border:1px solid rgba(62,213,152,.28);
  background: linear-gradient(180deg,rgba(255,255,255,.05),rgba(255,255,255,0) 40%), linear-gradient(180deg,rgba(62,213,152,.10),rgba(255,255,255,.02)), var(--tr-surface); min-width:0;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.10), 0 24px 50px -26px rgba(62,213,152,.35), var(--tr-shadow);
}
.tr-monitor.waiting { border-color:rgba(232,178,92,.28); background: linear-gradient(180deg,rgba(232,178,92,.07),rgba(255,255,255,.015)), var(--tr-surface); }
.tr-monitor.bad { border-color:rgba(255,92,92,.3); background: linear-gradient(180deg,rgba(255,92,92,.07),rgba(255,255,255,.015)), var(--tr-surface); }
.tr-monitor .head { display:flex; gap:14px; min-width:0; }
.tr-thumb {
  width:62px; height:88px; flex:none; border-radius:9px; background:#16161C;
  border:1px solid rgba(255,255,255,.08); display:flex; align-items:center; justify-content:center;
  overflow:hidden; box-shadow:0 10px 24px -14px rgba(0,0,0,.9);
}
.tr-thumb img { width:100%; height:100%; object-fit:cover; display:block; }
.tr-thumb.sm { width:34px; height:46px; border-radius:7px; }
.tr-monitor .title { font-size:16px; font-weight:800; line-height:1.25; letter-spacing:-.01em; overflow-wrap:anywhere; }
.tr-monitor .where { font-size:12px; color:var(--tr-text-3); margin-top:5px; }
.tr-monitor .tr-pill { font-size:11px; padding:6px 12px; }
.tr-metrics {
  margin-top:16px; display:grid; grid-template-columns:1fr 1fr; gap:12px;
  padding-top:14px; border-top:1px solid var(--tr-border);
}
.tr-metric { min-width:0; }
.tr-metric .k { font-family:var(--tr-mono); font-size:9.5px; letter-spacing:.14em; text-transform:uppercase; color:var(--tr-text-4); }
.tr-metric .v { font-size:15px; font-weight:700; margin-top:4px; overflow-wrap:anywhere; line-height:1.3; }
.tr-metric .v.mono { font-family:var(--tr-mono); font-weight:500; color:var(--tr-success); }
.tr-metric .v.amber { color:var(--tr-warning); }
.tr-metric .v.bad { color:var(--tr-danger); }
.tr-metric .v.soft { color:var(--tr-text-2); font-weight:600; font-size:13.5px; }
.tr-metric.wide { grid-column:span 2; }
.tr-note {
  margin-top:14px; display:flex; align-items:center; gap:11px; padding:12px 14px;
  border-radius:11px; background:rgba(255,255,255,.03); border:1px solid var(--tr-border); min-width:0;
}
.tr-note > div { min-width:0; }
.tr-note .t { font-size:13px; font-weight:700; }
.tr-note .s { font-size:11.5px; color:var(--tr-text-3); margin-top:1px; line-height:1.45; overflow-wrap:anywhere; }
.tr-note.bad { background:rgba(255,92,92,.06); border-color:rgba(255,92,92,.24); }
.tr-note.warn { background:rgba(232,178,92,.07); border-color:rgba(232,178,92,.22); }
.tr-note.ok { background:rgba(62,213,152,.07); border-color:rgba(62,213,152,.26); }

.tr-row {
  display:flex; align-items:center; justify-content:space-between; gap:12px;
  padding:11px 13px; border-radius:12px; background:var(--tr-surface);
  border:1px solid var(--tr-border); margin-bottom:8px; min-width:0;
}
.tr-row.ok   { background:rgba(62,213,152,.07); border-color:rgba(62,213,152,.32); }
.tr-row.warn { background:rgba(232,178,92,.07); border-color:rgba(232,178,92,.26); }
.tr-row.bad  { background:rgba(255,92,92,.06);  border-color:rgba(255,92,92,.24); }
.tr-row .body { min-width:0; }
.tr-row .n { font-size:13.5px; font-weight:700; overflow-wrap:anywhere; line-height:1.3; }
.tr-row .s { font-size:11.5px; color:var(--tr-text-3); margin-top:2px; overflow-wrap:anywhere; }
.tr-row .r { font-size:11px; color:var(--tr-text-3); display:flex; align-items:center; gap:7px; white-space:nowrap; flex:none; font-family:var(--tr-mono); letter-spacing:.06em; }
.tr-row .r.ok { color:var(--tr-success); font-weight:500; letter-spacing:.1em; font-size:10.5px; }
.tr-row .r.warn { color:var(--tr-warning); }
.tr-row .r.bad { color:var(--tr-danger); }

/* ── My Monitors cards ─────────────────────────────────────────────── */
[class*="st-key-trcard_mon_"] {
  background: linear-gradient(180deg,rgba(255,255,255,.04),rgba(255,255,255,0) 35%), var(--tr-surface) !important; border:1px solid var(--tr-border) !important;
  border-radius:18px !important; padding:20px 20px 18px !important; margin-bottom:8px; box-shadow: var(--tr-hi), var(--tr-shadow);
}
[class*="st-key-trcard_mon_"] > [data-testid="stVerticalBlock"] { gap:.6rem; }
.tr-mcard { min-width:0; border-radius:14px; }
.tr-mcard .top { display:flex; gap:14px; align-items:flex-start; min-width:0; }
.tr-mcard .info { min-width:0; flex:1; }
.tr-mcard .title { font-size:20px; font-weight:800; letter-spacing:-.03em; line-height:1.2; overflow-wrap:anywhere; margin-top:8px; }
.tr-mcard .where { font-size:12px; color:var(--tr-text-3); margin-top:4px; overflow-wrap:anywhere; }
/* The language row being watched: under the title, clearly secondary. */
.tr-mcard .lang, .tr-monitor .lang { display:inline-flex; align-items:center; gap:6px; margin-top:6px; padding:3px 9px 3px 7px;
  border-radius:7px; font-size:12px; font-weight:700; letter-spacing:.01em; color:var(--tr-text-2);
  background:rgba(255,255,255,.05); border:1px solid var(--tr-border-strong); }
.tr-mcard .lang svg, .tr-monitor .lang svg { color:var(--tr-accent-soft); }
.tr-monitor .lang { margin-top:5px; font-size:11.5px; }
.tr-history .t .lang { color:var(--tr-text-3); font-weight:600; }
.tr-mcard .pills { display:flex; flex-wrap:wrap; gap:6px; align-items:center; }
.tr-mcard .grid {
  margin-top:14px; padding-top:14px; border-top:1px solid var(--tr-border);
  display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:12px 16px;
}
.tr-mcard .grid .span2 { grid-column:span 2; }
.tr-mcard .targets { margin-top:14px; display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:8px; }
.tr-mcard .targets .tr-row { margin-bottom:0; background:var(--tr-sunken); }
.tr-mcard .tr-note { margin-top:12px; }
.tr-mcard.ok .tr-thumb { border-color:rgba(62,213,152,.3); }
.tr-mcard.bad .tr-thumb { border-color:rgba(255,92,92,.35); }
.tr-mcard.muted { opacity:.82; }
@media (max-width:1300px) { .tr-mcard .grid { grid-template-columns:repeat(2, minmax(0,1fr)); } }
@media (max-width:800px) { .tr-mcard .targets { grid-template-columns:1fr; } }

.tr-history { display:flex; gap:12px; padding:12px 13px; border-radius:13px; align-items:center;
  background:linear-gradient(180deg,rgba(255,255,255,.035),rgba(255,255,255,0) 50%), var(--tr-surface); border:1px solid var(--tr-border); margin-bottom:8px; min-width:0; box-shadow: var(--tr-hi);
  transition:border-color var(--tr-fast) var(--tr-ease); }
.tr-history:hover { border-color:var(--tr-border-hover); }
.tr-history .body { min-width:0; flex:1; }
.tr-history .t { font-size:13px; font-weight:700; overflow-wrap:anywhere; }
.tr-history .s { font-size:11.5px; color:var(--tr-text-3); margin-top:2px; overflow-wrap:anywhere; }
.tr-history .k { margin-top:6px; display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.tr-history .when { font-family:var(--tr-mono); font-size:10px; color:var(--tr-text-4); letter-spacing:.06em; }
.tr-history.ok { border-left:3px solid rgba(62,213,152,.6); }
.tr-history.warn { border-left:3px solid rgba(232,178,92,.6); }
.tr-history.bad { border-left:3px solid rgba(255,92,92,.6); }
.tr-rail-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:9px; }
.tr-rail-head a { font-size:11.5px; color:var(--tr-accent-soft); text-decoration:none; white-space:nowrap; }

/* ── the big live card ─────────────────────────────────────────────── */
.tr-live {
  position:relative; overflow:hidden; padding:30px; border-radius:20px;
  border:1.5px solid rgba(62,213,152,.4);
  background: radial-gradient(700px 300px at 10% 0%, rgba(62,213,152,.16), transparent 70%), #0C1210;
  margin-bottom:16px; min-width:0; box-shadow:0 30px 70px -30px rgba(62,213,152,.5);
}
.tr-live .top { display:flex; justify-content:space-between; align-items:flex-start; gap:24px; flex-wrap:wrap; }
.tr-live h2 { font-size:36px; font-weight:800; letter-spacing:-.03em; margin:16px 0 0; padding:0; line-height:1.06; overflow-wrap:anywhere; }
.tr-live .where { font-size:15px; color:#B8B8C2; margin-top:8px; }
.tr-live .detected { flex:none; text-align:right; }
.tr-live .detected .v { font-size:20px; font-weight:800; margin-top:4px; }
.tr-live .split { margin-top:26px; padding-top:22px; border-top:1px solid rgba(255,255,255,.09);
  display:flex; gap:36px; align-items:flex-end; flex-wrap:wrap; }
.tr-live .date { font-size:19px; font-weight:800; margin-top:6px; }
.tr-chips { display:flex; gap:9px; margin-top:8px; flex-wrap:wrap; }
.tr-chip { display:inline-block; padding:9px 15px; border-radius:9px; background:rgba(255,255,255,.05);
  border:1px solid rgba(255,255,255,.12); font-size:14px; font-weight:700; white-space:nowrap;
  color:var(--tr-text) !important; text-decoration:none !important; }
a.tr-chip { border-color:rgba(62,213,152,.45); }
a.tr-chip:hover { background:rgba(62,213,152,.12); }
.tr-live .foot { margin-top:20px; padding-top:18px; border-top:1px solid var(--tr-border);
  display:flex; gap:28px; flex-wrap:wrap; font-family:var(--tr-mono); font-size:11px;
  letter-spacing:.1em; text-transform:uppercase; color:var(--tr-text-4); }
.tr-live .actions { margin-top:24px; display:flex; gap:12px; flex-wrap:wrap; }
.tr-live .actions .tr-book { flex:1; min-width:240px; }
@media (max-width:900px) { .tr-live { padding:22px 18px; } .tr-live h2 { font-size:26px; } }

.tr-book {
  display:block; padding:18px; border-radius:13px; text-align:center; text-decoration:none !important;
  background: linear-gradient(135deg,#3ED598,#1FA875); color:#04120C !important;
  font-size:16px; font-weight:800; letter-spacing:.04em;
  box-shadow:0 20px 50px -20px rgba(62,213,152,.7);
}
.tr-book:hover { filter:brightness(1.06); }

/* ── state cards (stopped · expired · error · empty) ───────────────── */
.tr-state { padding:26px; border-radius:18px; display:flex; flex-direction:column; min-width:0; }
.tr-state .icon { width:40px; height:40px; border-radius:11px; display:flex; align-items:center; justify-content:center; font-size:14px; }
.tr-state .h { font-size:22px; font-weight:800; margin-top:16px; letter-spacing:-.03em; }
.tr-state .p { font-size:13.5px; margin-top:8px; line-height:1.55; overflow-wrap:anywhere; }
.tr-state .box { margin-top:16px; padding:13px; border-radius:11px; font-size:12px; line-height:1.6; overflow-wrap:anywhere; }
.tr-state.stopped { background:var(--tr-surface); border:1px solid rgba(255,255,255,.08); }
.tr-state.stopped .icon { background:rgba(255,255,255,.05); border:1px solid rgba(255,255,255,.1); color:#C9C9D2; }
.tr-state.stopped .p { color:var(--tr-text-3); }
.tr-state.stopped .box { background:var(--tr-sunken); border:1px solid var(--tr-border); color:#9A9AA4; }
.tr-state.expired { background:#12100B; border:1px solid rgba(232,178,92,.3); }
.tr-state.expired .icon { background:rgba(232,178,92,.12); border:1px solid rgba(232,178,92,.35); color:var(--tr-warning); font-size:15px; }
.tr-state.expired .p { color:#B5A588; }
.tr-state.expired .box { background:rgba(232,178,92,.07); border:1px solid rgba(232,178,92,.2); color:#D8C49E; }
.tr-state.error { background:#140D0E; border:1px solid rgba(255,92,92,.3); }
.tr-state.error .icon { background:rgba(255,92,92,.12); border:1px solid rgba(255,92,92,.35); color:var(--tr-danger); font-size:15px; }
.tr-state.error .p { color:#C0A3A9; }
.tr-state.error .box { background:rgba(255,92,92,.06); border:1px solid rgba(255,92,92,.18); color:#D9BDC3; line-height:1.7; }
.tr-state.empty {
  background:linear-gradient(180deg,rgba(255,255,255,.03),rgba(255,255,255,0)), var(--tr-sunken); border:1px dashed rgba(255,255,255,.16);
  align-items:center; text-align:center; justify-content:center; padding:30px 24px;
}
.tr-state.empty .ring {
  width:52px; height:52px; border-radius:50%; border:1px solid rgba(255,255,255,.12);
  display:flex; align-items:center; justify-content:center; font-size:17px; color:var(--tr-text-4);
}
.tr-state.empty .h { margin-top:16px; }
.tr-state.empty .p { color:var(--tr-text-3); max-width:260px; }

/* ── flash messages ────────────────────────────────────────────────── */
.tr-flash { display:flex; gap:11px; align-items:flex-start; padding:13px 15px; border-radius:12px;
  border:1px solid var(--tr-border); background:var(--tr-sunken); font-size:13.5px; color:var(--tr-text-2);
  line-height:1.5; margin-bottom:12px; min-width:0; }
.tr-flash > div { min-width:0; overflow-wrap:anywhere; }
.tr-flash .g { flex:none; font-size:14px; line-height:1.4; }
.tr-flash.success { background:rgba(62,213,152,.07); border-color:rgba(62,213,152,.26); color:#CFEFE0; }
.tr-flash.success .g { color:var(--tr-success); }
.tr-flash.warning { background:rgba(232,178,92,.07); border-color:rgba(232,178,92,.26); color:#E9DCC2; }
.tr-flash.warning .g { color:var(--tr-warning); }
.tr-flash.error { background:#140D0E; border-color:rgba(255,92,92,.3); color:#E6C8CE; }
.tr-flash.error .g { color:var(--tr-danger); }
.tr-flash b, .tr-flash strong { color:var(--tr-text); }

.tr-cta-help { display:flex; gap:9px; align-items:flex-start; font-size:12.5px; line-height:1.5; color:var(--tr-text-3); padding-top:12px; }
.tr-cta-help svg { margin-top:2px; }

/* ── mobile (≤768px): one column, on purpose ───────────────────────────
   Streamlit's own breakpoint for stacking columns is 640px; 768px is its
   `md` breakpoint and the width below which the sidebar becomes an overlay,
   so that is where the page becomes a phone layout. Every st.columns row
   stacks, except the groups named below, which are *meant* to sit side by
   side on a phone and are wrapped in a keyed container by the flow:
     trgrid_*   poster / theatre / tile grids — N per row via --tr-cols
     trpair_*   two short inputs or buttons that belong together
     trsteps    the 1–5 step rail, compact
   Nothing here is a desktop rule: at 769px and up this block is inert.  */
@media (max-width: 768px) {
  .block-container { padding: .75rem 1rem 5rem !important; }
  [data-testid="stMainBlockContainer"] { padding-top: .5rem !important; }
  [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; gap: .6rem !important; }
  [data-testid="stColumn"] { flex: 1 1 100% !important; width: 100% !important; min-width: 100% !important; }
  [data-testid="stColumn"] > div > [data-testid="stVerticalBlock"] { gap: .6rem; }

  /* grids: --tr-cols per row, an empty trailing column takes no room */
  [class*="st-key-trgrid_"] [data-testid="stHorizontalBlock"] { gap: .55rem !important; }
  [class*="st-key-trgrid_"] [data-testid="stColumn"] {
    --tr-cols: 2;
    flex: 0 0 calc((100% - (var(--tr-cols) - 1) * .55rem) / var(--tr-cols)) !important;
    width: calc((100% - (var(--tr-cols) - 1) * .55rem) / var(--tr-cols)) !important;
    min-width: 0 !important;
  }
  [class*="st-key-trgrid_"] [data-testid="stColumn"]:not(:has([data-testid="stElementContainer"])) { display: none; }
  [class*="st-key-trgrid_loc"] [data-testid="stColumn"],
  [class*="st-key-trgrid_interval"] [data-testid="stColumn"],
  [class*="st-key-trgrid_datemode"] [data-testid="stColumn"] { --tr-cols: 3; }
  [class*="st-key-trgrid_feat"] [data-testid="stColumn"],
  [class*="st-key-trgrid_th"] [data-testid="stColumn"] { --tr-cols: 1; }
  [class*="st-key-trpair"] [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; }
  [class*="st-key-trpair"] [data-testid="stColumn"] { flex: 1 1 0 !important; width: auto !important; min-width: 0 !important; }

  /* the sidebar is an overlay here; the toolbar's "open" control is the nav */
  [data-testid="stSidebar"] { width: min(300px, 86vw) !important; min-width: 0 !important; }
  .block-container { padding-top: 3.2rem !important; }
  /* the account chip sits at the top-right, below Streamlit's header so the
     transparent toolbar can never intercept a tap; a z-index keeps it on top. */
  [class*="st-key-tracct_top"] { margin: 0 0 10px auto; position: relative; z-index: 200; }
  .tr-acct-chip .n { max-width: 110px; font-size: 12.5px; }

  /* hero: responsive type, the mark becomes one quiet line under the copy */
  .tr-hero { padding: 24px 20px 20px; border-radius: 18px; margin-bottom: 16px; }
  .tr-hero-inner { flex-wrap: wrap; gap: 0; align-items: flex-start; }
  .tr-hero h1 { font-size: clamp(27px, 7.6vw, 36px); letter-spacing: -.035em; line-height: 1.04; }
  .tr-hero-lede { font-size: 15.5px; margin-top: 10px; }
  .tr-hero-sub { font-size: 13px; }
  .tr-hero-mark { display: flex; flex-direction: row; flex-wrap: wrap; gap: 0 7px; width: 100%; align-items: center;
    margin-top: 16px; padding: 12px 0 0; border-top: 1px solid rgba(255,255,255,.08); font-size: 9.5px; letter-spacing: .2em; line-height: 1.6; white-space: normal; }

  /* platforms: BookMyShow full width, District and PVR as a tidy pair */
  .tr-platforms { grid-template-columns: 1fr 1fr; gap: 10px; }
  .tr-platform.live { grid-column: span 2; }
  .tr-platform { padding: 14px 14px 13px; min-height: 0; border-radius: 14px; }
  .tr-platform .lockup { gap: 9px; min-width: 0; }
  .tr-platform .lockup img { width: 30px !important; height: 30px !important; }
  .tr-platform .lockup img[alt="PVR Cinemas"] { width: auto !important; height: 28px !important; }
  .tr-platform .lockup .name { font-size: 15px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
  .tr-platform .soon { margin-top: 9px; white-space: nowrap; }
  .tr-platform .row img { height: 30px; }
  .tr-platform .meta { font-size: 13px; }

  /* step rail: five compact pips and a readable "STEP n OF 5 · NAME" */
  [class*="st-key-trsteps"] [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: 5px !important; }
  [class*="st-key-trsteps"] [data-testid="stColumn"] { flex: 1 1 0 !important; width: auto !important; min-width: 0 !important; }
  .tr-step-pip { padding: 0; height: 38px; justify-content: center; gap: 0; border-radius: 11px; }
  .tr-step-pip .l { display: none; }
  .tr-step-pip .n { width: 24px; height: 24px; font-size: 11.5px; }
  .tr-step-mobile { display: block; font-size: 11px; letter-spacing: .18em; color: var(--tr-text-2); margin: 8px 0 2px; }

  /* cards and tiles */
  [class*="st-key-trcard"] { padding: 16px 14px !important; border-radius: 16px !important; }
  [class*="st-key-trpanel"] { padding: 14px 14px 12px !important; }
  .tr-step-title { font-size: 19px; }
  .tr-step-help { font-size: 13px; }
  .tr-loc { padding: 18px 10px 16px; min-height: 0; }
  .tr-loc .n { font-size: 16px; }
  .tr-loc .s { font-size: 11px; }
  .tr-loc.disabled .s { white-space: nowrap; letter-spacing: .1em; }
  .tr-interval { padding: 14px 6px; }
  .tr-interval .num { font-size: 26px; }
  .tr-interval.choice { padding: 11px 6px; }
  .tr-interval.choice .lbl { font-size: 13px; }
  .tr-interval.choice .unit { font-size: 11px; }
  .tr-feat { min-height: 0; padding: 14px 15px 13px; }
  .tr-poster .body { height: 84px; }
  .tr-summary-strip .v { max-width: 100%; }
  .tr-mcard .grid { grid-template-columns: repeat(2, minmax(0,1fr)); gap: 12px 12px; }
  .tr-mcard .title { font-size: 18px; }
  [class*="st-key-trcard_mon_"] { padding: 16px 14px 14px !important; }
  .tr-state.empty { padding: 26px 18px; }
  .tr-state .h { font-size: 20px; }
  .tr-live .detected { text-align: left; }
  .tr-cta-help { padding-top: 4px; }
  [class*="st-key-start"] .stButton > button[kind="primary"] { min-height: 60px; font-size: 15px; }
  /* 16px in the field itself stops iOS zooming the page on focus; the
     placeholder alone is a touch smaller so the whole sentence fits. */
  .stSelectbox .react-aria-ComboBox > [role="group"] { min-height: 54px; padding-left: 42px !important; }
  .stSelectbox .react-aria-ComboBox::before { left: 14px; }
  .stSelectbox .react-aria-ComboBox input { font-size: 16px !important; height: 52px; }
  .stSelectbox .react-aria-ComboBox input::placeholder { font-size: 14.5px !important; }
  .stTextInput [data-baseweb="input"] input { font-size: 16px !important; }
  .tr-page-foot { flex-wrap: wrap; gap: 6px; }
}
@media (max-width: 600px) {
  [class*="st-key-trgrid_movie"] [data-testid="stColumn"], [class*="st-key-trgrid_pop"] [data-testid="stColumn"] { --tr-cols: 2; }
}
@media (max-width: 480px) {
  /* two full-width buttons read better than two cramped ones */
  [class*="st-key-trpair_settings"] [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; }
  [class*="st-key-trpair_settings"] [data-testid="stColumn"] { flex: 1 1 100% !important; width: 100% !important; }
  /* a search field: tapping it opens the list, so the chevron is only width */
  .stSelectbox .react-aria-ComboBox > [role="group"] { padding-right: 4px !important; }
  .stSelectbox .react-aria-ComboBox > [role="group"] > button { display: none; }
}
@media (min-width: 601px) and (max-width: 768px) {
  [class*="st-key-trgrid_movie"] [data-testid="stColumn"], [class*="st-key-trgrid_pop"] [data-testid="stColumn"] { --tr-cols: 3; }
  [class*="st-key-trgrid_feat"] [data-testid="stColumn"], [class*="st-key-trgrid_th"] [data-testid="stColumn"] { --tr-cols: 2; }
}

/* ── typography, last so it wins ───────────────────────────────────── */
.stApp :is(p, h1, h2, h3, h4, h5, h6, li, label, input, textarea, button, a, div, span, td, th, small, strong, b, em, summary) {
  font-family: var(--tr-sans) !important;
}
.stApp :is(.tr-eyebrow, .tr-summary-strip .k, .tr-metric .k, .tr-metric .v.mono, .tr-version, .tr-version *, .tr-badge, .tr-step-num,
  .tr-interval .unit, .tr-throw .ab, .tr-summary .k, .tr-field-label, .tr-hero-mark,
  .tr-hero-mark *, .tr-step-pip .n, .tr-live .foot, .tr-live .foot *, .tr-platform .soon, .tr-step-mobile,
  .tr-rule .tr-eyebrow, .tr-pill:not(.connected), .tr-feat .k, .tr-count, .tr-row .r, .tr-history .when, .tr-auth-count, .tr-auth-diag, code, kbd, pre, .tr-mono) {
  font-family: var(--tr-mono) !important;
}
.stApp :is([data-testid="stIconMaterial"], .material-symbols-rounded, span[data-testid="stIconMaterial"]) {
  font-family: 'Material Symbols Rounded' !important;
}
</style>
""".replace("__NAV_ICONS__", _nav_css())


def inject() -> None:
    """Apply the theme once per rerun."""
    st.markdown(FONTS, unsafe_allow_html=True)
    st.markdown(CSS, unsafe_allow_html=True)


__all__ = ["CSS", "FONTS", "NAV_ICONS", "TOKENS", "inject"]
