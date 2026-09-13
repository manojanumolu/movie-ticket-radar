"""The TicketRadar look, as one CSS injection.

Every value here is lifted from ``TicketRadar UI Design System`` —
``DESIGN-SPEC.md`` for the tokens and rules, ``TicketRadar.dc.html`` for the
literal sizes. Streamlit's own chrome is overridden rather than themed around,
because the brief is that this must not look like a Streamlit app.

Two fonts do all the work: Archivo for everything the user reads, JetBrains
Mono for anything that is data (labels, timers, eyebrows).
"""

from __future__ import annotations

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
[data-testid="stHeader"], [data-testid="stToolbar"] { background: transparent !important; }
footer, #MainMenu { visibility: hidden; }

.block-container { padding: 1.6rem 2.2rem 4rem !important; max-width: 1600px !important; }
[data-testid="stMainBlockContainer"] { padding-top: 1.2rem !important; }

/* Streamlit stacks columns below ~640px on its own; these keep the gutters
   sane on the way down (DESIGN-SPEC §9). */
@media (max-width: 1100px) { .block-container { padding: 1.1rem 1.1rem 3rem !important; } }
@media (max-width: 640px)  { .block-container { padding: .9rem .9rem 6rem !important; } }

h1, h2, h3, h4 { font-family: var(--tr-sans); letter-spacing:-.03em; color: var(--tr-text); }
p, span, div, label, li { font-family: var(--tr-sans); }

