"""The entrance: sign in, create an account, reset a password.

Built from the approved login design (the attached reference in step 6): a
cinematic left zone — radar sweep, the headline, four promises, a wall of
what's showing, Charminar and a house of seats — and a glass panel on the
right that holds the form. Everything on screen is the same HTML-in-markdown
plus keyed-container technique the rest of the UI uses, so the page is still
plain Streamlit: the inputs, buttons and reruns are Streamlit's, which is
what lets ``AppTest`` drive it.

Three states share the panel and the visual:

    signin  →  "Welcome back."           email · password · SIGN IN
    signup  →  "Create your account."    name · email · password ×2 · CREATE ACCOUNT
    verify  →  "You're almost in."       resend the verification email · back to sign in
    reset   →  "Reset your password."    email · SEND RESET LINK  →  sent

A new account lands on *verify*, not in the app: Firebase has emailed its
verification link, and until the account record says ``emailVerified`` the
session never gets an ``AuthUser``. Signing in with an unverified account
lands there too.

Firebase is only ever spoken to from ``auth.firebase``; this module renders,
validates what can be validated before a network call, and shows the one
sentence ``AuthError`` carries. A raw Firebase message never reaches the page.

On a phone the visual steps aside: the brand and the headline sit above the
panel, the panel is the page, and the poster wall, skyline and seats are
gone rather than squeezed.
"""

from __future__ import annotations

import math
import time
from html import escape
from typing import Callable

import streamlit as st

from auth import firebase, google, session
from auth.firebase import AuthError
from ui import catalogue_view as cv
from ui import components as C

MODE_KEY = "auth_mode"
ERROR_KEY = "auth_error"
NOTICE_KEY = "auth_notice"
#: Resending the verification email: at most this many per pending account,
#: and never two inside the cooldown.
RESEND_LIMIT = 3
RESEND_COOLDOWN = 60
#: While an account waits for its link, the tab asks Firebase whether the
#: address has been verified this often — and continues by itself when it has.
VERIFY_POLL = 10
VERIFIED_KEY = "auth_verified_banner"

#: The four promises under the headline, in the design's order.
PROMISES = [
    ("bolt", "Real-time", "Monitoring"),
    ("bell", "Instant", "Email Alerts"),
    ("clapper", "All Premium", "Theatres"),
    ("people", "For True", "Movie Lovers"),
]

#: Icons the login page adds to the UI's one line-icon family.
LOGIN_ICONS = {
    "bolt": C.ICON_PATHS["bolt"],
    "bell": "<path d='M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 2h-15z'/><path d='M10 20.5a2 2 0 0 0 4 0'/>",
    "clapper": "<path d='M3.5 9.5h17v9a1.5 1.5 0 0 1-1.5 1.5H5a1.5 1.5 0 0 1-1.5-1.5z'/><path d='M3.8 9.5 5 5l15.2 2.6-.7 1.9'/><path d='M8 6l2 3.5M12 6.7l2 3.5M16 7.4l2 3.5'/>",
    "people": "<circle cx='9' cy='9' r='3'/><path d='M3.5 19a5.5 5.5 0 0 1 11 0'/><circle cx='16.5' cy='10' r='2.4'/><path d='M15 15.2a4.5 4.5 0 0 1 5.5 3.8'/>",
    "lock": "<rect x='5' y='10.5' width='14' height='10' rx='2'/><path d='M8 10.5V8a4 4 0 0 1 8 0v2.5'/><path d='M12 14.5v2.5'/>",
    "user": "<circle cx='12' cy='8.5' r='3.5'/><path d='M5 20a7 7 0 0 1 14 0'/>",
    "shield": "<path d='M12 3.5 5 6v5.5c0 4.2 3 7.6 7 9 4-1.4 7-4.8 7-9V6z'/><path d='M9.2 12l2 2 3.8-4'/>",
    "heart": "<path d='M12 20s-7-4.6-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.4-7 10-7 10z'/>",
    "signal": "<path d='M5 19v-4M9 19V9M13 19v-7M17 19V5'/>",
    "arrow": "<path d='M5 12h14M13 6l6 6-6 6'/>",
    "pin": C.ICON_PATHS["location"],
}

GOOGLE_G = (
    "<svg viewBox='0 0 24 24' width='20' height='20' aria-hidden='true'>"
    "<path fill='%234285F4' d='M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.4h6.5c-.3 1.5-1.1 2.8-2.4 3.6v3h3.9c2.3-2.1 3.5-5.2 3.5-8.7z'/>"
    "<path fill='%2334A853' d='M12 24c3.2 0 6-1.1 8-2.9l-3.9-3c-1.1.7-2.5 1.2-4.1 1.2-3.1 0-5.8-2.1-6.7-5H1.3v3.1C3.3 21.3 7.3 24 12 24z'/>"
    "<path fill='%23FBBC05' d='M5.3 14.3c-.5-1.5-.5-3.1 0-4.6V6.6H1.3c-1.7 3.4-1.7 7.4 0 10.8l4-3.1z'/>"
    "<path fill='%23EA4335' d='M12 4.7c1.8 0 3.3.6 4.6 1.8l3.4-3.4C17.9 1.2 15.2 0 12 0 7.3 0 3.3 2.7 1.3 6.6l4 3.1c.9-2.9 3.6-5 6.7-5z'/></svg>"
)


def _icon(name: str, size: int = 18, color: str = "currentColor", width: str = "1.8") -> str:
    paths = LOGIN_ICONS.get(name) or C.ICON_PATHS.get(name, C.ICON_PATHS["radar"])
    return (f'<svg class="tr-ic" viewBox="0 0 24 24" width="{size}" height="{size}" fill="none" '
            f'stroke="{color}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round" '
            f'aria-hidden="true">{paths}</svg>')


def _input_icon_uri(name: str, color: str = "#8E8E98") -> str:
    from urllib.parse import quote

    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='{color}' "
           f"stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round'>{LOGIN_ICONS.get(name) or C.ICON_PATHS[name]}</svg>")
    return f"url(\"data:image/svg+xml;utf8,{quote(svg, safe='')}\")"


