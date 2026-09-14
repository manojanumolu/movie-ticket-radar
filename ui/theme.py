"""The TicketRadar look, as one CSS injection.

Every value here is lifted from ``ticketradar-ui-design-system-2`` —
``DESIGN-SPEC.md`` for the tokens and rules, ``TicketRadar.dc.html`` for the
literal sizes. Streamlit's own chrome is overridden rather than themed around,
because the brief is that this must not look like a Streamlit app.

Two fonts do all the work: Archivo for everything the user reads, JetBrains
Mono for anything that is data (labels, timers, eyebrows).

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
    rules = []
    for index, (name, (paths, width)) in enumerate(NAV_ICONS.items(), start=1):
        idle = _svg_uri(paths, "#8E8E98", width)
        active = _svg_uri(paths, "#FF5573", width)
        rules.append(
            f'[data-testid="stSidebar"] [role="radiogroup"] label:nth-of-type({index})::before '
            f"{{ background-image:{idle}; }}\n"
            f'[data-testid="stSidebar"] [role="radiogroup"] label:nth-of-type({index}):has(input:checked)::before, '
            f'[data-testid="stSidebar"] [role="radiogroup"] label:nth-of-type({index})[data-selected="true"]::before '
            f"{{ background-image:{active}; }}"
        )
    return "\n".join(rules)


CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');

:root {
  --tr-bg:#08080A; --tr-surface:#101014; --tr-sunken:#0A0A0D; --tr-shell:#0B0B0E;
  --tr-border:rgba(255,255,255,.07); --tr-border-strong:rgba(255,255,255,.14);
  --tr-text:#F2F2F4; --tr-text-2:#B0B0BA; --tr-text-3:#8E8E98; --tr-text-4:#6E6E7A;
  --tr-accent:#FF3355; --tr-accent-soft:#FF6B85; --tr-accent-deep:#D4123F;
  --tr-success:#3ED598; --tr-warning:#E8B25C;
  --tr-mono:'JetBrains Mono',ui-monospace,Consolas,monospace;
  --tr-sans:'Archivo',-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;
}

/* ── shell ─────────────────────────────────────────────────────────── */
html, body, [data-testid="stAppViewContainer"], .stApp {
  background:
    radial-gradient(1200px 600px at 60% -10%, rgba(255,51,85,.10), transparent 70%),
    var(--tr-bg) !important;
  color: var(--tr-text);
  font-family: var(--tr-sans);
  -webkit-font-smoothing: antialiased;
}
[data-testid="stHeader"] { background: transparent !important; }
[data-testid="stDecoration"], [data-testid="stToolbar"], #MainMenu, footer { display:none !important; }
[data-testid="stSidebarHeader"] { padding:.6rem .6rem 0 !important; height:auto !important; min-height:0 !important; }
.block-container { padding: 1.4rem 2.2rem 4rem !important; max-width: 1600px !important; }
[data-testid="stMainBlockContainer"] { padding-top: 1.1rem !important; }
@media (max-width: 1100px) { .block-container { padding: 1.1rem 1.1rem 3rem !important; } }
@media (max-width: 640px)  { .block-container { padding: .9rem .9rem 6rem !important; } }

h1, h2, h3, h4 { font-family: var(--tr-sans); letter-spacing:-.03em; color: var(--tr-text); }
p, span, div, label, li, input, button { font-family: var(--tr-sans); }
[data-testid="stMarkdownContainer"] p { margin-bottom:0; }
[data-testid="stCaptionContainer"], .stCaption, [data-testid="stCaptionContainer"] p {
  color: var(--tr-text-3) !important; font-size:12.5px !important; line-height:1.5;
}
[data-testid="stVerticalBlock"] { gap: .75rem; }

/* ── sidebar ───────────────────────────────────────────────────────── */
[data-testid="stSidebar"] {
  background: linear-gradient(180deg,#0E0E12,#0A0A0D) !important;
  border-right: 1px solid var(--tr-border);
  min-width: 236px !important;
}
[data-testid="stSidebar"] .block-container,
[data-testid="stSidebarUserContent"] { padding: 1.5rem 1.1rem 1.2rem !important; }
[data-testid="stSidebarCollapseButton"] button, [data-testid="stSidebarCollapseButton"] svg { color:var(--tr-text-3); }

.tr-logo { display:flex; align-items:center; gap:11px; padding:0 6px 4px; }
.tr-logo-mark {
  width:36px; height:36px; border-radius:10px; flex:none;
  background: linear-gradient(140deg,#FF3355,#B3123A);
  display:flex; align-items:center; justify-content:center;
  box-shadow:0 8px 24px -8px rgba(255,51,85,.8);
}
.tr-logo-mark svg { display:block; }
.tr-logo-name { font-size:17px; font-weight:700; letter-spacing:-.02em; line-height:1.15; }
.tr-logo-name em { font-style:normal; color:var(--tr-accent); }
.tr-logo-sub { font-size:11px; color:#77777F; margin-top:1px; }

.tr-quote {
  padding:16px; border-radius:14px; background:rgba(255,255,255,.03);
  border:1px solid rgba(255,255,255,.06); font-size:13px; line-height:1.55;
  color:#C9C9D2; font-style:italic;
}
.tr-version {
  display:flex; justify-content:space-between; font-family:var(--tr-mono);
  font-size:10px; color:#5A5A64; letter-spacing:.08em; margin-top:14px;
}

/* Sidebar nav: a radio group dressed as the design's nav rows. */
[data-testid="stSidebar"] [role="radiogroup"] { gap:4px; margin-top:6px; }
[data-testid="stSidebar"] [role="radiogroup"] label {
  display:flex; align-items:center; gap:12px; padding:12px 14px; border-radius:11px;
  font-size:14.5px; color:#9A9AA4; border:1px solid transparent; transition:background .15s ease;
  cursor:pointer; margin:0;
}
[data-testid="stSidebar"] [role="radiogroup"] label::before {
  content:''; width:18px; height:18px; flex:none; background-repeat:no-repeat;
  background-position:center; background-size:contain;
}
[data-testid="stSidebar"] [role="radiogroup"] label:hover { background:rgba(255,255,255,.04); }
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {
  background: linear-gradient(90deg,rgba(255,51,85,.16),rgba(255,51,85,.02));
  border:1px solid rgba(255,51,85,.32); color:#fff; font-weight:600;
}
[data-testid="stSidebar"] [role="radiogroup"] label > div > div > div:first-child:not([data-testid]) { display:none; }
[data-testid="stSidebar"] [role="radiogroup"] label > div, [data-testid="stSidebar"] [role="radiogroup"] label > div > div { width:100%; }
[data-testid="stSidebar"] [role="radiogroup"] label[data-selected="true"] {
  background: linear-gradient(90deg,rgba(255,51,85,.16),rgba(255,51,85,.02));
  border:1px solid rgba(255,51,85,.32); color:#fff; font-weight:600;
}
[data-testid="stSidebar"] [role="radiogroup"] label [data-testid="stMarkdownContainer"] p {
  font-size:14.5px; color:inherit; display:flex; align-items:center; gap:10px; width:100%;
}
[data-testid="stSidebar"] [role="radiogroup"] label code {
  margin-left:auto; font-family:var(--tr-mono); font-size:11px; padding:2px 7px; border-radius:6px;
  background:rgba(255,51,85,.14); color:#FF6B85; border:none;
}
__NAV_ICONS__

/* ── hero ──────────────────────────────────────────────────────────── */
.tr-hero {
  position:relative; overflow:hidden; padding:34px 36px 30px; border-radius:18px;
  background: linear-gradient(115deg,#101015 0%,#15070C 55%,#2A0A15 100%);
  border:1px solid var(--tr-border); margin-bottom:22px;
}
.tr-hero::after {
  content:''; position:absolute; inset:0; pointer-events:none;
  background: repeating-linear-gradient(90deg,rgba(255,255,255,.035) 0 2px,transparent 2px 14px);
  opacity:.5;
}
.tr-hero-inner { position:relative; display:flex; justify-content:space-between; gap:40px; flex-wrap:wrap; }
.tr-hero h1 { font-size:44px; font-weight:800; letter-spacing:-.035em; line-height:1.02; margin:0; padding:0; }
.tr-hero-lede { font-size:19px; color:#E3E3E8; margin-top:10px; font-weight:500; }
.tr-hero-sub { font-size:14px; color:var(--tr-text-3); margin-top:6px; }
.tr-hero-mark {
  flex:none; text-align:right; font-family:var(--tr-mono); font-size:12px; line-height:2;
  letter-spacing:.26em; color:rgba(255,255,255,.4); text-transform:uppercase;
}
.tr-hero-mark b { color:var(--tr-accent); font-weight:400; }
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
.tr-rule { display:flex; align-items:center; gap:10px; margin:6px 0 12px; }
.tr-rule .tr-eyebrow { font-size:11px; letter-spacing:.2em; color:var(--tr-text-3); }
.tr-rule .line { height:1px; flex:1; background:var(--tr-border); }

.tr-step-head { display:flex; align-items:center; gap:13px; }
.tr-step-num {
  width:27px; height:27px; flex:none; border-radius:9px;
  background:rgba(255,51,85,.14); border:1px solid rgba(255,51,85,.35);
  color:var(--tr-accent-soft); font-family:var(--tr-mono); font-size:12px;
  display:flex; align-items:center; justify-content:center;
}
.tr-step-title { font-size:16px; font-weight:700; }
.tr-step-help { font-size:13px; color:var(--tr-text-3); margin-top:2px; }

/* Native bordered containers become design cards (surface, hairline, 16, 22). */
[class*="st-key-trcard"] {
  background: var(--tr-surface) !important;
  border: 1px solid var(--tr-border) !important;
  border-radius: 16px !important;
  padding: 22px !important;
}
[class*="st-key-trcard"] [class*="st-key-trcard"] { border:none !important; padding:0 !important; }
[class*="st-key-trpanel"] {
  background: var(--tr-sunken) !important; border:1px solid var(--tr-border) !important;
  border-radius:13px !important; padding:16px !important;
}

/* ── the pick pattern: whole tile is the button ────────────────────── */
[class*="st-key-pick_"] { position:relative; }
[class*="st-key-pick_"] [data-testid="stVerticalBlock"] { gap:0 !important; }
/* Streamlit's element container is itself position:relative and has no
   height once its button is taken out of flow, so the *container* is what
   gets stretched over the tile — otherwise the button collapses to a 2px
   strip under the tile and nothing is clickable. Both the keyed-class and the
   :has() form are given so it holds even if one of them stops matching. */
[class*="st-key-pick_"] > [data-testid="stElementContainer"]:has(> .stButton),
[class*="st-key-pick_"] > [class*="st-key-loc_"],
[class*="st-key-pick_"] > [class*="st-key-movie_"],
[class*="st-key-pick_"] > [class*="st-key-th_"],
[class*="st-key-pick_"] > [class*="st-key-interval_"] {
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
[class*="st-key-pick_"]:hover .tr-interval:not(.selected) { border-color:rgba(255,255,255,.18); }

/* ── platform lockups ──────────────────────────────────────────────── */
.tr-platforms { display:grid; grid-template-columns:1.5fr 1fr 1fr; gap:12px; }
@media (max-width:900px) { .tr-platforms { grid-template-columns:1fr 1fr; } .tr-platform.live { grid-column:span 2; } }
.tr-platform {
  padding:18px 20px; border-radius:14px; border:1px dashed rgba(255,255,255,.10);
  background:rgba(255,255,255,.015); min-height:96px;
}
.tr-platform .lockup { display:flex; align-items:center; gap:11px; }
.tr-platform .lockup img { display:block; flex:none; }
.tr-platform .lockup .name { font-size:18px; font-weight:600; color:#9A9AA4; letter-spacing:-.01em; }
.tr-platform .soon {
  margin-top:12px; font-family:var(--tr-mono); font-size:10px;
  letter-spacing:.16em; text-transform:uppercase; color:var(--tr-text-4);
}
.tr-platform.live {
  position:relative; overflow:hidden; border:1.5px solid var(--tr-accent);
  background: linear-gradient(135deg,rgba(255,51,85,.14),rgba(255,51,85,.03));
}
.tr-platform.live::before {
  content:''; position:absolute; top:0; left:0; width:60px; height:100%;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.07),transparent);
  animation: tr-sweep 3.4s ease-in-out infinite;
}
.tr-platform .row { position:relative; display:flex; justify-content:space-between; align-items:center; gap:12px; }
.tr-platform .row img { height:35px; width:auto; display:block; flex:none; }
.tr-platform .meta {
  margin-top:12px; display:flex; align-items:center; gap:8px; position:relative;
  font-size:13.5px; font-weight:600; color:#E3E3E8;
}
.tr-platform .meta svg { flex:none; }

/* ── pills, dots, spinners ─────────────────────────────────────────── */
.tr-pill {
  display:inline-flex; align-items:center; gap:6px; padding:4px 9px; border-radius:999px;
  font-size:10.5px; font-weight:700; letter-spacing:.1em; white-space:nowrap;
}
.tr-pill.ok      { background:rgba(62,213,152,.12); border:1px solid rgba(62,213,152,.30); color:var(--tr-success); }
.tr-pill.warn    { background:rgba(232,178,92,.12); border:1px solid rgba(232,178,92,.32); color:var(--tr-warning); }
.tr-pill.bad     { background:rgba(255,51,85,.12);  border:1px solid rgba(255,51,85,.35);  color:var(--tr-accent-soft); }
.tr-pill.neutral { background:rgba(255,255,255,.05);border:1px solid var(--tr-border-strong);color:var(--tr-text-2); }
.tr-pill.connected { font-size:11.5px; font-weight:600; letter-spacing:0; padding:5px 11px; border-color:rgba(62,213,152,.32); }

.tr-dot { width:7px; height:7px; border-radius:50%; flex:none; display:inline-block; }
.tr-dot.ok   { background:var(--tr-success); box-shadow:0 0 12px var(--tr-success); }
.tr-dot.bad  { background:var(--tr-accent);  box-shadow:0 0 12px var(--tr-accent); }
.tr-dot.warn { background:var(--tr-warning); }
.tr-dot.grey { background:var(--tr-text-3); }
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

/* ── step strip ────────────────────────────────────────────────────── */
.tr-steps { display:flex; gap:8px; margin:4px 0 6px; flex-wrap:wrap; }
.tr-step-pip {
  flex:1; min-width:112px; display:flex; align-items:center; gap:9px;
  padding:11px 13px; border-radius:12px; background:var(--tr-sunken);
  border:1px solid var(--tr-border); transition:all .15s ease;
}
.tr-step-pip .n {
  width:22px; height:22px; flex:none; border-radius:7px; display:flex;
  align-items:center; justify-content:center; font-family:var(--tr-mono);
  font-size:11px; background:rgba(255,255,255,.06); color:var(--tr-text-4);
  border:1px solid rgba(255,255,255,.10);
}
.tr-step-pip .l { font-size:12.5px; color:var(--tr-text-4); white-space:nowrap; }
.tr-step-pip.now {
  background:linear-gradient(135deg,rgba(255,51,85,.14),rgba(255,51,85,.03));
  border-color:rgba(255,51,85,.45);
}
.tr-step-pip.now .n { background:var(--tr-accent); color:#fff; border-color:var(--tr-accent);
  box-shadow:0 4px 14px -4px rgba(255,51,85,.9); }
.tr-step-pip.now .l { color:#fff; font-weight:600; }
.tr-step-pip.done { border-color:rgba(62,213,152,.26); background:rgba(62,213,152,.05); }
.tr-step-pip.done .n { background:rgba(62,213,152,.16); color:var(--tr-success);
  border-color:rgba(62,213,152,.35); }
.tr-step-pip.done .l { color:var(--tr-text-2); }
.tr-step-mobile { display:none; font-family:var(--tr-mono); font-size:10px; letter-spacing:.16em;
  color:var(--tr-text-4); text-transform:uppercase; margin:4px 0 8px; }
@media (max-width:760px) { .tr-steps { display:none; } .tr-step-mobile { display:block; } }

.tr-summary {
  display:flex; align-items:center; gap:12px; padding:11px 14px; border-radius:12px;
  background:var(--tr-sunken); border:1px solid var(--tr-border); margin-bottom:8px;
}
.tr-summary .n {
  width:22px; height:22px; flex:none; border-radius:6px; display:flex; align-items:center;
  justify-content:center; font-size:12px; background:var(--tr-accent); color:#fff;
}
.tr-summary .k { font-family:var(--tr-mono); font-size:9.5px; letter-spacing:.14em;
  text-transform:uppercase; color:var(--tr-text-4); }
.tr-summary .v { font-size:13.5px; font-weight:600; margin-top:2px; color:var(--tr-text); }

.tr-field-label { font-family:var(--tr-mono); font-size:10px; letter-spacing:.16em;
  text-transform:uppercase; color:var(--tr-text-4); margin:4px 0 8px; }
.tr-count { font-family:var(--tr-mono); font-size:10.5px; padding:2px 7px; border-radius:6px;
  background:rgba(255,51,85,.14); color:var(--tr-accent-soft); }

/* ── location tiles ────────────────────────────────────────────────── */
.tr-loc {
  position:relative; padding:20px 18px; border-radius:14px; background:var(--tr-sunken);
  border:1px solid rgba(255,255,255,.08); text-align:center; transition:all .15s ease; min-height:118px;
}
.tr-loc svg { display:block; margin:0 auto; }
.tr-loc .n { font-size:17px; font-weight:700; letter-spacing:-.02em; margin-top:8px; }
.tr-loc .s { font-size:12px; color:var(--tr-text-3); margin-top:3px; }
.tr-loc.selected {
  border:1.5px solid var(--tr-accent);
  background:linear-gradient(135deg,rgba(255,51,85,.14),rgba(255,51,85,.03));
  box-shadow:0 18px 44px -20px rgba(255,51,85,.8);
}
.tr-loc.disabled { border-style:dashed; opacity:.55; }
.tr-loc.disabled .s { font-family:var(--tr-mono); font-size:10px; letter-spacing:.14em;
  text-transform:uppercase; color:var(--tr-text-4); }
.tr-check {
  position:absolute; top:8px; right:8px; width:22px; height:22px; border-radius:50%;
  background:var(--tr-accent); color:#fff; font-size:12px; display:flex;
  align-items:center; justify-content:center; box-shadow:0 4px 14px rgba(255,51,85,.7);
}

/* ── theatre rows (DESIGN checkbox row) ────────────────────────────── */
.tr-throw {
  display:flex; align-items:center; gap:12px; padding:11px 13px; border-radius:12px;
  background:var(--tr-sunken); border:1px solid var(--tr-border); min-height:60px; transition:all .15s ease;
}
.tr-throw.selected { background:rgba(255,51,85,.07); border:1px solid rgba(255,51,85,.45); }
.tr-throw .box {
  width:20px; height:20px; flex:none; border-radius:6px; display:flex; align-items:center;
  justify-content:center; font-size:12px; color:#fff; border:1.5px solid rgba(255,255,255,.2);
}
.tr-throw.selected .box { background:var(--tr-accent); border:1px solid var(--tr-accent); }
.tr-throw .ab {
  width:38px; height:38px; flex:none; border-radius:10px; background:rgba(255,255,255,.05);
  border:1px solid rgba(255,255,255,.08); display:flex; align-items:center;
  justify-content:center; font-family:var(--tr-mono); font-size:11px; color:#9A9AA4;
}
.tr-throw .n { font-size:14px; font-weight:600; line-height:1.25; }
.tr-throw .a { font-size:12px; color:var(--tr-text-3); margin-top:2px; }
.tr-throw .fmts { margin-left:auto; display:flex; flex-direction:column; align-items:flex-end; gap:4px; max-width:38%; min-width:0; }
.tr-throw .fmts .tr-badge { max-width:100%; overflow:hidden; text-overflow:ellipsis; }
.tr-badge {
  display:inline-block; padding:3px 8px; border-radius:6px; background:rgba(232,178,92,.10);
  border:1px solid rgba(232,178,92,.26); font-family:var(--tr-mono); font-size:9.5px;
  letter-spacing:.12em; text-transform:uppercase; color:var(--tr-warning); white-space:nowrap;
}
.tr-badge.muted { background:rgba(255,255,255,.04); border-color:var(--tr-border); color:var(--tr-text-4);
  text-transform:none; letter-spacing:.04em; }
@media (max-width:760px) { .tr-throw .fmts { display:none; } }

/* ── format panels ─────────────────────────────────────────────────── */
.tr-fmt-head .n { font-size:13px; font-weight:700; letter-spacing:.02em; text-transform:uppercase; }
.tr-fmt-head .a { font-size:11.5px; color:var(--tr-text-3); margin-top:3px; }
.tr-fmt-head .tr-badge { margin-top:10px; }
[class*="st-key-trpanel"] .stCheckbox { margin:0; width:100% !important; }
[class*="st-key-trpanel"] [data-testid="stVerticalBlock"] { gap:7px; }
[class*="st-key-trpanel"] .stCheckbox label {
  display:flex; align-items:center; gap:10px; padding:9px 11px; border-radius:9px; margin:0;
  border:1px solid var(--tr-border); background:transparent; min-height:40px; cursor:pointer;
  transition:all .12s ease; width:100% !important; box-sizing:border-box;
}
[class*="st-key-trpanel"] .stCheckbox label:hover { border-color:rgba(255,255,255,.16); }
[class*="st-key-trpanel"] .stCheckbox label:has(input:checked),
[class*="st-key-trpanel"] .stCheckbox label[data-selected="true"] {
  background:rgba(255,51,85,.10); border-color:rgba(255,51,85,.42);
}
[class*="st-key-trpanel"] .stCheckbox label [data-testid="stMarkdownContainer"] p {
  font-size:13px; font-weight:500; color:var(--tr-text-2);
}
[class*="st-key-trpanel"] .stCheckbox label:has(input:checked) [data-testid="stMarkdownContainer"] p,
[class*="st-key-trpanel"] .stCheckbox label[data-selected="true"] [data-testid="stMarkdownContainer"] p {
  font-weight:600; color:var(--tr-text);
}
/* radio-style dot, per the design's format rows */
[class*="st-key-trpanel"] .stCheckbox label > div:first-of-type {
  width:14px !important; height:14px !important; min-width:14px; border-radius:50% !important; flex:none;
  background:var(--tr-sunken) !important; border:1.5px solid rgba(255,255,255,.22) !important;
  box-shadow:none !important; margin:0 !important; box-sizing:border-box;
}
[class*="st-key-trpanel"] .stCheckbox label:has(input:checked) > div:first-of-type,
[class*="st-key-trpanel"] .stCheckbox label[data-selected="true"] > div:first-of-type {
  border:4px solid var(--tr-accent) !important; background:var(--tr-sunken) !important;
}
[class*="st-key-trpanel"] .stCheckbox label > div:first-of-type svg { display:none !important; }

/* ── interval tiles ────────────────────────────────────────────────── */
.tr-interval {
  text-align:center; padding:15px 8px; border-radius:12px; background:var(--tr-sunken);
  border:1px solid rgba(255,255,255,.08); transition:all .15s ease;
}
.tr-interval.selected { background:rgba(255,51,85,.10); border:1.5px solid var(--tr-accent); }
.tr-interval .num { font-size:22px; font-weight:700; letter-spacing:-.02em; line-height:1.1; }
.tr-interval .unit { font-family:var(--tr-mono); font-size:10px; letter-spacing:.14em;
  text-transform:uppercase; color:var(--tr-text-3); margin-top:3px; }

/* ── catalogue status banner ───────────────────────────────────────── */
.tr-banner {
  display:flex; gap:13px; align-items:flex-start; padding:13px 15px; border-radius:12px;
  background:var(--tr-sunken); border:1px solid var(--tr-border);
}
.tr-banner .g {
  width:26px; height:26px; flex:none; border-radius:8px; display:flex; align-items:center;
  justify-content:center; font-size:13px; background:rgba(255,255,255,.05);
  border:1px solid rgba(255,255,255,.1); color:var(--tr-text-3);
}
.tr-banner .t { font-size:13.5px; font-weight:600; }
.tr-banner .s { font-size:12px; color:var(--tr-text-3); margin-top:3px; line-height:1.5; }
.tr-banner.ok { background:rgba(62,213,152,.06); border-color:rgba(62,213,152,.24); }
.tr-banner.ok .g { background:rgba(62,213,152,.14); border-color:rgba(62,213,152,.3); color:var(--tr-success); }
.tr-banner.warn { background:rgba(232,178,92,.06); border-color:rgba(232,178,92,.24); }
.tr-banner.warn .g { background:rgba(232,178,92,.14); border-color:rgba(232,178,92,.32); color:var(--tr-warning); }
.tr-banner.bad { background:#130C0E; border-color:rgba(255,51,85,.28); }
.tr-banner.bad .g { background:rgba(255,51,85,.12); border-color:rgba(255,51,85,.35); color:var(--tr-accent-soft); }

/* ── poster tiles ──────────────────────────────────────────────────── */
.tr-poster {
  position:relative; border-radius:13px; overflow:hidden;
  border:1px solid rgba(255,255,255,.08); background:var(--tr-sunken); transition:border-color .15s;
}
.tr-poster.selected {
  border:1.5px solid var(--tr-accent); background:rgba(255,51,85,.07);
  box-shadow:0 14px 40px -18px rgba(255,51,85,.8);
}
.tr-poster .art {
  position:relative; height:172px; overflow:hidden; background:#14141A;
  display:flex; align-items:center; justify-content:center;
}
.tr-poster.selected .art { background:#1B1216; }
.tr-poster .art img { position:absolute; inset:0; width:100%; height:100%; object-fit:cover; display:block; }
.tr-poster .art svg { opacity:.9; }
.tr-poster .body { padding:10px 11px 12px; }
.tr-poster .t { font-size:13px; font-weight:600; line-height:1.3; }
.tr-poster .m { font-size:11px; color:var(--tr-text-3); margin-top:4px; font-family:var(--tr-mono); }
@media (max-width:760px) { .tr-poster .art { height:auto; aspect-ratio:2/3; } }

/* ── search input (step 1) ─────────────────────────────────────────── */
[class*="st-key-movie_query"] [data-baseweb="input"] { padding-left:8px; }
[class*="st-key-movie_query"] input { font-size:14px !important; }

/* ── status cards ──────────────────────────────────────────────────── */
.tr-monitor {
  border-radius:16px; padding:18px; border:1px solid rgba(62,213,152,.22);
  background: linear-gradient(180deg,rgba(62,213,152,.07),rgba(255,255,255,.015));
}
.tr-monitor.waiting { border-color:rgba(232,178,92,.26); background: linear-gradient(180deg,rgba(232,178,92,.06),rgba(255,255,255,.015)); }
.tr-monitor.bad { border-color:rgba(255,51,85,.26); background: linear-gradient(180deg,rgba(255,51,85,.06),rgba(255,255,255,.015)); }
.tr-monitor .head { display:flex; gap:14px; }
.tr-thumb {
  width:62px; height:88px; flex:none; border-radius:9px; background:#16161C;
  border:1px solid rgba(255,255,255,.08); display:flex; align-items:center; justify-content:center;
  overflow:hidden;
}
.tr-thumb img { width:100%; height:100%; object-fit:cover; display:block; }
.tr-thumb.sm { width:34px; height:46px; border-radius:7px; }
.tr-monitor .title { font-size:16px; font-weight:700; line-height:1.25; }
.tr-monitor .where { font-size:12px; color:var(--tr-text-3); margin-top:5px; }
.tr-metrics {
  margin-top:16px; display:grid; grid-template-columns:1fr 1fr; gap:12px;
  padding-top:14px; border-top:1px solid var(--tr-border);
}
.tr-metric .k {
  font-family:var(--tr-mono); font-size:9.5px; letter-spacing:.14em;
  text-transform:uppercase; color:var(--tr-text-4);
}
.tr-metric .v { font-size:15px; font-weight:600; margin-top:4px; }
.tr-metric .v.mono { font-family:var(--tr-mono); color:var(--tr-success); }
.tr-metric .v.amber { color:var(--tr-warning); }
.tr-metric .v.soft { color:var(--tr-text-2); font-weight:500; font-size:14px; }
.tr-metric.wide { grid-column:span 2; }
.tr-note {
  margin-top:14px; display:flex; align-items:center; gap:11px; padding:12px 14px;
  border-radius:11px; background:rgba(255,255,255,.03); border:1px solid var(--tr-border);
}
.tr-note .t { font-size:13px; font-weight:600; }
.tr-note .s { font-size:11.5px; color:var(--tr-text-3); margin-top:1px; }
.tr-note.bad { background:rgba(255,51,85,.06); border-color:rgba(255,51,85,.22); }
.tr-note.warn { background:rgba(232,178,92,.07); border-color:rgba(232,178,92,.22); }
.tr-note.ok { background:rgba(62,213,152,.07); border-color:rgba(62,213,152,.26); }

.tr-row {
  display:flex; align-items:center; justify-content:space-between; gap:12px;
  padding:13px 14px; border-radius:12px; background:var(--tr-surface);
  border:1px solid var(--tr-border); margin-bottom:8px;
}
.tr-row.ok   { background:rgba(62,213,152,.07); border-color:rgba(62,213,152,.32); }
.tr-row.warn { background:rgba(232,178,92,.07); border-color:rgba(232,178,92,.26); }
.tr-row.bad  { background:rgba(255,51,85,.06);  border-color:rgba(255,51,85,.22); }
.tr-row .n { font-size:13.5px; font-weight:600; }
.tr-row .s { font-size:11.5px; color:var(--tr-text-3); margin-top:2px; }
.tr-row .r { font-size:11px; color:var(--tr-text-3); display:flex; align-items:center; gap:7px; white-space:nowrap; }
.tr-row .r.ok { color:var(--tr-success); font-weight:700; letter-spacing:.1em; font-size:10.5px; }
.tr-row .r.warn { color:var(--tr-warning); }
.tr-row .r.bad { color:var(--tr-accent-soft); }

.tr-history { display:flex; gap:12px; padding:12px; border-radius:12px;
  background:var(--tr-surface); border:1px solid var(--tr-border); margin-bottom:8px; }
.tr-history .t { font-size:13px; font-weight:600; }
.tr-history .s { font-size:11.5px; color:var(--tr-text-3); margin-top:2px; }
.tr-history .k { font-size:11px; margin-top:5px; }
.tr-rail-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:9px; }
.tr-rail-head a { font-size:11.5px; color:var(--tr-accent-soft); text-decoration:none; white-space:nowrap; }

/* ── the big live card ─────────────────────────────────────────────── */
.tr-live {
  position:relative; overflow:hidden; padding:30px; border-radius:20px;
  border:1.5px solid rgba(62,213,152,.4);
  background: radial-gradient(700px 300px at 10% 0%, rgba(62,213,152,.16), transparent 70%), #0C1210;
  margin-bottom:16px;
}
.tr-live .top { display:flex; justify-content:space-between; align-items:flex-start; gap:24px; flex-wrap:wrap; }
.tr-live h2 { font-size:38px; font-weight:800; letter-spacing:-.03em; margin:16px 0 0; padding:0; line-height:1.06; }
.tr-live .where { font-size:15px; color:#B8B8C2; margin-top:8px; }
.tr-live .detected { flex:none; text-align:right; }
.tr-live .detected .v { font-size:20px; font-weight:700; margin-top:4px; }
.tr-live .split { margin-top:26px; padding-top:22px; border-top:1px solid rgba(255,255,255,.09);
  display:flex; gap:36px; align-items:flex-end; flex-wrap:wrap; }
.tr-live .date { font-size:19px; font-weight:700; margin-top:6px; }
.tr-chips { display:flex; gap:9px; margin-top:8px; flex-wrap:wrap; }
.tr-chip { display:inline-block; padding:9px 15px; border-radius:9px; background:rgba(255,255,255,.05);
  border:1px solid rgba(255,255,255,.12); font-size:14px; font-weight:600; white-space:nowrap;
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
  font-size:16px; font-weight:700; letter-spacing:.04em;
  box-shadow:0 20px 50px -20px rgba(62,213,152,.7);
}
.tr-book:hover { filter:brightness(1.06); }

/* ── state cards (stopped · expired · error · empty) ───────────────── */
.tr-state { padding:26px; border-radius:18px; display:flex; flex-direction:column; }
.tr-state .icon {
  width:40px; height:40px; border-radius:11px; display:flex; align-items:center;
  justify-content:center; font-size:14px;
}
.tr-state .h { font-size:20px; font-weight:700; margin-top:16px; letter-spacing:-.02em; }
.tr-state .p { font-size:13.5px; margin-top:8px; line-height:1.55; }
.tr-state .box { margin-top:16px; padding:13px; border-radius:11px; font-size:12px; line-height:1.6; }
.tr-state.stopped { background:var(--tr-surface); border:1px solid rgba(255,255,255,.08); }
.tr-state.stopped .icon { background:rgba(255,255,255,.05); border:1px solid rgba(255,255,255,.1); color:#C9C9D2; }
.tr-state.stopped .p { color:var(--tr-text-3); }
.tr-state.stopped .box { background:var(--tr-sunken); border:1px solid var(--tr-border); color:#9A9AA4; }
.tr-state.expired { background:#12100B; border:1px solid rgba(232,178,92,.3); }
.tr-state.expired .icon { background:rgba(232,178,92,.12); border:1px solid rgba(232,178,92,.35); color:var(--tr-warning); font-size:15px; }
.tr-state.expired .p { color:#B5A588; }
.tr-state.expired .box { background:rgba(232,178,92,.07); border:1px solid rgba(232,178,92,.2); color:#D8C49E; }
.tr-state.error { background:#130C0E; border:1px solid rgba(255,51,85,.28); }
.tr-state.error .icon { background:rgba(255,51,85,.12); border:1px solid rgba(255,51,85,.35); color:var(--tr-accent-soft); font-size:15px; }
.tr-state.error .p { color:#C0A3A9; }
.tr-state.error .box { background:rgba(255,51,85,.06); border:1px solid rgba(255,51,85,.18); color:#D9BDC3; line-height:1.7; }
.tr-state.empty {
  background:var(--tr-sunken); border:1px dashed rgba(255,255,255,.14);
  align-items:center; text-align:center; justify-content:center; padding:34px 26px;
}
.tr-state.empty .ring {
  width:52px; height:52px; border-radius:50%; border:1px solid rgba(255,255,255,.12);
  display:flex; align-items:center; justify-content:center; font-size:17px; color:var(--tr-text-4);
}
.tr-state.empty .h { margin-top:18px; }
.tr-state.empty .p { color:var(--tr-text-3); max-width:230px; }

/* ── flash messages ────────────────────────────────────────────────── */
.tr-flash { display:flex; gap:11px; align-items:flex-start; padding:13px 15px; border-radius:12px;
  border:1px solid var(--tr-border); background:var(--tr-sunken); font-size:13.5px; color:var(--tr-text-2);
  line-height:1.5; margin-bottom:14px; }
.tr-flash .g { flex:none; font-size:14px; line-height:1.4; }
.tr-flash.success { background:rgba(62,213,152,.07); border-color:rgba(62,213,152,.26); color:#CFEFE0; }
.tr-flash.success .g { color:var(--tr-success); }
.tr-flash.warning { background:rgba(232,178,92,.07); border-color:rgba(232,178,92,.26); color:#E9DCC2; }
.tr-flash.warning .g { color:var(--tr-warning); }
.tr-flash.error { background:#130C0E; border-color:rgba(255,51,85,.28); color:#E6C8CE; }
.tr-flash.error .g { color:var(--tr-accent-soft); }
.tr-flash b, .tr-flash strong { color:var(--tr-text); }

.tr-cta-help { font-size:12.5px; line-height:1.5; color:var(--tr-text-3); padding-top:10px; }
.tr-cta-help span { color:var(--tr-warning); }

/* ── Streamlit widgets ─────────────────────────────────────────────── */
.stTextInput input, .stDateInput input, .stTimeInput input, .stTextArea textarea,
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="select"] > div {
  background: var(--tr-sunken) !important; color: var(--tr-text) !important;
  border-color: rgba(255,255,255,.09) !important; border-radius:11px !important;
  font-family: var(--tr-sans) !important; font-size:14px !important;
}
[data-baseweb="input"] { border:1px solid rgba(255,255,255,.09) !important; }
[data-baseweb="input"] input { border:none !important; padding:11px 14px !important; }
.stTextInput input:focus, [data-baseweb="input"]:focus-within,
.stDateInput [data-baseweb="input"]:focus-within, .stTimeInput [data-baseweb="select"] > div:focus-within {
  border-color: rgba(255,51,85,.55) !important; box-shadow:none !important;
}
.stTextInput input::placeholder { color: var(--tr-text-4) !important; }
[data-testid="stWidgetLabel"] p { font-size:12.5px !important; color:var(--tr-text-3) !important; }
[data-baseweb="select"] svg, .stDateInput svg { color: var(--tr-text-4); }
[data-baseweb="calendar"], [data-baseweb="popover"] > div, [data-baseweb="menu"] {
  background: var(--tr-surface) !important; border:1px solid var(--tr-border-strong) !important;
  border-radius:12px !important;
}
[data-baseweb="calendar"] * { color: var(--tr-text) !important; }
[data-baseweb="menu"] li { color: var(--tr-text) !important; }
[data-baseweb="menu"] li:hover, [data-baseweb="menu"] li[aria-selected="true"] { background: rgba(255,51,85,.12) !important; }
[data-baseweb="calendar"] [aria-selected="true"] { background: var(--tr-accent) !important; }

.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button, .stLinkButton > a {
  background: transparent; color: var(--tr-text-2);
  border:1px solid var(--tr-border-strong); border-radius:11px;
  font-family: var(--tr-sans); font-weight:600; font-size:13px;
  padding:.6rem 1rem; transition: all .15s ease; width:100%; min-height:44px;
}
.stButton > button:hover, .stFormSubmitButton > button:hover {
  border-color: rgba(255,51,85,.5); color: var(--tr-text); background: rgba(255,51,85,.06);
}
.stButton > button:focus:not(:active) { border-color: rgba(255,51,85,.5); color: var(--tr-text); box-shadow:none; }
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {
  background: linear-gradient(135deg,#FF3355,#D4123F); color:#fff; border:none;
  font-size:15px; font-weight:700; letter-spacing:.04em; text-transform:uppercase;
  padding:.95rem 1rem; border-radius:14px; min-height:56px;
  box-shadow:0 20px 50px -18px rgba(255,51,85,.85);
}
.stButton > button[kind="primary"]:hover { filter:brightness(1.08); transform:translateY(-1px); color:#fff; }
.stButton > button[kind="primary"] p { font-size:15px; font-weight:700; }
[class*="st-key-start"] .stButton > button[kind="primary"] { font-size:17px; min-height:62px; }
[class*="st-key-start"] .stButton > button[kind="primary"] p { font-size:17px; }
/* Destructive: tinted, not solid — visible without shouting (DESIGN-SPEC §5). */
.tr-danger .stButton > button, [class*="st-key-stop_"] .stButton > button, [class*="st-key-m_stop_"] .stButton > button {
  background: rgba(255,51,85,.12); border:1px solid rgba(255,51,85,.42);
  color: var(--tr-accent-soft); font-weight:700; letter-spacing:.06em; font-size:13.5px;
  text-transform:uppercase; padding:.85rem 1rem; border-radius:11px;
}
.tr-danger .stButton > button:hover, [class*="st-key-stop_"] .stButton > button:hover,
[class*="st-key-m_stop_"] .stButton > button:hover { background: rgba(255,51,85,.2); color:#fff; }
.tr-amber .stButton > button, [class*="st-key-m_ext_"] .stButton > button {
  background: rgba(232,178,92,.14); border:1px solid rgba(232,178,92,.4);
  color: var(--tr-warning); font-weight:700; letter-spacing:.05em; text-transform:uppercase;
}
.tr-amber .stButton > button:hover, [class*="st-key-m_ext_"] .stButton > button:hover { background: rgba(232,178,92,.22); color:var(--tr-warning); }
[class*="st-key-back"] .stButton > button { min-height:40px; font-size:12.5px; color:var(--tr-text-3); }
[class*="st-key-select_all"] .stButton > button { min-height:34px; padding:6px 11px; font-size:12px;
  color:#C9C9D2; border:1px solid rgba(255,255,255,.12); background:rgba(255,255,255,.03); border-radius:8px; }

/* Toggle */
[data-testid="stCheckbox"] [role="checkbox"][aria-checked="true"],
.stToggle [data-baseweb="checkbox"] div[aria-checked="true"],
[data-baseweb="checkbox"] [data-testid="stCheckbox"] [aria-checked="true"] { background: var(--tr-accent) !important; }
.stCheckbox [data-baseweb="checkbox"] > div:first-of-type[role] { background: rgba(255,255,255,.2); }
.stCheckbox [data-baseweb="checkbox"] > div[aria-checked="true"] { background: rgba(255,51,85,.9) !important; }

/* Expander */
[data-testid="stExpander"] details {
  background: var(--tr-surface); border:1px solid var(--tr-border) !important;
  border-radius:14px !important;
}
[data-testid="stExpander"] summary { font-size:13.5px; font-weight:600; color:var(--tr-text-2); }

/* Alerts — the palette's meanings, not Streamlit's */
[data-testid="stAlert"] { border-radius:12px; border:1px solid var(--tr-border); font-size:13.5px; }
[data-testid="stAlertContainer"] { background: var(--tr-sunken) !important; color: var(--tr-text-2) !important; }

hr { border-color: var(--tr-border) !important; margin:1.2rem 0 !important; }
[data-testid="stTooltipHoverTarget"] { color: var(--tr-text-3); }

/* ── typography, last so it wins ───────────────────────────────────── */
/* Streamlit sets its own theme font on every element with high-specificity
   emotion classes; the design has exactly two typefaces, so assert them. */
.stApp :is(p, h1, h2, h3, h4, h5, h6, li, label, input, textarea, button, a, div, span, td, th, small, strong, b, em) {
  font-family: var(--tr-sans) !important;
}
.stApp :is(.tr-eyebrow, .tr-metric .k, .tr-metric .v.mono, .tr-version, .tr-badge, .tr-step-num,
  .tr-interval .unit, .tr-throw .ab, .tr-poster .m, .tr-summary .k, .tr-field-label, .tr-hero-mark,
  .tr-hero-mark *, .tr-step-pip .n, .tr-live .foot, .tr-live .foot *, .tr-platform .soon, .tr-step-mobile,
  .tr-rule .tr-eyebrow, code, kbd, pre, .tr-mono) {
  font-family: var(--tr-mono) !important;
}
.stApp :is([data-testid="stIconMaterial"], .material-symbols-rounded, span[data-testid="stIconMaterial"]) {
  font-family: 'Material Symbols Rounded' !important;
}
</style>
""".replace("__NAV_ICONS__", _nav_css())


def inject() -> None:
    """Apply the theme once per rerun."""
    st.markdown(CSS, unsafe_allow_html=True)


__all__ = ["CSS", "NAV_ICONS", "TOKENS", "inject"]