/* ── sidebar ───────────────────────────────────────────────────────── */
[data-testid="stSidebar"] {
  background: linear-gradient(180deg,#0E0E12,#0A0A0D) !important;
  border-right: 1px solid var(--tr-border);
}
[data-testid="stSidebar"] .block-container,
[data-testid="stSidebarUserContent"] { padding: 1.4rem 1.1rem !important; }

.tr-logo { display:flex; align-items:center; gap:11px; padding:0 6px 4px; }
.tr-logo-mark {
  width:34px; height:34px; border-radius:10px; flex:none;
  background: linear-gradient(140deg,#FF3355,#B3123A);
  display:flex; align-items:center; justify-content:center;
  font-weight:800; font-size:15px; color:#fff;
  box-shadow:0 8px 24px -8px rgba(255,51,85,.8);
}
.tr-logo-name { font-size:17px; font-weight:700; letter-spacing:-.02em; }
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

/* Sidebar nav: a radio group dressed as nav rows. */
[data-testid="stSidebar"] [role="radiogroup"] { gap:4px; }
[data-testid="stSidebar"] [role="radiogroup"] label {
  padding:11px 14px; border-radius:11px; font-size:14px; color:#9A9AA4;
  border:1px solid transparent; transition:background .15s ease;
}
[data-testid="stSidebar"] [role="radiogroup"] label:hover { background:rgba(255,255,255,.04); }
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {
  background: linear-gradient(90deg,rgba(255,51,85,.16),rgba(255,51,85,.02));
  border:1px solid rgba(255,51,85,.32); color:#fff; font-weight:600;
}
[data-testid="stSidebar"] [role="radiogroup"] label > div:first-child { display:none; }

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
.tr-hero h1 { font-size:44px; font-weight:800; letter-spacing:-.035em; line-height:1.02; margin:0; }
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
.tr-rule .line { height:1px; flex:1; background:var(--tr-border); }

.tr-card {
  background:var(--tr-surface); border:1px solid var(--tr-border);
  border-radius:16px; padding:22px;
}
.tr-step-head { display:flex; align-items:center; gap:13px; }
.tr-step-num {
  width:27px; height:27px; flex:none; border-radius:9px;
  background:rgba(255,51,85,.14); border:1px solid rgba(255,51,85,.35);
  color:var(--tr-accent-soft); font-family:var(--tr-mono); font-size:12px;
  display:flex; align-items:center; justify-content:center;
}
.tr-step-title { font-size:16px; font-weight:700; }
.tr-step-help { font-size:13px; color:var(--tr-text-3); margin-top:2px; }

/* Native bordered containers become design cards.
   Hooked via st.container(key="trcard_…"), which Streamlit renders as a
   stable `st-key-<key>` class — unlike the internal data-testid for the
   border wrapper, which has been renamed between releases. */
[class*="st-key-trcard"] {
  background: var(--tr-surface) !important;
  border: 1px solid var(--tr-border) !important;
  border-radius: 16px !important;
  padding: 22px !important;
}
[class*="st-key-trcard"] [class*="st-key-trcard"] { border:none !important; padding:0 !important; }

/* ── platform selector ─────────────────────────────────────────────── */
.tr-platforms { display:grid; grid-template-columns:1.35fr 1fr 1fr 1fr; gap:12px; }
@media (max-width:900px) { .tr-platforms { grid-template-columns:1fr 1fr; } }
.tr-platform {
  padding:18px 20px; border-radius:14px; border:1px dashed rgba(255,255,255,.10);
  background:rgba(255,255,255,.015); opacity:.55;
}
.tr-platform .name { font-size:17px; font-weight:600; color:#9A9AA4; }
.tr-platform .soon {
  margin-top:10px; font-family:var(--tr-mono); font-size:10px;
  letter-spacing:.16em; text-transform:uppercase; color:var(--tr-text-4);
}
.tr-platform.live {
  position:relative; overflow:hidden; opacity:1; border:1.5px solid var(--tr-accent);
  background: linear-gradient(135deg,rgba(255,51,85,.14),rgba(255,51,85,.03));
}
.tr-platform.live::before {
  content:''; position:absolute; top:0; left:0; width:60px; height:100%;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.07),transparent);
  animation: tr-sweep 3.4s ease-in-out infinite;
}
.tr-platform .row { position:relative; display:flex; justify-content:space-between; align-items:center; gap:10px; }
.tr-platform .brand { font-size:21px; font-weight:700; letter-spacing:-.02em; color:var(--tr-text); }
.tr-platform .brand em { font-style:normal; color:var(--tr-accent); }
.tr-platform .meta {
  margin-top:12px; display:flex; align-items:center; gap:7px;
  font-size:13px; color:#C9C9D2; position:relative;
}

/* ── pills, dots, spinners ─────────────────────────────────────────── */
.tr-pill {
  display:inline-flex; align-items:center; gap:6px; padding:4px 10px; border-radius:999px;
  font-size:10.5px; font-weight:700; letter-spacing:.1em; white-space:nowrap;
}
.tr-pill.ok      { background:rgba(62,213,152,.12); border:1px solid rgba(62,213,152,.30); color:var(--tr-success); }
.tr-pill.warn    { background:rgba(232,178,92,.12); border:1px solid rgba(232,178,92,.32); color:var(--tr-warning); }
.tr-pill.bad     { background:rgba(255,51,85,.12);  border:1px solid rgba(255,51,85,.35);  color:var(--tr-accent-soft); }
.tr-pill.neutral { background:rgba(255,255,255,.05);border:1px solid var(--tr-border-strong);color:var(--tr-text-2); }

.tr-dot { width:7px; height:7px; border-radius:50%; flex:none; }
.tr-dot.ok   { background:var(--tr-success); box-shadow:0 0 12px var(--tr-success); }
.tr-dot.bad  { background:var(--tr-accent);  box-shadow:0 0 12px var(--tr-accent); }
.tr-dot.grey { background:var(--tr-text-3); }
.tr-dot.live { animation: tr-pulse 1.8s ease-in-out infinite; }

.tr-spin {
  width:15px; height:15px; flex:none; border-radius:50%;
  border:2px solid rgba(62,213,152,.25); border-top-color:var(--tr-success);
  animation: tr-spin 1s linear infinite;
}
.tr-spin.grey { border-color:rgba(255,255,255,.14); border-top-color:var(--tr-text-3); width:12px; height:12px; }

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
@media (max-width:760px) { .tr-step-pip .l { display:none; } .tr-step-pip { min-width:0; } }

.tr-summary {
  display:flex; align-items:center; gap:12px; padding:12px 15px; border-radius:12px;
  background:rgba(62,213,152,.05); border:1px solid rgba(62,213,152,.20); margin-bottom:8px;
}
.tr-summary .n {
  width:22px; height:22px; flex:none; border-radius:50%; display:flex; align-items:center;
  justify-content:center; font-size:12px; background:rgba(62,213,152,.16);
  color:var(--tr-success); border:1px solid rgba(62,213,152,.35);
}
.tr-summary .k { font-family:var(--tr-mono); font-size:10px; letter-spacing:.14em;
  text-transform:uppercase; color:var(--tr-text-4); }
.tr-summary .v { font-size:13.5px; font-weight:600; margin-top:2px; color:var(--tr-text); }

.tr-locked { opacity:.5; padding:22px; border-radius:16px;
  border:1px dashed rgba(255,255,255,.12); background:var(--tr-sunken); }
.tr-step-num.muted { background:rgba(255,255,255,.05); border-color:rgba(255,255,255,.1);
  color:var(--tr-text-4); }
.tr-field-label { font-family:var(--tr-mono); font-size:10px; letter-spacing:.16em;
  text-transform:uppercase; color:var(--tr-text-4); margin-bottom:8px; }
.tr-count { font-family:var(--tr-mono); font-size:10.5px; padding:2px 7px; border-radius:6px;
  background:rgba(255,51,85,.14); color:var(--tr-accent-soft); }

/* ── location tiles ────────────────────────────────────────────────── */
.tr-loc {
  position:relative; padding:20px 18px; border-radius:14px; background:var(--tr-sunken);
  border:1px solid var(--tr-border); text-align:center; transition:all .15s ease;
}
.tr-loc .pin { font-size:18px; color:var(--tr-text-4); }
.tr-loc .n { font-size:17px; font-weight:700; letter-spacing:-.02em; margin-top:8px; }
.tr-loc .s { font-size:12px; color:var(--tr-text-3); margin-top:3px; }
.tr-loc.selected {
  border:1.5px solid var(--tr-accent);
  background:linear-gradient(135deg,rgba(255,51,85,.14),rgba(255,51,85,.03));
  box-shadow:0 18px 44px -20px rgba(255,51,85,.8);
}
.tr-loc.selected .pin { color:var(--tr-accent); }
.tr-loc.disabled { border-style:dashed; opacity:.5; }
.tr-loc.disabled .s { font-family:var(--tr-mono); font-size:10px; letter-spacing:.14em;
  text-transform:uppercase; }
.tr-loc .check {
  position:absolute; top:9px; right:9px; width:22px; height:22px; border-radius:50%;
  background:var(--tr-accent); color:#fff; font-size:12px; display:flex;
  align-items:center; justify-content:center; box-shadow:0 4px 14px rgba(255,51,85,.7);
}

/* ── theatre tiles ─────────────────────────────────────────────────── */
.tr-theatre {
  position:relative; padding:16px; border-radius:13px; background:var(--tr-sunken);
  border:1px solid var(--tr-border); transition:all .15s ease; min-height:104px;
}
.tr-theatre.selected { border:1.5px solid var(--tr-accent); background:rgba(255,51,85,.07); }
.tr-theatre .hd { display:flex; gap:12px; align-items:center; }
.tr-theatre .ab {
  width:38px; height:38px; flex:none; border-radius:10px; background:rgba(255,255,255,.05);
  border:1px solid rgba(255,255,255,.08); display:flex; align-items:center;
  justify-content:center; font-family:var(--tr-mono); font-size:11px; color:#9A9AA4;
}
.tr-theatre .n { font-size:14px; font-weight:600; line-height:1.25; }
.tr-theatre .a { font-size:11.5px; color:var(--tr-text-3); margin-top:2px; }
.tr-theatre .fl { display:flex; gap:6px; flex-wrap:wrap; margin-top:12px; }
.tr-theatre .f {
  font-family:var(--tr-mono); font-size:9.5px; letter-spacing:.1em; text-transform:uppercase;
  padding:3px 8px; border-radius:6px; background:rgba(232,178,92,.10);
  border:1px solid rgba(232,178,92,.26); color:var(--tr-warning); white-space:nowrap;
}
.tr-theatre .f.more, .tr-theatre .f.muted {
  background:rgba(255,255,255,.04); border-color:var(--tr-border); color:var(--tr-text-4);
  text-transform:none; letter-spacing:.04em;
}
.tr-theatre .check {
  position:absolute; top:10px; right:10px; width:20px; height:20px; border-radius:50%;
  background:var(--tr-accent); color:#fff; font-size:11px; display:flex;
  align-items:center; justify-content:center;
}
.tr-fmt-head { margin:14px 0 8px; padding-bottom:8px; border-bottom:1px solid var(--tr-border); }
.tr-fmt-head .n { font-size:13.5px; font-weight:700; text-transform:uppercase; letter-spacing:.02em; }
.tr-fmt-head .a { font-size:11.5px; color:var(--tr-text-3); margin-top:2px; }

/* ── catalogue status banner ───────────────────────────────────────── */
.tr-banner {
  display:flex; gap:13px; align-items:flex-start; padding:14px 16px; border-radius:12px;
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
.tr-banner.ok .g { background:rgba(62,213,152,.14); border-color:rgba(62,213,152,.3);
  color:var(--tr-success); }
.tr-banner.warn { background:rgba(232,178,92,.06); border-color:rgba(232,178,92,.24); }
.tr-banner.warn .g { background:rgba(232,178,92,.14); border-color:rgba(232,178,92,.32);
  color:var(--tr-warning); }
.tr-banner.bad { background:#130C0E; border-color:rgba(255,51,85,.28); }
.tr-banner.bad .g { background:rgba(255,51,85,.12); border-color:rgba(255,51,85,.35);
  color:var(--tr-accent-soft); }

/* ── poster tiles ──────────────────────────────────────────────────── */
.tr-poster {
  position:relative; border-radius:13px; overflow:hidden;
  border:1px solid rgba(255,255,255,.08); background:var(--tr-sunken);
}
.tr-poster.selected {
  border:1.5px solid var(--tr-accent); background:rgba(255,51,85,.06);
  box-shadow:0 20px 50px -18px rgba(255,51,85,.85);
}
.tr-poster .art {
  height:168px; display:flex; align-items:flex-end; justify-content:center; padding-bottom:12px;
  background: repeating-linear-gradient(135deg,#1C1C22 0 6px,#15151A 6px 12px);
  background-size:cover; background-position:center;
}
.tr-poster.selected .art { background-image: repeating-linear-gradient(135deg,#22161A 0 7px,#1A1116 7px 14px); }
.tr-poster .art .cap {
  font-family:var(--tr-mono); font-size:9px; letter-spacing:.14em;
  color:rgba(255,255,255,.42); text-transform:uppercase;
}
.tr-poster .check {
  position:absolute; top:8px; right:8px; width:22px; height:22px; border-radius:50%;
  background:var(--tr-accent); color:#fff; font-size:12px;
  display:flex; align-items:center; justify-content:center;
  box-shadow:0 4px 14px rgba(255,51,85,.7);
}
.tr-poster .body { padding:10px 11px 12px; }
.tr-poster .t { font-size:13px; font-weight:600; line-height:1.3; }
.tr-poster .m { font-size:11px; color:var(--tr-text-3); margin-top:4px; font-family:var(--tr-mono); }

/* ── status cards ──────────────────────────────────────────────────── */
.tr-monitor {
  border-radius:16px; padding:18px; border:1px solid rgba(62,213,152,.22);
  background: linear-gradient(180deg,rgba(62,213,152,.07),rgba(255,255,255,.015));
}
.tr-monitor.stopped { border-color:rgba(255,255,255,.08); background:var(--tr-surface); }
.tr-monitor.expired { border-color:rgba(232,178,92,.30); background:#12100B; }
.tr-monitor .head { display:flex; gap:14px; }
.tr-monitor .art {
  width:62px; height:88px; flex:none; border-radius:9px;
  background: repeating-linear-gradient(135deg,#1C1C22 0 6px,#15151A 6px 12px);
  background-size:cover; background-position:center;
  border:1px solid rgba(255,255,255,.08);
  display:flex; align-items:center; justify-content:center;
}
.tr-monitor .art span { font-family:var(--tr-mono); font-size:8px; color:rgba(255,255,255,.4); letter-spacing:.1em; }
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
.tr-metric.wide { grid-column:span 2; }
.tr-note {
  margin-top:14px; display:flex; align-items:center; gap:11px; padding:12px 14px;
  border-radius:11px; background:rgba(255,255,255,.03); border:1px solid var(--tr-border);
}
.tr-note .t { font-size:13px; font-weight:600; }
.tr-note .s { font-size:11.5px; color:var(--tr-text-3); margin-top:1px; }
.tr-note.bad { background:rgba(255,51,85,.06); border-color:rgba(255,51,85,.22); }
.tr-note.warn { background:rgba(232,178,92,.07); border-color:rgba(232,178,92,.22); }

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
.tr-row .r.ok { color:var(--tr-success); font-weight:700; letter-spacing:.06em; font-size:10.5px; }

.tr-history { display:flex; gap:12px; padding:12px; border-radius:12px;
  background:var(--tr-surface); border:1px solid var(--tr-border); margin-bottom:8px; }
.tr-history .art { width:34px; height:46px; flex:none; border-radius:7px;
  background: repeating-linear-gradient(135deg,#1C1C22 0 6px,#15151A 6px 12px); }
.tr-history .t { font-size:13px; font-weight:600; }
.tr-history .s { font-size:11.5px; color:var(--tr-text-3); margin-top:2px; }
.tr-history .k { font-size:11px; margin-top:5px; }

/* ── the big live card ─────────────────────────────────────────────── */
.tr-live {
  position:relative; overflow:hidden; padding:30px; border-radius:20px;
  border:1.5px solid rgba(62,213,152,.4);
  background: radial-gradient(700px 300px at 10% 0%, rgba(62,213,152,.16), transparent 70%), #0C1210;
  margin-bottom:16px;
}
.tr-live .top { display:flex; justify-content:space-between; align-items:flex-start; gap:24px; flex-wrap:wrap; }
.tr-live h2 { font-size:38px; font-weight:800; letter-spacing:-.03em; margin:16px 0 0; line-height:1.06; }
.tr-live .where { font-size:15px; color:#B8B8C2; margin-top:8px; }
.tr-live .detected { flex:none; text-align:right; }
.tr-live .detected .v { font-size:20px; font-weight:700; margin-top:4px; }
.tr-live .split { margin-top:26px; padding-top:22px; border-top:1px solid rgba(255,255,255,.09);
  display:flex; gap:36px; align-items:flex-end; flex-wrap:wrap; }
.tr-live .date { font-size:19px; font-weight:700; margin-top:6px; }
.tr-chips { display:flex; gap:9px; margin-top:8px; flex-wrap:wrap; }
.tr-chip { padding:9px 15px; border-radius:9px; background:rgba(255,255,255,.05);
  border:1px solid rgba(255,255,255,.12); font-size:14px; font-weight:600; white-space:nowrap; }
.tr-live .foot { margin-top:20px; padding-top:18px; border-top:1px solid var(--tr-border);
  display:flex; gap:28px; flex-wrap:wrap; font-family:var(--tr-mono); font-size:11px;
  letter-spacing:.1em; text-transform:uppercase; color:var(--tr-text-4); }
@media (max-width:900px) { .tr-live { padding:22px 18px; } .tr-live h2 { font-size:26px; } }

.tr-book {
  display:block; padding:18px; border-radius:13px; text-align:center; text-decoration:none;
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
.tr-state.expired .icon { background:rgba(232,178,92,.12); border:1px solid rgba(232,178,92,.35); color:var(--tr-warning); }
.tr-state.expired .p { color:#B5A588; }
.tr-state.expired .box { background:rgba(232,178,92,.07); border:1px solid rgba(232,178,92,.2); color:#D8C49E; }
.tr-state.error { background:#130C0E; border:1px solid rgba(255,51,85,.28); }
.tr-state.error .icon { background:rgba(255,51,85,.12); border:1px solid rgba(255,51,85,.35); color:var(--tr-accent-soft); }
.tr-state.error .p { color:#C0A3A9; }
.tr-state.error .box { background:rgba(255,51,85,.06); border:1px solid rgba(255,51,85,.18); color:#D9BDC3; }
.tr-state.empty {
  background:var(--tr-sunken); border:1px dashed rgba(255,255,255,.14);
  align-items:center; text-align:center; justify-content:center; padding:34px 26px;
}
.tr-state.empty .ring {
  width:52px; height:52px; border-radius:50%; border:1px solid rgba(255,255,255,.12);
  display:flex; align-items:center; justify-content:center; font-size:17px; color:var(--tr-text-4);
}
.tr-state.empty .p { color:var(--tr-text-3); max-width:320px; }

/* ── Streamlit widgets ─────────────────────────────────────────────── */
.stTextInput input, .stDateInput input, .stTimeInput input, .stTextArea textarea,
[data-baseweb="input"], [data-baseweb="select"] > div {
  background: var(--tr-sunken) !important; color: var(--tr-text) !important;
  border:1px solid rgba(255,255,255,.09) !important; border-radius:11px !important;
  font-family: var(--tr-sans) !important; font-size:14px !important;
}
.stTextInput input:focus, [data-baseweb="input"]:focus-within {
  border-color: rgba(255,51,85,.55) !important;
}
.stTextInput input::placeholder { color: var(--tr-text-4) !important; }
[data-testid="stWidgetLabel"] p { font-size:12.5px !important; color:var(--tr-text-3) !important; }

.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
  background: transparent; color: var(--tr-text-2);
  border:1px solid var(--tr-border-strong); border-radius:11px;
  font-family: var(--tr-sans); font-weight:600; font-size:13px;
  padding:.55rem 1rem; transition: all .15s ease; width:100%;
}
.stButton > button:hover, .stFormSubmitButton > button:hover {
  border-color: rgba(255,51,85,.5); color: var(--tr-text); background: rgba(255,51,85,.06);
}
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {
  background: linear-gradient(135deg,#FF3355,#D4123F); color:#fff; border:none;
  font-size:15px; font-weight:700; letter-spacing:.04em; text-transform:uppercase;
  padding:.95rem 1rem; border-radius:14px;
  box-shadow:0 20px 50px -18px rgba(255,51,85,.85);
}
.stButton > button[kind="primary"]:hover { filter:brightness(1.08); transform:translateY(-1px); }
/* Destructive: tinted, not solid — visible without shouting (DESIGN-SPEC §5). */
.tr-danger .stButton > button {
  background: rgba(255,51,85,.12); border:1px solid rgba(255,51,85,.42);
  color: var(--tr-accent-soft); font-weight:700; letter-spacing:.06em;
  text-transform:uppercase; padding:.8rem 1rem; border-radius:11px;
}
.tr-danger .stButton > button:hover { background: rgba(255,51,85,.2); color:#fff; }
.tr-amber .stButton > button {
  background: rgba(232,178,92,.14); border:1px solid rgba(232,178,92,.4);
  color: var(--tr-warning); font-weight:700; letter-spacing:.05em; text-transform:uppercase;
}
.tr-amber .stButton > button:hover { background: rgba(232,178,92,.22); }

/* Checkbox rows (theatres) */
.stCheckbox label { min-height:44px; display:flex; align-items:center; font-size:14px; }
.stCheckbox [data-baseweb="checkbox"] div[data-testid="stMarkdownContainer"] p { font-size:14px; font-weight:600; }
.stCheckbox span[aria-hidden="true"], .stCheckbox [data-baseweb="checkbox"] > span {
  border-radius:6px !important; border:1.5px solid rgba(255,255,255,.2) !important;
  background: transparent !important; width:20px !important; height:20px !important;
}
.stCheckbox [data-baseweb="checkbox"] input:checked + span,
.stCheckbox [data-baseweb="checkbox"] span[data-checked="true"] {
  background: var(--tr-accent) !important; border-color: var(--tr-accent) !important;
}

/* Radio rows (formats, frequency) */
[role="radiogroup"] { gap:7px; }
.stRadio [role="radiogroup"] label {
  padding:11px 13px; border-radius:10px; border:1px solid rgba(255,255,255,.09);
  background: var(--tr-sunken); font-size:13.5px; color:#C9C9D2; min-height:44px;
}
.stRadio [role="radiogroup"] label:has(input:checked) {
  background: rgba(255,51,85,.10); border-color: rgba(255,51,85,.40); color:#fff; font-weight:600;
}
.stRadio [role="radiogroup"] label div[data-baseweb="radio"] div:first-child {
  border-color: rgba(255,255,255,.22) !important;
}

/* Segmented control (frequency). Targeted by button kind, which is the part
   of this widget's markup that has stayed put. */
button[kind="segmented_control"], button[kind="segmented_controlActive"] {
  background: var(--tr-sunken) !important; border:1px solid rgba(255,255,255,.09) !important;
  border-radius:12px !important; color: var(--tr-text-2) !important;
  font-family: var(--tr-sans) !important; font-weight:700 !important; font-size:15px !important;
  padding:.8rem .6rem !important; white-space:pre-line; line-height:1.25;
}
button[kind="segmented_controlActive"] {
  background: rgba(255,51,85,.10) !important; border-color: var(--tr-accent) !important;
  color:#fff !important;
}

/* Toggle */
[data-testid="stCheckbox"] [role="checkbox"][aria-checked="true"],
.stToggle [data-baseweb="checkbox"] div[aria-checked="true"] { background: var(--tr-accent) !important; }

/* Expander */
[data-testid="stExpander"] details {
  background: var(--tr-surface); border:1px solid var(--tr-border) !important;
  border-radius:14px !important;
}
[data-testid="stExpander"] summary { font-size:13.5px; font-weight:600; color:var(--tr-text-2); }

/* Alerts — the palette's meanings, not Streamlit's */
[data-testid="stAlert"] { border-radius:12px; border:1px solid var(--tr-border); font-size:13.5px; }
[data-testid="stAlertContainer"] { background: var(--tr-sunken) !important; color: var(--tr-text-2) !important; }

hr { border-color: var(--tr-border) !important; }
[data-testid="stTooltipHoverTarget"] { color: var(--tr-text-3); }
</style>
"""


def inject() -> None:
    """Apply the theme once per rerun."""
    st.markdown(CSS, unsafe_allow_html=True)


__all__ = ["CSS", "TOKENS", "inject"]