# ──────────────────────────────────────────────────────────────────────────
# Styles — only on this page; the app's own theme is untouched
# ──────────────────────────────────────────────────────────────────────────
CSS = """
<style>
/* the room: a near-black house with the projector's glow low on the left */
html, body, [data-testid="stAppViewContainer"], .stApp {
  background:
    radial-gradient(900px 620px at 8% 92%, rgba(255,51,85,.24), transparent 62%),
    radial-gradient(700px 480px at 92% 8%, rgba(255,51,85,.10), transparent 60%),
    radial-gradient(1400px 800px at 50% 50%, #101016, #07070A 75%) !important;
  background-attachment: fixed !important;
}
.block-container { padding: 1rem 2.4rem 1.6rem !important; max-width: 1600px !important; }
[data-testid="stMainBlockContainer"] { padding-top: .6rem !important; }
[data-testid="stHeader"] { display: none !important; }
[class*="st-key-trauth_shell"] { position: relative; }

/* ── top bar ───────────────────────────────────────────────────────── */
.tr-auth-top { display:flex; align-items:center; justify-content:space-between; gap:20px; padding:4px 2px 14px; animation: tr-auth-fade .7s var(--tr-ease) both; }
.tr-auth-lockup { display:flex; align-items:center; gap:13px; }
.tr-auth-lockup .mark { width:46px; height:46px; border-radius:50%; display:flex; align-items:center; justify-content:center; flex:none;
  background: radial-gradient(circle at 40% 35%, rgba(255,120,140,.35), rgba(255,51,85,.08) 60%, transparent 70%);
  border:1px solid rgba(255,51,85,.55); box-shadow: 0 0 0 5px rgba(255,51,85,.06), 0 0 30px -6px rgba(255,51,85,.7); }
.tr-auth-lockup .mark svg { width:22px; height:22px; }
.tr-auth-lockup .name { font-size:24px; font-weight:800; letter-spacing:-.035em; line-height:1; color:#fff; }
.tr-auth-lockup .name em { font-style:normal; color:var(--tr-accent); }
.tr-auth-lockup .sub { font-family:var(--tr-mono); font-size:9.5px; letter-spacing:.32em; color:var(--tr-text-3); margin-top:6px; }
.tr-auth-nav { display:flex; align-items:center; gap:22px; font-size:13px; font-weight:600; color:var(--tr-text-2); }
.tr-auth-nav .city { display:inline-flex; align-items:center; gap:5px; color:#fff; }
.tr-auth-nav .tag { font-size:11px; color:var(--tr-text-4); font-weight:500; }

/* ── the cinematic zone ────────────────────────────────────────────── */
.tr-auth-visual { position:relative; min-height:760px; overflow:hidden; isolation:isolate; padding:292px 0 150px 12px; animation: tr-auth-fade 1s var(--tr-ease) both; }
.tr-auth-visual .glow { position:absolute; inset:auto -10% -20% -20%; height:60%; z-index:0; pointer-events:none;
  background: radial-gradient(closest-side, rgba(255,51,85,.28), rgba(255,51,85,.06) 55%, transparent 75%);
  filter: blur(10px); animation: tr-auth-breathe 7s ease-in-out infinite; }

/* radar */
.tr-radar { position:absolute; left:12px; top:0; width:300px; height:300px; z-index:1; }
.tr-radar .ring { position:absolute; border-radius:50%; border:1px solid rgba(255,51,85,.34); left:50%; top:50%; transform:translate(-50%,-50%); }
.tr-radar .ring.r1 { width:60px; height:60px; border-color:rgba(255,51,85,.6); }
.tr-radar .ring.r2 { width:130px; height:130px; }
.tr-radar .ring.r3 { width:200px; height:200px; border-color:rgba(255,51,85,.26); }
.tr-radar .ring.r4 { width:270px; height:270px; border-color:rgba(255,51,85,.16); }
.tr-radar .axis { position:absolute; left:50%; top:50%; width:270px; height:1px; background:rgba(255,51,85,.16); transform:translate(-50%,-50%); }
.tr-radar .axis.v { transform:translate(-50%,-50%) rotate(90deg); }
.tr-radar .sweep { position:absolute; inset:15px; border-radius:50%; overflow:hidden; transform-origin:50% 50%;
  background: conic-gradient(from 0deg, rgba(255,51,85,0) 0deg, rgba(255,51,85,0) 250deg, rgba(255,51,85,.06) 300deg, rgba(255,51,85,.42) 359deg, rgba(255,51,85,0) 360deg);
  animation: tr-radar-sweep 6.5s linear infinite; }
.tr-radar .sweep::after { content:""; position:absolute; left:50%; top:50%; width:135px; height:2px; transform-origin:0 50%; transform:rotate(-90deg);
  background: linear-gradient(90deg, #FF6B85, rgba(255,51,85,.15)); box-shadow: 0 0 12px rgba(255,51,85,.9); }
.tr-radar .core { position:absolute; left:50%; top:50%; width:10px; height:10px; border-radius:50%; background:#FF3355; transform:translate(-50%,-50%);
  box-shadow: 0 0 0 4px rgba(255,51,85,.25), 0 0 22px 4px rgba(255,51,85,.7); }
.tr-radar .blip { position:absolute; width:8px; height:8px; border-radius:50%; background:#FF5573; box-shadow:0 0 14px 3px rgba(255,51,85,.8); animation: tr-blip 2.8s ease-out infinite; }
.tr-radar .blip.b1 { left:214px; top:66px; }
.tr-radar .blip.b2 { left:190px; top:190px; animation-delay:1.3s; }
.tr-radar .blip.b3 { left:104px; top:118px; width:5px; height:5px; animation-delay:.6s; }
.tr-radar .tag { position:absolute; left:262px; top:152px; padding:8px 12px; border:1px solid rgba(255,51,85,.7); border-radius:8px; white-space:nowrap;
  font-family:var(--tr-mono); font-size:11px; letter-spacing:.2em; color:#FF6B85; line-height:1.45; background:rgba(12,8,10,.72);
  box-shadow: 0 0 0 1px rgba(255,51,85,.12), 0 12px 30px -14px rgba(255,51,85,.8); animation: tr-auth-rise .8s .5s var(--tr-ease) both; }

/* headline & promises */
.tr-auth-copy { position:relative; z-index:2; max-width:600px; animation: tr-auth-rise .9s .15s var(--tr-ease) both; }
.tr-auth-copy h1 { font-size:58px; line-height:1.02; letter-spacing:-.04em; font-weight:800; color:#fff; margin:0; }
.tr-auth-copy h1 em { font-style:normal; color:var(--tr-accent); }
.tr-auth-copy .lede { font-size:19px; line-height:1.55; color:var(--tr-text-2); margin:22px 0 0; max-width:600px; font-weight:500; }
.tr-auth-feats { display:flex; gap:26px; margin-top:30px; }
.tr-auth-feat { display:flex; flex-direction:column; align-items:center; gap:10px; width:96px; text-align:center; }
.tr-auth-feat .ic { width:54px; height:54px; border-radius:50%; display:flex; align-items:center; justify-content:center;
  border:1px solid rgba(255,51,85,.45); background: radial-gradient(circle at 50% 40%, rgba(255,51,85,.22), rgba(255,51,85,.04) 70%);
  box-shadow: 0 0 0 5px rgba(255,51,85,.05), 0 10px 26px -14px rgba(255,51,85,.9); color:#FF5573; transition: transform .25s var(--tr-ease), box-shadow .25s var(--tr-ease); }
.tr-auth-feat:hover .ic { transform: translateY(-3px); box-shadow: 0 0 0 6px rgba(255,51,85,.08), 0 16px 30px -14px rgba(255,51,85,1); }
.tr-auth-feat .t { font-size:13px; line-height:1.35; color:var(--tr-text); font-weight:600; }
.tr-auth-city { position:absolute; right:60px; bottom:268px; z-index:2; font-family:var(--tr-mono); font-size:11.5px; letter-spacing:.34em; line-height:1.9; color:var(--tr-text-2); text-align:center; }
.tr-auth-mark { position:absolute; left:0; right:0; bottom:24px; z-index:3; font-family:var(--tr-mono); font-size:10.5px; letter-spacing:.34em; line-height:1.9; color:var(--tr-text-2); text-align:center; text-shadow:0 2px 14px #000, 0 0 6px #000; }

/* the wall of what's showing */
.tr-auth-wall { position:absolute; right:14px; top:0; z-index:1; width:360px; perspective:900px; }
.tr-auth-wall .grid { display:grid; grid-template-columns:repeat(3, 104px); gap:14px; justify-content:end; transform: rotateY(-22deg) rotateX(5deg); transform-origin:100% 40%; }
.tr-auth-wall .card { position:relative; height:154px; border-radius:10px; overflow:hidden; border:1px solid rgba(255,255,255,.14);
  background: linear-gradient(160deg,#2A1218,#120B10 60%,#1A0D12); box-shadow: 0 26px 50px -22px rgba(0,0,0,1), 0 0 0 1px rgba(255,51,85,.06);
  animation: tr-auth-rise .8s var(--tr-ease) both; }
.tr-auth-wall .card:nth-child(2) { animation-delay:.08s; } .tr-auth-wall .card:nth-child(3) { animation-delay:.16s; }
.tr-auth-wall .card:nth-child(4) { animation-delay:.24s; } .tr-auth-wall .card:nth-child(5) { animation-delay:.32s; } .tr-auth-wall .card:nth-child(6) { animation-delay:.4s; }
.tr-auth-wall .card img { position:absolute; inset:0; width:100%; height:100%; object-fit:cover; display:block; filter:saturate(1.05) contrast(1.02); }
.tr-auth-wall .card::after { content:""; position:absolute; inset:0; background: linear-gradient(180deg, rgba(0,0,0,0) 55%, rgba(6,6,9,.85)); pointer-events:none; }
.tr-auth-wall .card .t { position:absolute; left:10px; right:10px; bottom:9px; z-index:1; font-family:var(--tr-mono); font-size:9.5px; letter-spacing:.14em; text-transform:uppercase; color:rgba(255,255,255,.85);
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.tr-auth-wall .card.ph .t { color:rgba(255,255,255,.4); }
.tr-auth-wall .card.ph::before { content:""; position:absolute; inset:16px; border:1px dashed rgba(255,255,255,.12); border-radius:8px; }

/* skyline and the house */
.tr-auth-skyline { position:absolute; right:30px; bottom:120px; z-index:1; width:250px; height:135px; color:rgba(255,255,255,.07); }
.tr-auth-skyline svg { width:100%; height:100%; display:block; filter: drop-shadow(0 0 22px rgba(255,51,85,.25)); }
.tr-auth-seats { position:absolute; left:-6%; right:-6%; bottom:0; z-index:2; display:flex; flex-direction:column; gap:10px; pointer-events:none; }
.tr-auth-seats .row { display:flex; justify-content:center; gap:12px; }
.tr-auth-seats .row.back { transform:scale(.86); opacity:.55; }
.tr-auth-seats i { width:68px; height:44px; border-radius:16px 16px 6px 6px; flex:none;
  background: linear-gradient(180deg,#4A0F1E 0%,#2B0912 55%,#150409 100%); border-top:1px solid rgba(255,90,120,.35); box-shadow: inset 0 -12px 18px -12px #000, 0 -8px 26px -18px rgba(255,51,85,.8); }
.tr-auth-seats .row.front i { width:84px; height:58px; }
.tr-auth-visual .floor { position:absolute; left:0; right:0; bottom:0; height:200px; z-index:1; pointer-events:none; background: linear-gradient(180deg, transparent, rgba(7,7,10,.92) 70%); }

/* ── the panel ─────────────────────────────────────────────────────── */
[class*="st-key-trauth_panel"] {
  position:relative; max-width:560px; margin-left:auto; padding:34px 34px 30px !important; border-radius:24px;
  background: linear-gradient(165deg, rgba(28,20,24,.82), rgba(14,12,16,.9) 60%, rgba(18,12,16,.88));
  border:1px solid rgba(255,255,255,.12); backdrop-filter: blur(22px); -webkit-backdrop-filter: blur(22px);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.10), 0 40px 90px -40px rgba(0,0,0,1), 0 0 0 1px rgba(255,51,85,.06), 0 30px 80px -50px rgba(255,51,85,.6);
  animation: tr-auth-rise .9s .1s var(--tr-ease) both; gap:.55rem;
}
[class*="st-key-trauth_panel"]::before { content:""; position:absolute; inset:0; border-radius:24px; pointer-events:none;
  background: radial-gradient(500px 220px at 85% 0%, rgba(255,51,85,.12), transparent 60%); }
.tr-auth-brand { display:flex; align-items:center; gap:18px; justify-content:center; padding:2px 0 22px; }
.tr-auth-brand .lock { display:flex; flex-direction:column; align-items:center; gap:10px; }
.tr-auth-brand .lock svg { width:44px; height:44px; filter: drop-shadow(0 0 14px rgba(255,51,85,.7)); }
.tr-auth-brand .lock .n { font-size:21px; font-weight:800; letter-spacing:.06em; color:#fff; }
.tr-auth-brand .lock .n em { font-style:normal; color:var(--tr-accent); }
.tr-auth-brand .sep { width:1px; height:64px; background:rgba(255,255,255,.14); }
.tr-auth-brand .tag { font-family:var(--tr-mono); font-size:10.5px; letter-spacing:.34em; line-height:1.75; color:var(--tr-text-3); }
.tr-auth-h { font-size:34px; font-weight:800; letter-spacing:-.035em; line-height:1.1; color:#fff; margin:0; }
.tr-auth-p { font-size:15.5px; line-height:1.55; color:var(--tr-text-2); margin:10px 0 18px; font-weight:500; }
.tr-auth-label { font-family:var(--tr-mono); font-size:10px; letter-spacing:.18em; text-transform:uppercase; color:var(--tr-text-3); margin:6px 0 -4px 2px; }

/* inputs: a glyph on the left, the same glass as the app */
[class*="st-key-trauth_panel"] .stTextInput [data-baseweb="input"],
[class*="st-key-trauth_panel"] .stTextInput [data-baseweb="base-input"] { min-height:56px; border-radius:14px !important; position:relative;
  background: linear-gradient(180deg, rgba(255,255,255,.06), rgba(255,255,255,.03)) !important; border-color: rgba(255,255,255,.13) !important; }
[class*="st-key-trauth_panel"] .stTextInput [data-baseweb="input"]::before { content:""; position:absolute; left:17px; top:50%; width:20px; height:20px; transform:translateY(-50%);
  background-repeat:no-repeat; background-size:20px 20px; opacity:.85; pointer-events:none; transition: opacity var(--tr-fast) var(--tr-ease); }
[class*="st-key-trauth_panel"] .stTextInput [data-baseweb="input"] input { padding-left:50px !important; padding-right:14px !important; font-size:15px !important; }
[class*="st-key-trauth_panel"] .stTextInput [data-baseweb="input"]:focus-within::before { opacity:1; }
[class*="st-key-auth_email"] [data-baseweb="input"]::before, [class*="st-key-auth_su_email"] [data-baseweb="input"]::before, [class*="st-key-auth_reset_email"] [data-baseweb="input"]::before { background-image: __MAIL__; }
[class*="st-key-auth_password"] [data-baseweb="input"]::before, [class*="st-key-auth_su_password"] [data-baseweb="input"]::before, [class*="st-key-auth_su_confirm"] [data-baseweb="input"]::before { background-image: __LOCK__; }
[class*="st-key-auth_su_name"] [data-baseweb="input"]::before { background-image: __USER__; }
[class*="st-key-trauth_panel"] .stTextInput [data-baseweb="input"] > div:last-child { padding-right:8px; }
[class*="st-key-trauth_panel"] .stTextInput [data-baseweb="input"] button { transition: color var(--tr-fast) var(--tr-ease), transform var(--tr-fast) var(--tr-ease); color:var(--tr-text-3); }
[class*="st-key-trauth_panel"] .stTextInput [data-baseweb="input"] button:hover { color:#fff; transform:scale(1.08); }
[class*="st-key-trauth_panel"] [data-testid="stForm"] { padding:0; border:none; }
[class*="st-key-trauth_panel"] [data-testid="stForm"] [data-testid="stVerticalBlock"] { gap:.55rem; }

/* the CTA and the Google button */
[class*="st-key-trauth_panel"] .stFormSubmitButton > button, [class*="st-key-trauth_panel"] .stFormSubmitButton > button[kind*="primary"] {
  background: linear-gradient(180deg,#FF6A85 0%,#FF3355 48%,#D4123F 100%); color:#fff; border:1px solid rgba(255,140,160,.45);
  font-size:15px; font-weight:800; letter-spacing:.14em; text-transform:uppercase; min-height:60px; border-radius:16px; margin-top:8px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.4), inset 0 -1px 0 rgba(0,0,0,.25), 0 22px 46px -16px rgba(255,51,85,.95), 0 0 0 4px rgba(255,51,85,.08);
  flex-direction: row-reverse; gap:12px; transition: all .22s var(--tr-ease); }
[class*="st-key-trauth_panel"] .stFormSubmitButton > button:hover { transform:translateY(-2px); background: linear-gradient(180deg,#FF7A93 0%,#FF3F5F 48%,#DC1746 100%); color:#fff;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.5), inset 0 -1px 0 rgba(0,0,0,.25), 0 28px 56px -16px rgba(255,51,85,1), 0 0 0 5px rgba(255,51,85,.10); }
[class*="st-key-trauth_panel"] .stFormSubmitButton > button:active { transform:translateY(0) scale(.995); }
[class*="st-key-trauth_panel"] .stFormSubmitButton > button p { font-size:15px; font-weight:800; letter-spacing:.14em; }
[class*="st-key-trauth_panel"] .stFormSubmitButton > button [data-testid="stIconMaterial"] { font-size:22px; }
[class*="st-key-auth_google"] .stButton > button, a.tr-auth-google { min-height:58px; border-radius:16px; font-size:15.5px; font-weight:700; letter-spacing:0; text-transform:none; gap:12px;
  background: linear-gradient(180deg, rgba(255,255,255,.07), rgba(255,255,255,.03)); border:1px solid rgba(255,255,255,.14); color:#fff; }
[class*="st-key-auth_google"] .stButton > button::before, a.tr-auth-google::before { content:""; width:20px; height:20px; flex:none; background: url("data:image/svg+xml;utf8,__GOOGLE__") no-repeat center / 20px 20px; }
[class*="st-key-auth_google"] .stButton > button:hover, a.tr-auth-google:hover { border-color: rgba(255,255,255,.3); background: linear-gradient(180deg, rgba(255,255,255,.11), rgba(255,255,255,.05)); }
[class*="st-key-auth_google"] .stButton > button p { font-size:15.5px; font-weight:700; }
/* The live Google control is a real link — a top-level navigation to Google —
   drawn as the very same button: same box, same face, same hover. */
a.tr-auth-google { display:flex; align-items:center; justify-content:center; width:100%; box-sizing:border-box; padding:0 16px;
  font-family: inherit; line-height:1.2; text-decoration:none !important; cursor:pointer; user-select:none;
  transition: border-color .18s var(--tr-ease), background .18s var(--tr-ease), transform .18s var(--tr-ease); }
a.tr-auth-google:active { transform: scale(.995); }
a.tr-auth-google:focus-visible { outline: 2px solid rgba(255,255,255,.35); outline-offset: 2px; }
.tr-auth-or { display:flex; align-items:center; gap:14px; margin:10px 0 6px; font-family:var(--tr-mono); font-size:11px; letter-spacing:.24em; color:var(--tr-text-4); }
.tr-auth-or::before, .tr-auth-or::after { content:""; flex:1; height:1px; background:rgba(255,255,255,.1); }

/* text links, as buttons the tests can press */
[class*="st-key-auth_forgot"] .stButton, [class*="st-key-auth_to_signup"] .stButton, [class*="st-key-auth_to_signin"] .stButton, [class*="st-key-auth_back"] .stButton { width:auto; display:inline-block; }
[class*="st-key-auth_forgot"] .stButton > button, [class*="st-key-auth_to_signup"] .stButton > button, [class*="st-key-auth_to_signin"] .stButton > button, [class*="st-key-auth_back"] .stButton > button {
  background:none !important; border:none !important; box-shadow:none !important; padding:2px 2px; min-height:0; width:auto; transform:none !important;
  color:var(--tr-accent-soft); font-size:14px; font-weight:600; letter-spacing:0; text-transform:none; border-radius:6px; }
[class*="st-key-auth_forgot"] .stButton > button:hover, [class*="st-key-auth_to_signup"] .stButton > button:hover, [class*="st-key-auth_to_signin"] .stButton > button:hover, [class*="st-key-auth_back"] .stButton > button:hover { color:#fff; text-decoration:underline; text-underline-offset:3px; }
[class*="st-key-auth_forgot"] .stButton > button p, [class*="st-key-auth_to_signup"] .stButton > button p, [class*="st-key-auth_to_signin"] .stButton > button p, [class*="st-key-auth_back"] .stButton > button p { font-size:14px; font-weight:600; }
[class*="st-key-auth_forgot"] { display:flex; justify-content:flex-end; margin-top:-2px; }
[class*="st-key-trpair_auth_foot"] { margin-top:8px; }
[class*="st-key-trpair_auth_foot"] [data-testid="stHorizontalBlock"] { justify-content:center; gap:6px !important; align-items:center; }
[class*="st-key-trpair_auth_foot"] [data-testid="stColumn"] { flex:0 0 auto !important; width:auto !important; min-width:0 !important; }
.tr-auth-foot-text { font-size:14px; color:var(--tr-text-2); font-weight:500; text-align:right; white-space:nowrap; line-height:34px; }
.tr-auth-hint { font-size:12.5px; color:var(--tr-text-3); margin:-2px 0 0 2px; line-height:1.45; }
.tr-auth-count { font-family:var(--tr-mono); font-size:11px; letter-spacing:.2em; color:var(--tr-text-3); margin-top:2px; font-variant-numeric:tabular-nums; }
.tr-auth-sent .eyebrow { font-family:var(--tr-mono); font-size:10.5px; letter-spacing:.3em; color:#FF6B85; margin-bottom:12px; }
.tr-auth-sent .eyebrow.ok { color:#3ED598; }
.tr-auth-sent .ic.wait { color:#FF6B85; background: radial-gradient(circle at 50% 40%, rgba(255,51,85,.22), rgba(255,51,85,.04) 70%); border-color:rgba(255,51,85,.45);
  box-shadow: 0 0 0 8px rgba(255,51,85,.05), 0 14px 40px -18px rgba(255,51,85,.9); }
.tr-auth-sent .folders { display:inline-flex; align-items:center; gap:8px; margin:14px auto 0; padding:8px 12px; border-radius:10px; font-size:12.5px; color:var(--tr-text-2);
  background:rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.08); }
.tr-auth-sent .folders svg { color:#FF6B85; flex:none; }
[class*="st-key-auth_resend"] .stButton > button:disabled { opacity:.5; transform:none; box-shadow:none; cursor:default; background:linear-gradient(180deg, rgba(255,255,255,.07), rgba(255,255,255,.03)); border-color:rgba(255,255,255,.12); color:var(--tr-text-3); }
[class*="st-key-auth_continue"] .stButton > button { min-height:50px; border-radius:16px; font-size:13px; font-weight:700; letter-spacing:.1em; text-transform:uppercase;
  background: linear-gradient(180deg, rgba(62,213,152,.16), rgba(62,213,152,.06)); border:1px solid rgba(62,213,152,.4); color:#9FEBC9; }
[class*="st-key-auth_continue"] .stButton > button:hover { color:#fff; border-color:rgba(62,213,152,.7); background: linear-gradient(180deg, rgba(62,213,152,.22), rgba(62,213,152,.1)); }
[class*="st-key-auth_continue"] .stButton > button p { font-size:13px; font-weight:700; letter-spacing:.1em; }
.tr-auth-diag { font-family:var(--tr-mono); font-size:9.5px; letter-spacing:.14em; color:var(--tr-text-4); text-align:center; margin-top:10px; line-height:1.6; overflow-wrap:anywhere; }

/* feedback */
.tr-auth-alert { display:flex; gap:10px; align-items:flex-start; padding:12px 14px; border-radius:12px; font-size:13.5px; line-height:1.45; font-weight:500; margin:4px 0 2px;
  animation: tr-auth-rise .35s var(--tr-ease) both; }
.tr-auth-alert.error { background: rgba(255,92,92,.10); border:1px solid rgba(255,92,92,.35); color:#FFB3B3; }
.tr-auth-alert.info { background: rgba(232,178,92,.08); border:1px solid rgba(232,178,92,.32); color:#F2D19A; }
.tr-auth-alert.success { background: rgba(62,213,152,.09); border:1px solid rgba(62,213,152,.35); color:#9FEBC9; }
.tr-auth-alert svg { flex:none; margin-top:1px; }
.tr-auth-busy { display:flex; align-items:center; justify-content:center; gap:12px; margin-top:12px; padding:12px; border-radius:14px; font-family:var(--tr-mono); font-size:12px; letter-spacing:.24em; color:#FF8CA0;
  background: rgba(255,51,85,.08); border:1px solid rgba(255,51,85,.3); }
.tr-auth-busy .spin { width:16px; height:16px; border-radius:50%; border:2px solid rgba(255,107,133,.25); border-top-color:#FF6B85; animation: tr-spin .8s linear infinite; }
.tr-auth-done { display:flex; align-items:center; justify-content:center; gap:12px; margin-top:12px; padding:14px; border-radius:14px; font-family:var(--tr-mono); font-size:12px; letter-spacing:.24em; color:#9FEBC9;
  background: rgba(62,213,152,.10); border:1px solid rgba(62,213,152,.4); animation: tr-auth-rise .3s var(--tr-ease) both; }
.tr-auth-sent { text-align:center; padding:26px 8px 12px; animation: tr-auth-rise .5s var(--tr-ease) both; }
.tr-auth-sent .ic { width:76px; height:76px; margin:0 auto 18px; border-radius:50%; display:flex; align-items:center; justify-content:center; color:#3ED598;
  background: radial-gradient(circle at 50% 40%, rgba(62,213,152,.22), rgba(62,213,152,.04) 70%); border:1px solid rgba(62,213,152,.45); box-shadow: 0 0 0 8px rgba(62,213,152,.05), 0 14px 40px -18px rgba(62,213,152,.9); }
.tr-auth-sent h3 { font-size:24px; font-weight:800; letter-spacing:-.03em; color:#fff; margin:0 0 8px; }
.tr-auth-sent p { color:var(--tr-text-2); font-size:14.5px; line-height:1.55; margin:0 auto; max-width:380px; }
.tr-auth-sent p b { color:#fff; font-weight:700; }
.tr-auth-sent .small { font-size:12.5px; color:var(--tr-text-3); margin-top:14px; }
.tr-auth-config { font-size:12.5px; color:var(--tr-text-3); text-align:center; margin-top:6px; }
.tr-auth-sent .steps { display:flex; flex-direction:column; gap:8px; margin:18px auto 4px; max-width:340px; text-align:left; }
.tr-auth-sent .step { display:flex; gap:10px; align-items:flex-start; font-size:13px; color:var(--tr-text-2); line-height:1.45; }
.tr-auth-sent .step strong { color:#fff; font-weight:700; }
.tr-auth-sent .step b { flex:none; width:22px; height:22px; border-radius:50%; display:inline-flex; align-items:center; justify-content:center;
  font-family:var(--tr-mono); font-size:10.5px; color:#FF6B85; border:1px solid rgba(255,51,85,.45); background:rgba(255,51,85,.08); }
[class*="st-key-auth_resend"] .stButton > button {
  background: linear-gradient(180deg,#FF6A85 0%,#FF3355 48%,#D4123F 100%); color:#fff; border:1px solid rgba(255,140,160,.45);
  font-size:13.5px; font-weight:800; letter-spacing:.12em; text-transform:uppercase; min-height:54px; border-radius:16px; margin-top:6px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.4), inset 0 -1px 0 rgba(0,0,0,.25), 0 22px 46px -16px rgba(255,51,85,.95); }
[class*="st-key-auth_resend"] .stButton > button:hover { background: linear-gradient(180deg,#FF7A93 0%,#FF3F5F 48%,#DC1746 100%); color:#fff; }
[class*="st-key-auth_resend"] .stButton > button:disabled { opacity:.55; transform:none; box-shadow:none; cursor:default; }
[class*="st-key-auth_resend"] .stButton > button p { font-size:13.5px; font-weight:800; letter-spacing:.12em; }
[class*="st-key-auth_verify_back"] .stButton > button { min-height:50px; border-radius:16px; font-size:13px; font-weight:700; letter-spacing:.1em; text-transform:uppercase;
  background: linear-gradient(180deg, rgba(255,255,255,.07), rgba(255,255,255,.03)); border:1px solid rgba(255,255,255,.14); color:var(--tr-text); }
[class*="st-key-auth_verify_back"] .stButton > button p { font-size:13px; font-weight:700; letter-spacing:.1em; }

/* ── the phone header, hidden on a desktop ─────────────────────────── */
.tr-auth-mhead { display:none; }

/* ── foot ──────────────────────────────────────────────────────────── */
.tr-auth-bottom { display:flex; align-items:center; justify-content:space-between; gap:20px; padding:18px 2px 0; margin-top:8px; border-top:1px solid rgba(255,255,255,.07); animation: tr-auth-fade 1s .3s var(--tr-ease) both; }
.tr-auth-bottom .items { display:flex; gap:28px; flex-wrap:wrap; }
.tr-auth-bottom .item { display:inline-flex; align-items:center; gap:9px; font-size:13.5px; color:var(--tr-text-2); font-weight:600; }
.tr-auth-bottom .item svg { color:#FF5573; }
.tr-auth-bottom .sign { font-family:var(--tr-mono); font-size:12px; letter-spacing:.3em; color:var(--tr-text-3); white-space:nowrap; }
.tr-auth-bottom .sign em { font-style:normal; color:var(--tr-accent); }

/* ── motion ────────────────────────────────────────────────────────── */
@keyframes tr-auth-rise { from { opacity:0; transform:translateY(16px); } to { opacity:1; transform:none; } }
@keyframes tr-auth-fade { from { opacity:0; } to { opacity:1; } }
@keyframes tr-auth-breathe { 0%,100% { opacity:.8; transform:scale(1); } 50% { opacity:1; transform:scale(1.06); } }
@keyframes tr-radar-sweep { to { transform:rotate(360deg); } }
@keyframes tr-blip { 0% { box-shadow:0 0 0 0 rgba(255,51,85,.7); opacity:1; } 70% { box-shadow:0 0 0 14px rgba(255,51,85,0); opacity:.9; } 100% { box-shadow:0 0 0 0 rgba(255,51,85,0); opacity:1; } }
@media (prefers-reduced-motion: reduce) {
  [class*="st-key-trauth_shell"] *, [class*="st-key-trauth_shell"] *::before, [class*="st-key-trauth_shell"] *::after { animation: none !important; transition: none !important; }
  .tr-radar .sweep { transform: rotate(35deg); }
}

/* ── laptops: the wall goes first, the radar shrinks ───────────────── */
@media (max-width: 1280px) {
  .tr-auth-wall { width:300px; }
  .tr-auth-wall .grid { grid-template-columns:repeat(3, 88px); gap:12px; }
  .tr-auth-wall .card { height:130px; }
  .tr-auth-copy h1 { font-size:50px; }
  .tr-auth-city { right:250px; }
}
@media (max-width: 1100px) {
  .block-container { padding: .9rem 1.4rem 1.4rem !important; }
  .tr-auth-wall, .tr-auth-skyline, .tr-auth-city { display:none; }
  .tr-auth-copy { max-width:none; }
  .tr-auth-visual { padding-right:12px; }
  .tr-auth-copy h1 { font-size:44px; }
  .tr-auth-copy .lede { font-size:16.5px; }
  .tr-auth-feats { gap:12px; }
  .tr-auth-feat { width:92px; }
  .tr-auth-visual { min-height:680px; padding-bottom:140px; }
  .tr-auth-nav .tag { display:none; }
  [class*="st-key-trauth_panel"] { padding:26px 24px 24px !important; }
}

/* ── phones: the panel is the page ─────────────────────────────────── */
@media (max-width: 768px) {
  .block-container { padding: .9rem 1rem 2rem !important; }
  [data-testid="stMainBlockContainer"] { padding-top: .4rem !important; }
  .tr-auth-top { padding:2px 0 10px; }
  .tr-auth-nav { display:none; }
  .tr-auth-lockup .mark { width:40px; height:40px; }
  .tr-auth-lockup .name { font-size:21px; }
  .tr-auth-lockup .sub { font-size:8.5px; letter-spacing:.26em; }
  /* the cinematic column is gone; a compact header carries the message */
  [class*="st-key-trauth_shell"] > div > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child { display:none !important; }
  .tr-auth-mhead { display:block; position:relative; padding:14px 2px 18px; overflow:hidden; animation: tr-auth-rise .7s var(--tr-ease) both; }
  .tr-auth-mhead h1 { font-size:clamp(30px, 8.6vw, 38px); line-height:1.04; letter-spacing:-.04em; font-weight:800; color:#fff; margin:0; }
  .tr-auth-mhead h1 em { font-style:normal; color:var(--tr-accent); }
  .tr-auth-mhead .lede { font-size:14.5px; line-height:1.5; color:var(--tr-text-2); margin:10px 0 0; max-width:34ch; }
  .tr-auth-mhead .mini { position:absolute; right:0; top:-10px; width:130px; height:130px; opacity:.85; }
  .tr-auth-mhead .mini .ring { position:absolute; border-radius:50%; border:1px solid rgba(255,51,85,.35); left:50%; top:50%; transform:translate(-50%,-50%); }
  .tr-auth-mhead .mini .r1 { width:40px; height:40px; border-color:rgba(255,51,85,.6); } .tr-auth-mhead .mini .r2 { width:86px; height:86px; } .tr-auth-mhead .mini .r3 { width:120px; height:120px; border-color:rgba(255,51,85,.2); }
  .tr-auth-mhead .mini .sweep { position:absolute; inset:8px; border-radius:50%; background: conic-gradient(from 0deg, rgba(255,51,85,0) 0deg, rgba(255,51,85,0) 270deg, rgba(255,51,85,.4) 359deg, rgba(255,51,85,0) 360deg); animation: tr-radar-sweep 6.5s linear infinite; }
  .tr-auth-mhead .mini .core { position:absolute; left:50%; top:50%; width:7px; height:7px; border-radius:50%; background:#FF3355; transform:translate(-50%,-50%); box-shadow:0 0 16px 3px rgba(255,51,85,.8); }
  .tr-auth-mhead .feats { display:flex; gap:8px; margin-top:16px; flex-wrap:wrap; }
  .tr-auth-mhead .feats span { display:inline-flex; align-items:center; gap:6px; padding:6px 10px; border-radius:999px; border:1px solid rgba(255,51,85,.3); background:rgba(255,51,85,.07); font-size:11.5px; font-weight:600; color:var(--tr-text-2); white-space:nowrap; }
  .tr-auth-mhead .feats svg { color:#FF5573; }
  [class*="st-key-trauth_panel"] { max-width:none; margin:0; padding:22px 18px 20px !important; border-radius:20px; }
  .tr-auth-brand { gap:14px; padding-bottom:16px; }
  .tr-auth-brand .lock svg { width:38px; height:38px; }
  .tr-auth-brand .lock .n { font-size:18px; }
  .tr-auth-brand .tag { font-size:9.5px; letter-spacing:.28em; }
  .tr-auth-h { font-size:27px; }
  .tr-auth-p { font-size:14.5px; margin-bottom:12px; }
  [class*="st-key-trauth_panel"] .stTextInput [data-baseweb="input"] input { font-size:16px !important; }   /* no iOS zoom */
  [class*="st-key-trauth_panel"] .stFormSubmitButton > button { min-height:56px; font-size:14px; }
  [class*="st-key-auth_google"] .stButton > button, a.tr-auth-google { min-height:54px; font-size:14.5px; }
  [class*="st-key-trpair_auth_foot"] [data-testid="stHorizontalBlock"] { flex-wrap:nowrap !important; }
  .tr-auth-foot-text { font-size:13.5px; }
  .tr-auth-bottom { flex-direction:column; align-items:flex-start; gap:12px; padding-top:14px; }
  .tr-auth-bottom .items { gap:12px 18px; }
  .tr-auth-bottom .item { font-size:12.5px; }
  .tr-auth-bottom .sign { font-size:10.5px; letter-spacing:.24em; }
}
@media (max-width: 400px) {
  [class*="st-key-trauth_panel"] { padding:20px 15px 18px !important; }
  .tr-auth-brand { gap:10px; } .tr-auth-brand .sep { display:none; } .tr-auth-brand .tag { display:none; }
  .tr-auth-foot-text { font-size:13px; }
}
</style>
""".replace("__MAIL__", _input_icon_uri("mail")).replace("__LOCK__", _input_icon_uri("lock")) \
   .replace("__USER__", _input_icon_uri("user")).replace("__GOOGLE__", GOOGLE_G)


# ──────────────────────────────────────────────────────────────────────────
# Markup
# ──────────────────────────────────────────────────────────────────────────
def _topbar() -> str:
    return f"""<div class="tr-auth-top">
      <div class="tr-auth-lockup">
        <div class="mark">{C.TICKET_GLYPH.replace('stroke="#fff"', 'stroke="#FF5573"')}</div>
        <div><div class="name">Ticket<em>Radar</em></div><div class="sub">MOVIES • ALERTS • YOU FIRST</div></div>
      </div>
      <div class="tr-auth-nav">
        <span class="city">Hyderabad {_icon("pin", 15, "#FF5573")}</span>
        <span>Movies</span><span>Theatres</span><span>Alerts</span>
        <span class="tag">A Better Movie Experience</span>
      </div>
    </div>"""


def _posters(limit: int = 6) -> list[tuple[str, str]]:
    """(title, poster url) for the wall — the city's real catalogue, never a
    made-up film. Placeholders fill in when the catalogue is empty."""
    picked: list[tuple[str, str]] = []
    seen: set[str] = set()
    try:
        for m in cv.view("hyderabad").movies:
            # Rows are per language; one poster per film is enough here.
            if m.poster_url and m.title not in seen:
                seen.add(m.title)
                picked.append((m.title, m.poster_url))
            if len(picked) == limit:
                break
    except Exception:  # noqa: BLE001 - the login page must never depend on the catalogue
        return []
    return picked


def _wall() -> str:
    cards = []
    for title, url in _posters():
        cards.append(f'<div class="card"><img src="{C.e(url)}" alt="" loading="lazy" onerror="this.remove()">'
                     f'<div class="t">{C.e(title)}</div></div>')
    for label in ("Now showing", "Coming soon", "Premieres", "Re-release", "Late night", "IMAX")[len(cards):]:
        cards.append(f'<div class="card ph"><div class="t">{label}</div></div>')
    return f'<div class="tr-auth-wall"><div class="grid">{"".join(cards)}</div></div>'


CHARMINAR = """<svg viewBox="0 0 280 150" fill="currentColor" aria-hidden="true">
<path d="M18 150V60c0-6 3-10 7-12 1-6 2-12 4-18l3-2 3 2c2 6 3 12 4 18 4 2 7 6 7 12v90zM234 150V60c0-6 3-10 7-12 1-6 2-12 4-18l3-2 3 2c2 6 3 12 4 18 4 2 7 6 7 12v90z"/>
<path d="M86 150V68c0-5 2-9 6-11 1-5 2-9 3-14l3-2 3 2c1 5 2 9 3 14 4 2 6 6 6 11v82zM170 150V68c0-5 2-9 6-11 1-5 2-9 3-14l3-2 3 2c1 5 2 9 3 14 4 2 6 6 6 11v82z"/>
<path d="M46 150V96h188v54H210v-30a18 18 0 0 0-36 0v30h-14v-28a20 20 0 0 0-40 0v28h-14v-30a18 18 0 0 0-36 0v30z"/>
<path d="M46 96V80h188v16z" opacity=".7"/><path d="M30 80h220v6H30z" opacity=".5"/>
<circle cx="140" cy="60" r="10" opacity=".5"/><path d="M128 70h24l-12-24z" opacity=".35"/>
</svg>"""


def _visual() -> str:
    feats = "".join(
        f'<div class="tr-auth-feat"><div class="ic">{_icon(icon, 22, "currentColor", "1.7")}</div>'
        f'<div class="t">{a}<br>{b}</div></div>'
        for icon, a, b in PROMISES
    )
    seats_back = "".join("<i></i>" for _ in range(11))
    seats_front = "".join("<i></i>" for _ in range(9))
    return f"""<div class="tr-auth-visual">
      <div class="glow"></div>
      <div class="tr-radar">
        <div class="ring r4"></div><div class="ring r3"></div><div class="ring r2"></div><div class="ring r1"></div>
        <div class="axis"></div><div class="axis v"></div>
        <div class="sweep"></div><div class="core"></div>
        <div class="blip b1"></div><div class="blip b2"></div><div class="blip b3"></div>
        <div class="tag">TICKETS<br>DETECTED</div>
      </div>
      <div class="tr-auth-copy">
        <h1>Never Miss<br>the <em>Moment</em></h1>
        <p class="lede">We track movie ticket releases so you don't have to.<br>Get instant email alerts the moment tickets go live.</p>
        <div class="tr-auth-feats">{feats}</div>
      </div>
      <div class="tr-auth-city">HYDERABAD<br>MOVES DIFFERENTLY</div>
      {_wall()}
      <div class="tr-auth-skyline">{CHARMINAR}</div>
      <div class="floor"></div>
      <div class="tr-auth-seats"><div class="row back">{seats_back}</div><div class="row front">{seats_front}</div></div>
      <div class="tr-auth-mark">SOME MOVIES ARE MEANT<br>TO BE EXPERIENCED TOGETHER</div>
    </div>"""


def _mobile_head() -> str:
    chips = "".join(f'<span>{_icon(icon, 13)}{a} {b}</span>' for icon, a, b in PROMISES)
    return f"""<div class="tr-auth-mhead">
      <div class="mini"><div class="ring r3"></div><div class="ring r2"></div><div class="ring r1"></div><div class="sweep"></div><div class="core"></div></div>
      <h1>Never Miss<br>the <em>Moment</em></h1>
      <p class="lede">Instant email alerts the moment tickets go live.</p>
      <div class="feats">{chips}</div>
    </div>"""


def _brand() -> str:
    return f"""<div class="tr-auth-brand">
      <div class="lock">{C.TICKET_GLYPH.replace('stroke="#fff"', 'stroke="#FF3355"').replace('width="19" height="19"', 'width="44" height="44"')}
        <div class="n">TICKET<em>RADAR</em></div></div>
      <div class="sep"></div>
      <div class="tag">GOOD<br>MOVIES<br>FIND<br>YOU</div>
    </div>"""


def _bottom() -> str:
    return f"""<div class="tr-auth-bottom">
      <div class="items">
        <span class="item">{_icon("signal", 17)}Monitoring 24 / 7</span>
        <span class="item">{_icon("shield", 17)}Secure &amp; Private</span>
        <span class="item">{_icon("heart", 17)}Built for Movie Lovers</span>
      </div>
      <div class="sign">Lights. &nbsp;Tickets. &nbsp;<em>Action.</em></div>
    </div>"""


def _alert(kind: str, message: str) -> None:
    glyph = {"error": "error", "info": "warning", "success": "check"}.get(kind, "warning")
    color = {"error": "#FF8A8A", "info": "#E8B25C", "success": "#3ED598"}.get(kind, "#E8B25C")
    C.html(f'<div class="tr-auth-alert {kind}">{_icon(glyph, 17, color, "2")}<span>{C.e(message)}</span></div>')


def _label(text: str) -> None:
    C.html(f'<div class="tr-auth-label">{C.e(text)}</div>')


# ──────────────────────────────────────────────────────────────────────────
# State
# ──────────────────────────────────────────────────────────────────────────
def _mode() -> str:
    return st.session_state.get(MODE_KEY) or "signin"


def _switch(mode: str) -> None:
    """An ``on_click`` for the text links: runs before the rerun that the
    click causes, so the new state is what that rerun draws."""
    st.session_state[MODE_KEY] = mode
    st.session_state.pop(ERROR_KEY, None)
    st.session_state.pop(NOTICE_KEY, None)


def _google_notice() -> None:
    st.session_state[NOTICE_KEY] = ("info", google.MESSAGES["not_configured"])


def _page_url() -> str:
    """The URL the browser is on, for deriving the redirect URI. Empty when
    Streamlit cannot say (bare mode, older runtimes)."""
    try:
        return str(st.context.url or "")
    except Exception:  # noqa: BLE001
        return ""


def google_ready() -> tuple[bool, str]:
    """Is Google sign-in usable here: configured, and a redirect URI known?
    Returns ``(ready, redirect_uri)``."""
    if not google.is_configured():
        return False, ""
    redirect = google.redirect_uri(_page_url())
    return bool(redirect), redirect


def prepare_google() -> None:
    """Mint this session's ``state`` and queue its cookie — called by the
    gate *before* the bridge renders, so the cookie exists by the time the
    link below is drawn. Nothing happens unless Google is configured."""
    if google.is_configured():
        session.oauth_state()


def _google_button() -> None:
    """The one control on the page that is a link rather than a button.

    A Streamlit button reruns the script; it cannot send the browser to
    Google, and the bridge's iframe is sandboxed without permission to. An
    anchor with ``target="_top"`` is a plain top-level navigation — allowed
    from anywhere, framed or not — and it is styled to be indistinguishable
    from the button it replaces. When Google is not configured the button
    stays, and says so honestly, exactly as before.
    """
    ready, redirect = google_ready()
    if not ready:
        st.button("Continue with Google", key="auth_google", use_container_width=True, on_click=_google_notice)
        return
    url = google.authorization_url(session.oauth_state(), redirect)
    with st.container(key="auth_google"):
        C.html(f'<a class="tr-auth-google" href="{escape(url, quote=True)}" target="_top" '
               f'rel="noopener" data-testid="tr-google-signin">Continue with Google</a>')


def handle_google_return() -> session.AuthUser | None:
    """Back from Google with ``?code=…&state=…`` — or ``?error=…``.

    Runs once the bridge has answered, because the ``state`` cookie is the
    only thing that ties this brand-new session to the link the person
    clicked. The code is single-use and never reaches a second run: the
    query string is cleared before anything else happens. A success signs
    in through ``session.sign_in_user`` — the same call a password sign-in
    makes — and reruns into the app; every failure is one clean line on the
    login page, and nothing is created.
    """
    try:
        params = {k: str(v) for k, v in st.query_params.to_dict().items()}
    except Exception:  # noqa: BLE001 - no query params in bare mode
        return None
    code, state, error = params.get("code", ""), params.get("state", ""), params.get("error", "")
    if not code and not error:
        return None
    try:
        st.query_params.clear()
    except Exception:  # noqa: BLE001
        pass
    st.session_state[MODE_KEY] = "signin"

    if error and not code:
        print(f"[auth] google return: error={error[:40]}", flush=True)
        session.clear_oauth_state()
        st.session_state[NOTICE_KEY] = ("info", google.MESSAGES["cancelled"] if error == "access_denied"
                                        else google.MESSAGES["failed"])
        return None

    if not session.oauth_state_matches(state):
        print("[auth] google return: state mismatch - code not exchanged", flush=True)
        session.clear_oauth_state()
        st.session_state[ERROR_KEY] = google.MESSAGES["state"]
        return None
    session.clear_oauth_state()

    try:
        redirect = google.redirect_uri(_page_url())
        id_token = google.exchange_code(code, redirect)
        creds = firebase.FirebaseAuth().sign_in_with_google(id_token, redirect)
        if not creds.email_verified:
            # Google always vouches for its addresses; if Firebase still
            # says otherwise, do not enter and do not start the password
            # account's verification flow for an account that has none.
            raise AuthError(firebase.MESSAGES["EMAIL_NOT_VERIFIED"], "EMAIL_NOT_VERIFIED")
        user = session.sign_in_user(creds)
    except (google.GoogleError, AuthError) as exc:
        st.session_state[ERROR_KEY] = str(exc)
        return None
    print("[auth] google return: signed in", flush=True)
    st.rerun()
    return user


def _forgot_from_signin() -> None:
    """Switch to reset only after validating the already-entered email."""
    email = str(st.session_state.get("auth_email", "")).strip()
    if not email:
        st.session_state[ERROR_KEY] = "Enter your email address first."
        return
    st.session_state["auth_reset_email"] = email
    _switch("reset")


def _fail(message: str) -> None:
    st.session_state[ERROR_KEY] = message
    st.rerun()


def _feedback() -> None:
    error = st.session_state.pop(ERROR_KEY, None)
    if error:
        _alert("error", error)
    notice = st.session_state.pop(NOTICE_KEY, None)
    if notice:
        _alert(notice[0], notice[1])
    if not firebase.is_configured():
        _alert("info", firebase.MESSAGES["not_configured"])


def _busy(placeholder, text: str) -> None:
    placeholder.markdown(f'<div class="tr-auth-busy"><span class="spin"></span>{C.e(text)}</div>',
                         unsafe_allow_html=True)


def _attempt(placeholder, busy_text: str, action: Callable[[], None]) -> None:
    """Run one Firebase call with the busy state on screen; a failure becomes
    the panel's error line on the next paint."""
    _busy(placeholder, busy_text)
    try:
        action()
    except AuthError as exc:
        placeholder.empty()
        _fail(str(exc))


def _welcome(placeholder, user: session.AuthUser) -> None:
    """The success beat: a green line for half a second, then the app."""
    placeholder.markdown(
        f'<div class="tr-auth-done">{_icon("check", 16, "#3ED598", "2.4")}'
        f'<span>SIGNED IN — WELCOME, {C.e(user.first_name.upper())}</span></div>',
        unsafe_allow_html=True,
    )
    time.sleep(0.45)
    st.rerun()


# ──────────────────────────────────────────────────────────────────────────
# The three panels
# ──────────────────────────────────────────────────────────────────────────
def _signin() -> None:
    verified = st.session_state.pop(VERIFIED_KEY, "")
    if verified:
        C.html('<h2 class="tr-auth-h">Email verified.</h2>'
               '<p class="tr-auth-p">Your TicketRadar account is active. Sign in with your password to continue.</p>')
        _alert("success", f"{verified} is verified — welcome to TicketRadar.")
    else:
        C.html('<h2 class="tr-auth-h">Welcome back.</h2>'
               '<p class="tr-auth-p">Track movie ticket releases and get notified the moment they go live.</p>')
    with st.form("auth_signin_form", border=False, clear_on_submit=False):
        _label("Email address")
        email = st.text_input("Email address", key="auth_email", placeholder="you@example.com",
                              label_visibility="collapsed", autocomplete="email")
        _label("Password")
        password = st.text_input("Password", key="auth_password", type="password",
                                 placeholder="Enter your password", label_visibility="collapsed",
                                 autocomplete="current-password")
        _feedback()
        submitted = st.form_submit_button("SIGN IN", key="auth_signin", type="primary",
                                          use_container_width=True, icon=":material/arrow_forward:")
    st.button("Forgot password?", key="auth_forgot", on_click=_forgot_from_signin)
    status = st.empty()

    C.html('<div class="tr-auth-or">OR</div>')
    _google_button()

    with st.container(key="trpair_auth_foot"):
        a, b = st.columns([1, 1], gap="small", vertical_alignment="center")
        with a:
            C.html('<div class="tr-auth-foot-text">Don\'t have an account?</div>')
        with b:
            st.button("CREATE ACCOUNT", key="auth_to_signup", on_click=_switch, args=("signup",))

    if submitted:
        email = (email or "").strip()
        if not email or not password:
            _fail("Enter your email and password.")
        if not firebase.valid_email(email):
            _fail(firebase.MESSAGES["INVALID_EMAIL"])

        def go() -> None:
            creds = firebase.FirebaseAuth().sign_in(email, password)
            if not creds.email_verified:
                # The account is real; the address isn't proven yet. Park
                # it for resending — never as a signed-in user.
                session.set_pending(creds)
                st.session_state[MODE_KEY] = "verify"
                st.session_state[NOTICE_KEY] = ("info", "Your email address isn't verified yet. "
                                                        "Open the link we sent you, then sign in.")
                st.rerun()
            user = session.sign_in_user(creds)
            _welcome(status, user)

        _attempt(status, "SIGNING IN…", go)


def _signup() -> None:
    C.html('<h2 class="tr-auth-h">Create your account.</h2>'
           '<p class="tr-auth-p">One account, every theatre. Alerts land in your inbox the moment tickets open.</p>')
    with st.form("auth_signup_form", border=False, clear_on_submit=False):
        _label("Full name")
        name = st.text_input("Full name", key="auth_su_name", placeholder="Your name",
                             label_visibility="collapsed", autocomplete="name")
        _label("Email address")
        email = st.text_input("Email address", key="auth_su_email", placeholder="you@example.com",
                              label_visibility="collapsed", autocomplete="email")
        _label("Password")
        password = st.text_input("Password", key="auth_su_password", type="password",
                                 placeholder=f"At least {firebase.PASSWORD_MIN} characters, letters and numbers",
                                 label_visibility="collapsed", autocomplete="new-password")
        _label("Confirm password")
        confirm = st.text_input("Confirm password", key="auth_su_confirm", type="password",
                                placeholder="Type it once more", label_visibility="collapsed",
                                autocomplete="new-password")
        _feedback()
        submitted = st.form_submit_button("CREATE ACCOUNT", key="auth_signup", type="primary",
                                          use_container_width=True, icon=":material/arrow_forward:")
    status = st.empty()
    with st.container(key="trpair_auth_foot"):
        a, b = st.columns([1, 1], gap="small", vertical_alignment="center")
        with a:
            C.html('<div class="tr-auth-foot-text">Already have an account?</div>')
        with b:
            st.button("Sign in", key="auth_to_signin", on_click=_switch, args=("signin",))

    if submitted:
        name = " ".join((name or "").split())
        email = (email or "").strip()
        if not name:
            _fail("Enter your full name.")
        if not email:
            _fail("Enter your email address.")
        if not firebase.valid_email(email):
            _fail(firebase.MESSAGES["INVALID_EMAIL"])
        problem = firebase.password_problem(password or "")
        if problem:
            _fail(f"Choose a stronger password — {problem.lower()}")
        if password != confirm:
            _fail("Those passwords don't match. Type them again.")

        def go() -> None:
            client = firebase.FirebaseAuth()
            creds = client.sign_up(name, email, password)
            # Firebase has the account. The person is not signed in until
            # they use the verification link — which is requested here, and
            # only counted as sent when Firebase says 2xx.
            session.set_pending(creds)
            st.session_state[MODE_KEY] = "verify"
            try:
                client.send_email_verification(creds.id_token, _continue_url(creds.email))
            except AuthError as exc:
                session.record_verification_answer(exc.status, exc.code)
                st.session_state[ERROR_KEY] = _send_failure(exc, created=True)
            else:
                session.record_verification_answer(client.last_status, client.last_code)
                session.mark_verification_sent(RESEND_COOLDOWN)
                st.session_state[NOTICE_KEY] = ("success", _accepted(creds.email))
            st.rerun()

        _attempt(status, "CREATING YOUR ACCOUNT…", go)


def _send_failure(exc: AuthError, *, created: bool = False) -> str:
    """What to say when Firebase did not accept a VERIFY_EMAIL request.
    Names the code and status, because that is what fixes the project
    configuration; never a key, a token or an address."""
    lead = "Your account was created, but " if created else ""
    return (f"{lead}Firebase rejected the verification email request: {exc.code} "
            f"(HTTP {exc.status}). {exc}")


def _accepted(email: str) -> str:
    """Accepted is not delivered — say exactly that, without saying "Inbox"."""
    return (f"Verification email sent to {email}. Check your Inbox, Spam, or Promotions folder — "
            "it comes from Firebase, not from TicketRadar's own address.")


def _continue_url(email: str) -> str:
    """Where Firebase's verification page offers to send the person next:
    this app, with a flag and the address to prefill — never a credential.
    "" when the app's own URL can't be read (tests, an old runtime)."""
    from urllib.parse import quote, urlsplit

    try:
        parts = urlsplit(str(st.context.url or ""))
    except Exception:  # noqa: BLE001
        return ""
    if not parts.scheme or not parts.netloc or parts.netloc.startswith("localhost"):
        return ""
    return f"{parts.scheme}://{parts.netloc}/?verified={quote(email)}"


def handle_verified_return() -> None:
    """Back from Firebase's "email verified" page (its Continue button):
    show the verified banner on the sign-in form with the address ready.
    The flag proves nothing by itself — sign-in still needs the password
    and Firebase's own record still decides ``email_verified``."""
    try:
        email = str(st.query_params.get("verified") or "").strip()
    except Exception:  # noqa: BLE001
        return
    if not email:
        return
    # ``verified`` is navigation state only, never proof. If this browser
    # still has the Firebase refresh credential, ask Firebase again and enter
    # automatically; otherwise use a normal sign-in with this email prefilled.
    pend = session.pending()
    if pend and pend.get("email") == email and session.continue_if_verified() is not None:
        try:
            st.query_params.clear()
        except Exception:  # noqa: BLE001
            pass
        st.rerun()
    st.session_state[VERIFIED_KEY] = email
    st.session_state[MODE_KEY] = "signin"
    if firebase.valid_email(email):
        st.session_state["auth_email"] = email
    try:
        st.query_params.clear()
    except Exception:  # noqa: BLE001
        pass


def _cooldown_remaining(pend: dict | None, now: float | None = None) -> int:
    """Whole seconds until the resend is allowed again — from the absolute
    deadline Firebase's acceptance set, never a counter."""
    if not pend:
        return 0
    now = time.time() if now is None else now
    left = float(pend.get("cooldown_until", 0)) - now
    return max(0, math.ceil(left))


def _resend_state() -> tuple[bool, str]:
    """(allowed, why not) for another verification email."""
    pend = session.pending()
    if pend is None:
        return False, "Sign in again to request a new link."
    if pend.get("sends", 0) >= RESEND_LIMIT:
        return False, "That's the limit for now — check spam, or try again later."
    remaining = _cooldown_remaining(pend)
    if remaining > 0:
        return False, f"RESEND AVAILABLE IN {remaining}s"
    return True, ""


def _resend() -> None:
    """An ``on_click``: one more verification email. "Sent" and the
    cooldown happen only after Firebase answered 2xx."""
    allowed, _ = _resend_state()
    pend = session.pending()
    if not allowed or pend is None:
        return
    client = firebase.FirebaseAuth()
    try:
        client.send_email_verification(pend["id_token"], _continue_url(pend["email"]))
    except AuthError as exc:
        session.record_verification_answer(exc.status, exc.code)
        if exc.code in ("INVALID_ID_TOKEN", "TOKEN_EXPIRED", "USER_NOT_FOUND", "CREDENTIAL_TOO_OLD_LOGIN_AGAIN"):
            session.clear_pending()
            st.session_state[ERROR_KEY] = ("Firebase rejected the verification email request: "
                                           f"{exc.code} (HTTP {exc.status}). Sign in again and we'll send a fresh link.")
        else:
            st.session_state[ERROR_KEY] = _send_failure(exc)
        return
    session.record_verification_answer(client.last_status, client.last_code)
    session.mark_verification_sent(RESEND_COOLDOWN)
    st.session_state[NOTICE_KEY] = ("success", _accepted(pend["email"]))


def _verify() -> None:
    """Verify your email: the account exists, the address isn't proven yet.
    The hierarchy is deliberate — verify first; resend is the fallback."""
    pend = session.pending()
    email = pend["email"] if pend else ""
    sent = bool(pend and pend.get("sends", 0) > 0)
    lead = (f"We've sent a verification link to <strong>{C.e(email)}</strong>." if sent else
            f"We'll send a verification link to <strong>{C.e(email)}</strong> when you use the button below.")
    C.html(f"""<div class="tr-auth-sent">
      <div class="ic wait">{_icon("mail", 32, "currentColor", "1.6")}</div>
      <div class="eyebrow">VERIFY YOUR EMAIL</div>
      <h3>You're almost in.</h3>
      <p>{lead}</p>
      <div class="steps">
        <div class="step"><b>1</b><span>Open the email from TicketRadar.</span></div>
        <div class="step"><b>2</b><span>Click <strong>Verify email</strong>.</span></div>
        <div class="step"><b>3</b><span>Return here.</span></div>
      </div>
      <div class="folders">{_icon("search", 14)}<span>Check your Inbox, Spam, or Promotions folder.</span></div>
    </div>""")
    _countdown()
    st.button("BACK TO SIGN IN", key="auth_verify_back", use_container_width=True,
              on_click=_switch, args=("signin",))


def _countdown() -> None:
    """The resend button and its countdown live in a
    fragment that reruns itself once a second while an account is waiting —
    the rest of the page (and the app) stays put. Each tick reads the
    absolute deadline, so a tab that was asleep shows the right number the
    moment it wakes; every ``VERIFY_POLL`` seconds it also asks Firebase
    whether the link has been used, and continues into the app when it has."""
    pend = session.pending()
    live = pend is not None and (pend.get("sends", 0) < RESEND_LIMIT or bool(pend.get("refresh_token")))

    @st.fragment(run_every=1 if live else None)
    def block() -> None:
        pend = session.pending()
        if pend and pend.get("refresh_token") and time.time() - float(pend.get("checked_at", 0)) >= VERIFY_POLL:
            if session.continue_if_verified() is not None:
                st.rerun()
        _feedback()
        allowed, why = _resend_state()
        pend = session.pending()
        sent = bool(pend and pend.get("sends", 0) > 0)
        st.button("RESEND VERIFICATION EMAIL" if sent else "SEND VERIFICATION EMAIL", key="auth_resend",
                  use_container_width=True, disabled=not allowed, help=why or None, on_click=_resend)
        if allowed and sent:
            why = "RESEND AVAILABLE"
        if why:
            klass = "tr-auth-count" if why.startswith("RESEND") else "tr-auth-hint"
            C.html(f'<div class="{klass}" style="text-align:center;">{C.e(why)}</div>')
    block()


def _reset() -> None:
    C.html('<h2 class="tr-auth-h">Reset your password.</h2>'
           '<p class="tr-auth-p">Enter your email and we\'ll send you a secure reset link.</p>')
    with st.form("auth_reset_form", border=False, clear_on_submit=False):
        _label("Email address")
        email = st.text_input("Email address", key="auth_reset_email", placeholder="you@example.com",
                              label_visibility="collapsed", autocomplete="email")
        _feedback()
        submitted = st.form_submit_button("SEND RESET LINK", key="auth_send_reset", type="primary",
                                          use_container_width=True, icon=":material/arrow_forward:")
    status = st.empty()
    _back_to_signin()

    if submitted:
        email = (email or "").strip()
        if not firebase.valid_email(email):
            _fail(firebase.MESSAGES["INVALID_EMAIL"])

        def go() -> None:
            firebase.FirebaseAuth().send_password_reset(email)
            st.session_state["auth_reset_sent_to"] = email
            st.session_state[MODE_KEY] = "reset_sent"
            st.rerun()

        _attempt(status, "SENDING…", go)


def _reset_sent() -> None:
    email = st.session_state.get("auth_reset_sent_to", "")
    C.html(f"""<div class="tr-auth-sent">
      <div class="ic">{_icon("mail", 32, "currentColor", "1.6")}</div>
      <h3>Check your inbox.</h3>
      <p>If an account exists for this email, a password-reset email has been requested. Check your Inbox or Spam.</p>
      <p class="small">Nothing there in a couple of minutes? Look in spam, or send it again.</p>
    </div>""")
    st.button("Send it again", key="auth_back", help="Request another reset link",
              on_click=_switch, args=("reset",))
    _back_to_signin()


def _back_to_signin() -> None:
    with st.container(key="trpair_auth_foot"):
        a, b = st.columns([1, 1], gap="small", vertical_alignment="center")
        with a:
            C.html('<div class="tr-auth-foot-text">Remembered it?</div>')
        with b:
            st.button("Back to sign in", key="auth_to_signin", on_click=_switch, args=("signin",))


# ──────────────────────────────────────────────────────────────────────────
# The page
# ──────────────────────────────────────────────────────────────────────────
def render() -> None:
    """Draw the whole entrance. The caller stops the script afterwards.

    The cookie flush and the verified-email return are handled by the gate
    (``auth.gate``) before this runs, inside the fixed ``tr_chrome`` slot, so
    they never shift this page's position between runs.
    """
    st.markdown(CSS, unsafe_allow_html=True)
    with st.container(key="trauth_shell"):
        C.html(_topbar())
        visual, panel = st.columns([1.16, 0.84], gap="large")
        with visual:
            C.html(_visual())
        with panel:
            C.html(_mobile_head())
            with st.container(key="trauth_panel"):
                C.html(_brand())
                mode = _mode()
                if mode == "signup":
                    _signup()
                elif mode == "verify":
                    _verify()
                elif mode == "reset":
                    _reset()
                elif mode == "reset_sent":
                    _reset_sent()
                else:
                    _signin()
        C.html(_bottom())


#: Opacity only, and only the main pane. A ``transform`` here (or anything
#: on the sidebar) overrides the ``translateX`` Streamlit itself uses to
#: slide the sidebar away — the collapsed sidebar then sits over the page
#: and swallows every click. That was the broken sidebar after sign-in.
ENTRANCE_CSS = """<style>
[data-testid="stMainBlockContainer"] { animation: tr-app-enter .6s var(--tr-ease) both; }
@keyframes tr-app-enter { from { opacity:0; } to { opacity:1; } }
@media (prefers-reduced-motion: reduce) { [data-testid="stMainBlockContainer"] { animation:none; } }
</style>"""


def entrance() -> None:
    """The first paint after signing in eases the app in, once."""
    if st.session_state.pop("auth_just_signed_in", False):
        st.markdown(ENTRANCE_CSS, unsafe_allow_html=True)


__all__ = ["CSS", "ENTRANCE_CSS", "MODE_KEY", "ERROR_KEY", "NOTICE_KEY", "VERIFIED_KEY",
           "entrance", "handle_verified_return", "render"]
