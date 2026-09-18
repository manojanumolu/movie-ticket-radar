"""The entrance: sign in, create an account, reset a password.

A cinematic room on the left — the radar (``ui.radar``) leading, a fanned
hand of six fixed posters (``ui.assets_registry``, the brand's own files,
never the catalogue's), the twenty-four crafts of filmmaking scattered in
the dark (``ui.crafts``), the headline on the floor — and a charcoal panel
on the right that holds the form. Everything on screen is the same
HTML-in-markdown plus keyed-container technique the rest of the UI uses,
so the page is still plain Streamlit: the inputs, buttons and reruns are
Streamlit's, which is what lets ``AppTest`` drive it. Nothing here reads
the catalogue, Firestore or a network for decoration.

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

On a phone the room steps aside for a stack of its own: the brand, a short
hero with a small radar and three posters, then the panel, which is the
page. The desktop composition is gone rather than squeezed.

Waiting is honest: a Firebase call puts one mono line with a tiny radar in
the panel at once, and only if it drags past half a second does the room
dim behind a larger radar and a status line — a CSS delay, never a sleep.
"""

from __future__ import annotations

import math
import time
from html import escape
from typing import Callable

import streamlit as st

from auth import disposable, firebase, google, session
from auth.firebase import AuthError
from ui import assets_registry as art
from ui import components as C
from ui import crafts, radar

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
/* ── the room: near-black, one projector's warmth low on the left, a cooler
   spill high on the right — the light of a theatre before the film ────── */
html, body, [data-testid="stAppViewContainer"], .stApp {
  background:
    radial-gradient(1100px 700px at 6% 96%, rgba(255,51,85,.20), transparent 60%),
    radial-gradient(800px 520px at 96% 4%, rgba(255,51,85,.09), transparent 58%),
    radial-gradient(1600px 900px at 50% 40%, #0F0F15, #07070A 78%) !important;
  background-attachment: fixed !important;
}
.block-container { padding: 1rem 2.4rem 1.6rem !important; max-width: 1600px !important; }
[data-testid="stMainBlockContainer"] { padding-top: .6rem !important; }
[data-testid="stHeader"] { display: none !important; }
[class*="st-key-trauth_shell"] { position: relative; }
/* a fine film grain over the whole room; one tiny repeating gradient, no image */
[class*="st-key-trauth_shell"]::before { content:""; position:fixed; inset:0; z-index:0; pointer-events:none; opacity:.35;
  background-image: repeating-linear-gradient(0deg, rgba(255,255,255,.012) 0 1px, transparent 1px 3px); }

/* ── top bar ───────────────────────────────────────────────────────── */
.tr-auth-top { position:relative; z-index:2; display:flex; align-items:center; justify-content:space-between; gap:20px; padding:4px 2px 12px; animation: tr-auth-fade .7s var(--tr-ease) both; }
.tr-auth-lockup { display:flex; align-items:center; gap:13px; }
.tr-auth-lockup .mark { width:46px; height:46px; border-radius:50%; display:flex; align-items:center; justify-content:center; flex:none;
  background: radial-gradient(circle at 40% 35%, rgba(255,120,140,.35), rgba(255,51,85,.08) 60%, transparent 70%);
  border:1px solid rgba(255,51,85,.55); box-shadow: 0 0 0 5px rgba(255,51,85,.06), 0 0 30px -6px rgba(255,51,85,.7); }
.tr-auth-lockup .mark svg { width:22px; height:22px; }
.tr-auth-lockup .name { font-size:24px; font-weight:800; letter-spacing:-.035em; line-height:1; color:#fff; }
.tr-auth-lockup .name em { font-style:normal; color:var(--tr-accent); }
.tr-auth-lockup .sub { font-family:var(--tr-mono); font-size:9.5px; letter-spacing:.32em; color:var(--tr-text-3); margin-top:6px; }
.tr-auth-status { display:flex; align-items:center; gap:18px; font-family:var(--tr-mono); font-size:10.5px; letter-spacing:.22em; color:var(--tr-text-3); }
.tr-auth-status .live { display:inline-flex; align-items:center; gap:8px; color:#9FEBC9; }
.tr-auth-status .live i { width:7px; height:7px; border-radius:50%; background:#3ED598; box-shadow:0 0 0 3px rgba(62,213,152,.18), 0 0 12px rgba(62,213,152,.8); animation: tr-auth-live 2.2s ease-in-out infinite; }
.tr-auth-status .sep { width:1px; height:12px; background:rgba(255,255,255,.14); }

/* ── the cinematic zone ────────────────────────────────────────────── */
.tr-auth-visual { --tr-copy-top:412px; position:relative; min-height:860px; isolation:isolate; overflow:hidden; padding:var(--tr-copy-top) 24px 170px 8px; animation: tr-auth-fade 1s var(--tr-ease) both; }
/* the projector: one cone of light from the top right, across the room */
.tr-auth-visual .beam { position:absolute; right:-12%; top:-16%; width:96%; height:110%; z-index:0; pointer-events:none; opacity:.9;
  background: conic-gradient(from 197deg at 94% 8%, transparent 0deg, rgba(255,107,133,.13) 14deg, rgba(255,51,85,.05) 30deg, rgba(255,51,85,.015) 44deg, transparent 56deg); filter: blur(14px); }
.tr-auth-visual .glow { position:absolute; left:-18%; bottom:-10%; width:70%; height:50%; z-index:0; pointer-events:none;
  background: radial-gradient(closest-side, rgba(255,51,85,.24), rgba(255,51,85,.05) 55%, transparent 75%); filter: blur(12px); animation: tr-auth-breathe 8s ease-in-out infinite; }
/* the radar's sweep, continued across the room: the dish scans the posters */
.tr-auth-visual .scan { position:absolute; left:165px; top:185px; width:1500px; height:1500px; margin:-750px 0 0 -750px; border-radius:50%; z-index:1; pointer-events:none; opacity:.9;
  background: conic-gradient(from 0deg, transparent 0deg 286deg, rgba(255,51,85,.035) 330deg, rgba(255,51,85,.10) 359deg, transparent 360deg);
  animation: tr-radar-sweep 6.5s linear infinite; }
/* the film strip: one curve that ties the radar to the posters */
.tr-auth-strip { position:absolute; right:8px; top:0; width:600px; height:470px; z-index:1; pointer-events:none; }
.tr-auth-strip svg { position:absolute; left:-460px; top:-110px; width:1180px; height:760px; overflow:visible; display:block; }
.tr-auth-strip .halo { fill:none; stroke:rgba(255,51,85,.06); stroke-width:118; }
.tr-auth-strip .band { fill:none; stroke:#1A1219; stroke-width:66; }
.tr-auth-strip .holes { fill:none; stroke:rgba(255,120,145,.11); stroke-width:66; stroke-dasharray:7 15; }
.tr-auth-strip .film { fill:none; stroke:#0F0B11; stroke-width:48; }
.tr-auth-strip .frames { fill:none; stroke:rgba(255,255,255,.05); stroke-width:48; stroke-dasharray:1 118; }
/* an aperture, out of focus, in the dark on the right */
.tr-auth-reel { position:absolute; right:-170px; bottom:40px; width:520px; height:520px; z-index:0; pointer-events:none; opacity:.05; color:#FF8CA0; }
.tr-auth-reel svg { width:100%; height:100%; display:block; }
/* a row of seats: the house, in the foreground, fading into the floor */
.tr-auth-seats { position:absolute; left:-4%; right:-4%; bottom:0; z-index:2; display:flex; flex-direction:column; gap:10px; pointer-events:none; padding-bottom:6px;
  -webkit-mask-image: linear-gradient(180deg, #000 45%, transparent 100%); mask-image: linear-gradient(180deg, #000 45%, transparent 100%); }
.tr-auth-seats .row { display:flex; justify-content:center; gap:12px; }
.tr-auth-seats .row.back { transform:scale(.86); opacity:.6; }
.tr-auth-seats i { width:68px; height:44px; border-radius:16px 16px 6px 6px; flex:none; position:relative;
  background: linear-gradient(180deg,#3E0D1B 0%,#240811 50%,#12040A 100%); border-top:1px solid rgba(255,100,130,.42);
  box-shadow: inset 0 -12px 18px -12px #000, inset 0 1px 0 rgba(255,140,160,.12), 0 -8px 26px -18px rgba(255,51,85,.9); }
.tr-auth-seats i::after { content:""; position:absolute; left:8px; right:8px; bottom:-7px; height:9px; border-radius:0 0 5px 5px; background:#150409; border-top:1px solid rgba(255,90,120,.18); }
.tr-auth-seats .row.front i { width:84px; height:58px; }
.tr-auth-visual .floor { position:absolute; left:0; right:0; bottom:0; height:220px; z-index:1; pointer-events:none; background: linear-gradient(180deg, transparent, rgba(7,7,10,.75) 80%); }

/* the radar, front and left; .scan above is centred on it */
.tr-auth-visual .tr-radar.hero { position:absolute; left:0; top:20px; z-index:2; animation: tr-auth-rise 1s .1s var(--tr-ease) both; }
.tr-auth-visual .tr-radar.hero .tag { left:2%; right:auto; top:auto; bottom:-6%; animation: tr-auth-rise .8s .7s var(--tr-ease) both; }
.tr-auth-visual .tr-radar.hero .tag::before { left:50%; right:auto; top:-9px; width:1px; height:8px; }

/* the posters: six at three depths, hung along the strip */
.tr-auth-posters { position:absolute; right:8px; top:0; width:600px; height:470px; z-index:3; perspective:1600px; }
.tr-auth-posters .hand { position:absolute; inset:0; transform: rotateY(-6deg) rotateX(2deg); transform-style:preserve-3d; transform-origin:60% 50%; }
.tr-auth-poster { position:absolute; border-radius:12px; overflow:hidden; background:#150B10; border:1px solid rgba(255,255,255,.14);
  box-shadow: 0 30px 60px -22px rgba(0,0,0,1), 0 0 0 1px rgba(255,51,85,.05), 0 14px 40px -26px rgba(255,51,85,.6);
  transform: rotate(var(--rot)) translateY(0); transition: transform .5s var(--tr-ease), box-shadow .5s var(--tr-ease), filter .5s var(--tr-ease);
  /* "backwards", not "both": a filled animation would keep holding transform and beat :hover's lift */
  animation: tr-auth-deal 1s var(--delay, 0s) var(--tr-ease) backwards; filter: brightness(var(--dim, 1)); }
.tr-auth-poster img { position:absolute; inset:0; width:100%; height:100%; object-fit:cover; object-position:50% 20%; display:block; }
.tr-auth-poster::before { content:""; position:absolute; inset:0; z-index:1; pointer-events:none; border-radius:inherit;
  background: linear-gradient(160deg, rgba(255,255,255,.14), rgba(255,255,255,0) 38%, rgba(0,0,0,0) 62%, rgba(0,0,0,.35)); }
.tr-auth-poster::after { content:""; position:absolute; inset:0; z-index:1; pointer-events:none; background: linear-gradient(180deg, transparent 58%, rgba(6,6,9,.9)); }
.tr-auth-poster .t { position:absolute; left:11px; right:11px; bottom:10px; z-index:2; font-family:var(--tr-mono); font-size:9.5px; letter-spacing:.16em; text-transform:uppercase; color:rgba(255,255,255,.88);
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; text-shadow:0 1px 8px #000; }
.tr-auth-poster:hover { transform: rotate(var(--rot)) translateY(-10px) scale(1.035); z-index:9 !important; filter:brightness(1.05);
  box-shadow: 0 40px 70px -24px rgba(0,0,0,1), 0 0 0 1px rgba(255,51,85,.25), 0 24px 60px -24px rgba(255,51,85,.9); }
.tr-auth-poster.lead .sheen { position:absolute; inset:-40% -60%; z-index:1; pointer-events:none;
  background: linear-gradient(115deg, transparent 42%, rgba(255,255,255,.13) 50%, transparent 58%); animation: tr-auth-sheen 9s 2s ease-in-out infinite; }
.tr-auth-posters .credit { position:absolute; right:0; bottom:-26px; font-family:var(--tr-mono); font-size:10px; letter-spacing:.3em; color:var(--tr-text-4); white-space:nowrap; }

/* headline & the four promises */
.tr-auth-copy { position:relative; z-index:2; max-width:none; animation: tr-auth-rise .9s .2s var(--tr-ease) both; }
.tr-auth-copy .eyebrow { font-family:var(--tr-mono); font-size:10.5px; letter-spacing:.34em; color:#FF8CA0; margin-bottom:16px; display:flex; align-items:center; gap:12px; }
.tr-auth-copy .eyebrow::before { content:""; width:28px; height:1px; background:rgba(255,107,133,.7); }
.tr-auth-copy h1 { font-size:60px; line-height:1; letter-spacing:-.04em; font-weight:800; color:#fff; margin:0; }
.tr-auth-copy h1 em { font-style:normal; color:var(--tr-accent); }
.tr-auth-copy .lede { font-size:18px; line-height:1.55; color:var(--tr-text-2); margin:20px 0 0; max-width:560px; font-weight:500; }
.tr-auth-feats { display:flex; gap:8px; margin-top:26px; flex-wrap:nowrap; }
.tr-auth-feat { display:inline-flex; align-items:center; gap:8px; padding:7px 12px 7px 8px; border-radius:999px; border:1px solid rgba(255,255,255,.10);
  background: linear-gradient(180deg, rgba(255,255,255,.05), rgba(255,255,255,.02)); font-family:var(--tr-mono); font-size:10px; letter-spacing:.16em; color:var(--tr-text-2);
  transition: border-color .25s var(--tr-ease), transform .25s var(--tr-ease), background .25s var(--tr-ease); }
.tr-auth-feat .ic { width:22px; height:22px; border-radius:50%; display:flex; align-items:center; justify-content:center; color:#FF6B85; background:rgba(255,51,85,.10); border:1px solid rgba(255,51,85,.35); }
.tr-auth-feat:hover { border-color:rgba(255,51,85,.45); transform:translateY(-2px); background: linear-gradient(180deg, rgba(255,51,85,.10), rgba(255,51,85,.03)); }
.tr-auth-mark { position:absolute; left:0; right:0; bottom:22px; z-index:3; font-family:var(--tr-mono); font-size:10.5px; letter-spacing:.34em; line-height:1.9; color:var(--tr-text-3); text-align:center; text-shadow:0 2px 14px #000; }

/* ── the panel ─────────────────────────────────────────────────────── */
[class*="st-key-trauth_panel"] {
  position:relative; z-index:3; max-width:560px; margin-left:auto; padding:34px 34px 30px !important; border-radius:26px;
  background: linear-gradient(168deg, #17131A 0%, #0F0D12 52%, #120E14 100%);
  border:1px solid rgba(255,255,255,.10);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.09), 0 50px 100px -40px rgba(0,0,0,1), 0 0 0 1px rgba(255,51,85,.05), 0 30px 90px -50px rgba(255,51,85,.55);
  /* opacity only, and never "both": a transform here — even the identity
     matrix a finished rise leaves behind — would pin the fixed veil inside
     the panel, and a filled animation makes the panel a backdrop root that
     stops the veil's blur at its edge. "backwards" lets the effect end. */
  animation: tr-auth-fade .9s .1s var(--tr-ease) backwards; gap:.55rem;
}
[class*="st-key-trauth_panel"]::before { content:""; position:absolute; inset:0; border-radius:26px; pointer-events:none;
  background: radial-gradient(560px 240px at 88% -4%, rgba(255,51,85,.16), transparent 60%), radial-gradient(400px 200px at 0% 104%, rgba(255,51,85,.07), transparent 60%); }
[class*="st-key-trauth_panel"]::after { content:""; position:absolute; left:14%; right:14%; top:-1px; height:1px; pointer-events:none;
  background: linear-gradient(90deg, transparent, rgba(255,140,160,.75), transparent); }
.tr-auth-brand { display:flex; align-items:center; gap:18px; justify-content:center; padding:2px 0 22px; animation: tr-auth-rise .9s .15s var(--tr-ease) both; }
.tr-auth-brand .lock { display:flex; flex-direction:column; align-items:center; gap:10px; }
.tr-auth-brand .lock .tr-radar { width:52px; filter: drop-shadow(0 0 14px rgba(255,51,85,.55)); }
.tr-auth-brand .lock .n { font-size:21px; font-weight:800; letter-spacing:.06em; color:#fff; }
.tr-auth-brand .lock .n em { font-style:normal; color:var(--tr-accent); }
.tr-auth-brand .sep { width:1px; height:64px; background:rgba(255,255,255,.14); }
.tr-auth-brand .tag { font-family:var(--tr-mono); font-size:10.5px; letter-spacing:.34em; line-height:1.75; color:var(--tr-text-3); }
.tr-auth-h { font-size:34px; font-weight:800; letter-spacing:-.035em; line-height:1.1; color:#fff; margin:0; }
.tr-auth-p { font-size:15.5px; line-height:1.55; color:var(--tr-text-2); margin:10px 0 18px; font-weight:500; }
.tr-auth-label { font-family:var(--tr-mono); font-size:10px; letter-spacing:.18em; text-transform:uppercase; color:var(--tr-text-3); margin:6px 0 -4px 2px; }

/* inputs: a glyph on the left, a sunken charcoal field, a red ring on focus */
[class*="st-key-trauth_panel"] .stTextInput [data-baseweb="input"],
[class*="st-key-trauth_panel"] .stTextInput [data-baseweb="base-input"] { min-height:56px; border-radius:14px !important; position:relative;
  background: linear-gradient(180deg, rgba(255,255,255,.045), rgba(255,255,255,.025)) !important; border-color: rgba(255,255,255,.11) !important;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.04), inset 0 2px 6px rgba(0,0,0,.35); transition: border-color .18s var(--tr-ease), box-shadow .18s var(--tr-ease); }
[class*="st-key-trauth_panel"] .stTextInput [data-baseweb="input"]:focus-within { border-color: rgba(255,51,85,.65) !important;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.04), 0 0 0 3px rgba(255,51,85,.16), 0 8px 24px -14px rgba(255,51,85,.8); }
[class*="st-key-trauth_panel"] .stTextInput [data-baseweb="input"]::before { content:""; position:absolute; left:17px; top:50%; width:20px; height:20px; transform:translateY(-50%);
  background-repeat:no-repeat; background-size:20px 20px; opacity:.8; pointer-events:none; transition: opacity var(--tr-fast) var(--tr-ease); }
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

/* the CTA: a glossy red key, and the Google button in the same box */
[class*="st-key-trauth_panel"] .stFormSubmitButton > button, [class*="st-key-trauth_panel"] .stFormSubmitButton > button[kind*="primary"] {
  position:relative; overflow:hidden; background: linear-gradient(180deg,#FF6E88 0%,#FF3355 50%,#D6133F 100%); color:#fff; border:1px solid rgba(255,140,160,.5);
  font-size:15px; font-weight:800; letter-spacing:.14em; text-transform:uppercase; min-height:60px; border-radius:16px; margin-top:8px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.45), inset 0 -2px 0 rgba(0,0,0,.28), 0 20px 44px -16px rgba(255,51,85,.95), 0 0 0 4px rgba(255,51,85,.08);
  flex-direction: row-reverse; gap:12px; transition: transform .22s var(--tr-ease), box-shadow .22s var(--tr-ease), filter .22s var(--tr-ease); }
[class*="st-key-trauth_panel"] .stFormSubmitButton > button::before { content:""; position:absolute; left:1px; right:1px; top:1px; height:48%; border-radius:15px 15px 40% 40%; pointer-events:none;
  background: linear-gradient(180deg, rgba(255,255,255,.30), rgba(255,255,255,.04)); }
[class*="st-key-trauth_panel"] .stFormSubmitButton > button:hover { transform:translateY(-2px); filter:brightness(1.06); color:#fff; border-color: rgba(255,160,175,.65);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.5), inset 0 -2px 0 rgba(0,0,0,.28), 0 26px 54px -16px rgba(255,51,85,1), 0 0 0 5px rgba(255,51,85,.11); }
[class*="st-key-trauth_panel"] .stFormSubmitButton > button:active { transform:translateY(0) scale(.99); filter:brightness(.97);
  box-shadow: inset 0 2px 4px rgba(0,0,0,.35), 0 12px 30px -16px rgba(255,51,85,.8), 0 0 0 4px rgba(255,51,85,.08); }
[class*="st-key-trauth_panel"] .stFormSubmitButton > button p { font-size:15px; font-weight:800; letter-spacing:.14em; position:relative; }
[class*="st-key-trauth_panel"] .stFormSubmitButton > button [data-testid="stIconMaterial"] { font-size:22px; position:relative; }
[class*="st-key-auth_google"] .stButton > button, a.tr-auth-google { position:relative; overflow:hidden; min-height:58px; border-radius:16px; font-size:15.5px; font-weight:700; letter-spacing:0; text-transform:none; gap:12px;
  background: linear-gradient(180deg, rgba(255,255,255,.085), rgba(255,255,255,.035)); border:1px solid rgba(255,255,255,.14); color:#fff;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.10), 0 14px 30px -18px rgba(0,0,0,.9); }
[class*="st-key-auth_google"] .stButton > button::before, a.tr-auth-google::before { content:""; width:20px; height:20px; flex:none; background: url("data:image/svg+xml;utf8,__GOOGLE__") no-repeat center / 20px 20px; }
[class*="st-key-auth_google"] .stButton > button:hover, a.tr-auth-google:hover { border-color: rgba(255,255,255,.3); background: linear-gradient(180deg, rgba(255,255,255,.12), rgba(255,255,255,.05)); transform:translateY(-1px); }
[class*="st-key-auth_google"] .stButton > button p { font-size:15.5px; font-weight:700; }
/* The live Google control is a real link — opened in a new tab, the one
   navigation the host's sandbox allows — drawn as the very same button:
   same box, same face, same hover. */
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

/* the wait: a line in the panel at once; the room dims only if it drags on */
.tr-auth-busy { display:flex; align-items:center; justify-content:center; gap:12px; margin-top:12px; padding:10px 14px; border-radius:14px; font-family:var(--tr-mono); font-size:12px; letter-spacing:.24em; color:#FF8CA0;
  background: rgba(255,51,85,.08); border:1px solid rgba(255,51,85,.3); animation: tr-auth-fade .2s var(--tr-ease) both; }
.tr-auth-busy .tr-radar { width:26px; flex:none; }
.tr-auth-veil { position:fixed; inset:0; z-index:100000; display:flex; align-items:center; justify-content:center; pointer-events:none;
  background: radial-gradient(900px 600px at 50% 50%, rgba(20,10,14,.55), rgba(6,6,9,.78)); backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
  opacity:0; animation: tr-auth-fade .35s .6s var(--tr-ease) forwards; }
.tr-auth-veil .box { display:flex; flex-direction:column; align-items:center; gap:18px; padding:34px 44px 30px; border-radius:24px;
  background: linear-gradient(168deg, rgba(23,19,26,.96), rgba(15,13,18,.98)); border:1px solid rgba(255,255,255,.10);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.08), 0 50px 100px -40px rgba(0,0,0,1), 0 30px 90px -50px rgba(255,51,85,.6); }
.tr-auth-veil .msg { font-family:var(--tr-mono); font-size:12.5px; letter-spacing:.3em; color:#FF8CA0; text-align:center; }
.tr-auth-veil .sub { font-family:var(--tr-mono); font-size:10px; letter-spacing:.3em; color:var(--tr-text-4); }
.tr-auth-done { display:flex; align-items:center; justify-content:center; gap:12px; margin-top:12px; padding:12px 14px; border-radius:14px; font-family:var(--tr-mono); font-size:12px; letter-spacing:.24em; color:#9FEBC9;
  background: rgba(62,213,152,.10); border:1px solid rgba(62,213,152,.4); animation: tr-auth-rise .3s var(--tr-ease) both; }
.tr-auth-done .tr-radar { width:26px; flex:none; }
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
  position:relative; overflow:hidden; background: linear-gradient(180deg,#FF6E88 0%,#FF3355 50%,#D6133F 100%); color:#fff; border:1px solid rgba(255,140,160,.5);
  font-size:13.5px; font-weight:800; letter-spacing:.12em; text-transform:uppercase; min-height:54px; border-radius:16px; margin-top:6px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.45), inset 0 -2px 0 rgba(0,0,0,.28), 0 20px 44px -16px rgba(255,51,85,.95); }
[class*="st-key-auth_resend"] .stButton > button::before { content:""; position:absolute; left:1px; right:1px; top:1px; height:48%; border-radius:15px 15px 40% 40%; pointer-events:none;
  background: linear-gradient(180deg, rgba(255,255,255,.30), rgba(255,255,255,.04)); }
[class*="st-key-auth_resend"] .stButton > button:hover { filter:brightness(1.06); color:#fff; }
[class*="st-key-auth_resend"] .stButton > button:disabled { opacity:.55; transform:none; box-shadow:none; cursor:default; }
[class*="st-key-auth_resend"] .stButton > button:disabled::before { display:none; }
[class*="st-key-auth_resend"] .stButton > button p { font-size:13.5px; font-weight:800; letter-spacing:.12em; position:relative; }
[class*="st-key-auth_verify_back"] .stButton > button { min-height:50px; border-radius:16px; font-size:13px; font-weight:700; letter-spacing:.1em; text-transform:uppercase;
  background: linear-gradient(180deg, rgba(255,255,255,.07), rgba(255,255,255,.03)); border:1px solid rgba(255,255,255,.14); color:var(--tr-text); }
[class*="st-key-auth_verify_back"] .stButton > button p { font-size:13px; font-weight:700; letter-spacing:.1em; }

/* ── the phone header, hidden on a desktop ─────────────────────────── */
.tr-auth-mhead { display:none; }

/* ── foot ──────────────────────────────────────────────────────────── */
.tr-auth-bottom { position:relative; z-index:2; display:flex; align-items:center; justify-content:space-between; gap:20px; padding:18px 2px 0; margin-top:8px; border-top:1px solid rgba(255,255,255,.07); animation: tr-auth-fade 1s .3s var(--tr-ease) both; }
.tr-auth-bottom .items { display:flex; gap:28px; flex-wrap:wrap; }
.tr-auth-bottom .item { display:inline-flex; align-items:center; gap:9px; font-size:13.5px; color:var(--tr-text-2); font-weight:600; }
.tr-auth-bottom .item svg { color:#FF5573; }
.tr-auth-bottom .sign { font-family:var(--tr-mono); font-size:12px; letter-spacing:.3em; color:var(--tr-text-3); white-space:nowrap; }
.tr-auth-bottom .sign em { font-style:normal; color:var(--tr-accent); }

/* ── motion ────────────────────────────────────────────────────────── */
@keyframes tr-auth-rise { from { opacity:0; transform:translateY(16px); } to { opacity:1; transform:none; } }
@keyframes tr-auth-fade { from { opacity:0; } to { opacity:1; } }
@keyframes tr-auth-breathe { 0%,100% { opacity:.8; transform:scale(1); } 50% { opacity:1; transform:scale(1.06); } }
@keyframes tr-auth-deal { from { opacity:0; transform: rotate(var(--rot)) translateY(34px); } to { opacity:1; transform: rotate(var(--rot)) translateY(0); } }
@keyframes tr-auth-sheen { 0%, 70% { transform: translateX(-60%); } 100% { transform: translateX(60%); } }
@keyframes tr-auth-live { 0%,100% { opacity:1; } 50% { opacity:.45; } }
__RADAR__
__CRAFTS__
@media (prefers-reduced-motion: reduce) {
  [class*="st-key-trauth_shell"] *, [class*="st-key-trauth_shell"] *::before, [class*="st-key-trauth_shell"] *::after { animation: none !important; transition: none !important; }
  .tr-auth-veil { opacity:1; }
}

/* ── laptops: the hand of posters scales down, the radar too ──────── */
@media (max-width: 1440px) {
  .tr-auth-posters, .tr-auth-strip { transform: scale(.82); transform-origin: 100% 0; }
  .tr-auth-visual { --tr-copy-top:372px; min-height:820px; }
  .tr-auth-feats { flex-wrap:wrap; }
  .tr-auth-visual .tr-radar.hero { width:300px !important; }
  .tr-auth-visual .scan { left:150px; top:170px; }
}
@media (max-width: 1280px) {
  .tr-auth-posters, .tr-auth-strip { transform: scale(.72); }
  .tr-auth-visual { --tr-copy-top:340px; min-height:780px; }
  .tr-auth-visual .tr-radar.hero { width:270px !important; }
  .tr-auth-visual .scan { left:135px; top:155px; }
  .tr-auth-copy h1 { font-size:52px; }
}
/* ── tablets: the room keeps its radar and headline; the hand and the
   constellation step aside so nothing crowds the form ──────────────── */
@media (max-width: 1100px) {
  .block-container { padding: .9rem 1.4rem 1.4rem !important; }
  .tr-auth-posters, .tr-auth-strip, .tr-auth-reel, .tr-crafts, .tr-auth-visual .beam { display:none; }
  .tr-auth-visual { --tr-copy-top:300px; min-height:700px; padding:var(--tr-copy-top) 12px 170px 4px; }
  .tr-auth-visual .scan { left:125px; top:145px; width:1000px; height:1000px; margin:-500px 0 0 -500px; }
  .tr-auth-seats .row.back { display:none; }
  .tr-auth-visual .tr-radar.hero { width:250px !important; }
  .tr-auth-copy { max-width:none; }
  .tr-auth-copy h1 { font-size:44px; }
  .tr-auth-copy .lede { font-size:16px; }
  .tr-auth-status .city, .tr-auth-status .sep { display:none; }
  [class*="st-key-trauth_panel"] { padding:26px 24px 24px !important; }
}

/* ── phones: a deliberate stack — brand, a short hero, three posters,
   the form. The desktop room is not squeezed; it is gone ───────────── */
@media (max-width: 768px) {
  .block-container { padding: .9rem 1rem 2rem !important; }
  [data-testid="stMainBlockContainer"] { padding-top: .4rem !important; }
  .tr-auth-top { padding:2px 0 10px; }
  .tr-auth-status { display:none; }
  .tr-auth-lockup .mark { width:40px; height:40px; }
  .tr-auth-lockup .name { font-size:21px; }
  .tr-auth-lockup .sub { font-size:8.5px; letter-spacing:.26em; }
  [class*="st-key-trauth_shell"] > div > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child { display:none !important; }
  .tr-auth-mhead { display:block; position:relative; padding:12px 2px 18px; overflow:hidden; animation: tr-auth-rise .7s var(--tr-ease) both; }
  .tr-auth-mhead .eyebrow { font-family:var(--tr-mono); font-size:9.5px; letter-spacing:.3em; color:#FF8CA0; margin-bottom:10px; }
  .tr-auth-mhead h1 { font-size:clamp(30px, 8.6vw, 38px); line-height:1.02; letter-spacing:-.04em; font-weight:800; color:#fff; margin:0; max-width:62%; }
  .tr-auth-mhead h1 em { font-style:normal; color:var(--tr-accent); }
  .tr-auth-mhead .lede { font-size:14.5px; line-height:1.5; color:var(--tr-text-2); margin:10px 0 0; max-width:34ch; }
  .tr-auth-mhead .tr-radar.mini { position:absolute; right:2px; top:-2px; width:112px !important; opacity:.95; }
  .tr-auth-mstrip { position:relative; height:118px; margin:18px 0 0; }
  .tr-auth-mstrip .tr-auth-poster { position:absolute; width:72px; height:106px; border-radius:9px; animation:none; }
  .tr-auth-mstrip .tr-auth-poster .t { display:none; }
  .tr-auth-mstrip .tr-auth-poster:nth-child(1) { left:0; top:8px; --rot:-7deg; z-index:1; }
  .tr-auth-mstrip .tr-auth-poster:nth-child(2) { left:58px; top:0; --rot:-1deg; z-index:3; }
  .tr-auth-mstrip .tr-auth-poster:nth-child(3) { left:120px; top:9px; --rot:6deg; z-index:2; }
  .tr-auth-mstrip .credit { position:absolute; left:210px; top:34px; font-family:var(--tr-mono); font-size:9.5px; letter-spacing:.22em; line-height:1.9; color:var(--tr-text-3); }
  .tr-auth-mhead .tr-craft-strip { margin-top:14px; }
  [class*="st-key-trauth_panel"] { max-width:none; margin:0; padding:22px 18px 20px !important; border-radius:20px; }
  [class*="st-key-trauth_panel"]::before, [class*="st-key-trauth_panel"]::after { border-radius:20px; }
  .tr-auth-brand { gap:14px; padding-bottom:16px; }
  .tr-auth-brand .lock .tr-radar { width:44px; }
  .tr-auth-brand .lock .n { font-size:18px; }
  .tr-auth-brand .tag { font-size:9.5px; letter-spacing:.28em; }
  .tr-auth-h { font-size:27px; }
  .tr-auth-p { font-size:14.5px; margin-bottom:12px; }
  [class*="st-key-trauth_panel"] .stTextInput [data-baseweb="input"] input { font-size:16px !important; }   /* no iOS zoom */
  [class*="st-key-trauth_panel"] .stFormSubmitButton > button { min-height:56px; font-size:14px; }
  [class*="st-key-auth_google"] .stButton > button, a.tr-auth-google { min-height:54px; font-size:14.5px; }
  [class*="st-key-trpair_auth_foot"] [data-testid="stHorizontalBlock"] { flex-wrap:nowrap !important; }
  .tr-auth-foot-text { font-size:13.5px; }
  .tr-auth-veil .box { padding:26px 28px 24px; margin:0 16px; }
  .tr-auth-bottom { flex-direction:column; align-items:flex-start; gap:12px; padding-top:14px; }
  .tr-auth-bottom .items { gap:12px 18px; }
  .tr-auth-bottom .item { font-size:12.5px; }
  .tr-auth-bottom .sign { font-size:10.5px; letter-spacing:.24em; }
}
@media (max-width: 400px) {
  [class*="st-key-trauth_panel"] { padding:20px 15px 18px !important; }
  .tr-auth-brand { gap:10px; } .tr-auth-brand .sep { display:none; } .tr-auth-brand .tag { display:none; }
  .tr-auth-foot-text { font-size:13px; }
  .tr-auth-mhead .tr-radar.mini { width:96px !important; }
  .tr-auth-mstrip .credit { display:none; }
}
</style>
""".replace("__MAIL__", _input_icon_uri("mail")).replace("__LOCK__", _input_icon_uri("lock")) \
   .replace("__USER__", _input_icon_uri("user")).replace("__GOOGLE__", GOOGLE_G) \
   .replace("__RADAR__", radar.CSS).replace("__CRAFTS__", crafts.CSS)


# ──────────────────────────────────────────────────────────────────────────
# Markup
# ──────────────────────────────────────────────────────────────────────────
def _topbar() -> str:
    return f"""<div class="tr-auth-top">
      <div class="tr-auth-lockup">
        <div class="mark">{C.TICKET_GLYPH.replace('stroke="#fff"', 'stroke="#FF5573"')}</div>
        <div><div class="name">Ticket<em>Radar</em></div><div class="sub">MOVIES • ALERTS • YOU FIRST</div></div>
      </div>
      <div class="tr-auth-status">
        <span class="live"><i></i>RADAR ONLINE</span><span class="sep"></span>
        <span class="city">HYDERABAD</span><span class="sep"></span><span>24 / 7</span>
      </div>
    </div>"""


#: The six on a desktop: where each poster hangs in a 600×470 box —
#: (left, top, width, height, rotation°, z, brightness, deal delay s).
#: Three depths, not a fan: the lead (Avengers) is largest and in front;
#: Interstellar and Avatar flank it a step back; RRR, Baahubali and
#: Spider-Man sit lower and further back. Overlaps are corners only, so
#: every poster shows at least nine-tenths of itself. A seventh or eighth
#: poster in the folder takes an :data:`EXTRA_SLOTS` place at the back.
FAN = {
    "interstellar":            (44,  52, 118, 177, -10, 2, .82, .05),
    "rrr":                     (118, 224, 116, 174, -5, 3, .90, .30),
    "avengers_endgame":        (214, 24, 164, 246,  -2, 5, 1.0, .12),
    "avatar":                  (366, 84, 130, 195,   5, 4, .95, .20),
    "baahubali":               (276, 284, 112, 168,  3, 4, .88, .38),
    "spiderman_brand_new_day": (482, 236, 98, 147,   9, 3, .86, .46),
}
EXTRA_SLOTS = ((-30, 260, 96, 144, -16, 1, .7, .54), (500, 30, 90, 135, 12, 1, .7, .6))
LEAD = "avengers_endgame"
#: The three that fit a phone, front to back.
PHONE_POSTERS = ("rrr", "avengers_endgame", "interstellar")


#: A transparent pixel: what an ``<img>`` shows where its ``<picture>`` has
#: no source for this viewport, so nothing is fetched for a hidden card.
BLANK = "data:image/gif;base64,R0lGODlhAQABAAAAACw="
CAPTIONS = {"spiderman_brand_new_day": "Spider-Man"}


def _poster_card(asset, slot=None, *, media: str, lead: bool = False) -> str:
    """One card. ``slot`` is the hand's inline geometry; the phone strip
    positions its three by CSS instead. ``media`` is the viewport that
    shows this card — the only one that downloads it."""
    style = ""
    if slot:
        left, top, width, height, rot, z, dim, delay = slot
        style = (f' style="left:{left}px;top:{top}px;width:{width}px;height:{height}px;'
                 f'--rot:{rot}deg;z-index:{z};--dim:{dim};--delay:{delay}s"')
    src = art.static_url(asset)
    sheen = '<span class="sheen"></span>' if lead else ""
    caption = CAPTIONS.get(asset.key, asset.label)
    return (f'<div class="tr-auth-poster{" lead" if lead else ""}"{style}>'
            f'<picture><source media="{media}" srcset="{C.e(src)}">'
            f'<img src="{BLANK}" alt="" decoding="async"></picture>'
            f'{sheen}<div class="t">{C.e(caption)}</div></div>')


def _hand() -> str:
    """The six fixed posters, fanned. They are the brand's own files
    (``static/login/posters``), never the catalogue's, so the wall is the
    same on every visit and costs the browser one cached fetch each."""
    cards = []
    extras = 0
    for asset in art.posters():
        slot = FAN.get(asset.key)
        if slot is None:
            slot = EXTRA_SLOTS[extras % len(EXTRA_SLOTS)]
            extras += 1
        cards.append(_poster_card(asset, slot, media="(min-width: 1101px)", lead=asset.key == LEAD))
    return (f'<div class="tr-auth-posters"><div class="hand">{"".join(cards)}</div>'
            f'<div class="credit">NOW PLAYING ACROSS HYDERABAD</div></div>')


#: The curve the posters hang from — one path, stroked five times: a soft
#: red halo, the dark band, the sprocket holes (a dashed stroke that the
#: narrower film stroke covers except at the edges), the film, and faint
#: frame lines.
STRIP_PATH = "M-40 640 C 220 610, 330 330, 560 250 S 860 60, 1010 190 S 1160 520, 1080 800"


def _strip() -> str:
    return f"""<div class="tr-auth-strip" aria-hidden="true"><svg viewBox="0 0 1180 760">
      <path class="halo" d="{STRIP_PATH}"/><path class="band" d="{STRIP_PATH}"/>
      <path class="holes" d="{STRIP_PATH}"/><path class="film" d="{STRIP_PATH}"/>
      <path class="frames" d="{STRIP_PATH}"/>
    </svg></div>"""


#: A projector's aperture: a ring, six holes, a hub — drawn once, oversized.
REEL_HOLES = '<circle cx="318.0" cy="200.0" r="34"/><circle cx="259.0" cy="302.2" r="34"/><circle cx="141.0" cy="302.2" r="34"/><circle cx="82.0" cy="200.0" r="34"/><circle cx="141.0" cy="97.8" r="34"/><circle cx="259.0" cy="97.8" r="34"/>'


def _reel() -> str:
    return (f'<div class="tr-auth-reel" aria-hidden="true"><svg viewBox="0 0 400 400" fill="none" stroke="currentColor">'
            f'<circle cx="200" cy="200" r="190" stroke-width="14"/><circle cx="200" cy="200" r="160" stroke-width="1.5"/>'
            f'<g stroke-width="10">{REEL_HOLES}</g><circle cx="200" cy="200" r="30" stroke-width="12"/></svg></div>')


def _seats() -> str:
    """Two rows of the house, rim-lit from the screen."""
    back = "".join("<i></i>" for _ in range(11))
    front = "".join("<i></i>" for _ in range(9))
    return f'<div class="tr-auth-seats" aria-hidden="true"><div class="row back">{back}</div><div class="row front">{front}</div></div>'


def _phone_strip() -> str:
    cards = [_poster_card(a, media="(max-width: 768px)")
             for a in (art.poster(k) for k in PHONE_POSTERS) if a is not None]
    return (f'<div class="tr-auth-mstrip">{"".join(cards)}'
            f'<div class="credit">NOW PLAYING<br>ACROSS<br>HYDERABAD</div></div>')


def _feats() -> str:
    return "".join(
        f'<span class="tr-auth-feat"><span class="ic">{_icon(icon, 13, "currentColor", "1.9")}</span>{C.e(f"{a} {b}".upper())}</span>'
        for icon, a, b in PROMISES
    )


def _visual() -> str:
    """The room, back to front: the projector's cone and the floor glow, the
    radar's sweep continued across the room, the film strip, the aperture,
    the radar, the posters hung along the strip, the crafts in the dark,
    the headline, and the house's seats in the foreground."""
    return f"""<div class="tr-auth-visual">
      <div class="beam"></div><div class="glow"></div><div class="scan"></div>
      {_strip()}
      {_reel()}
      {radar.svg(size=330, state="scanning", tag="TICKETS<br>DETECTED", cls="hero", label="TicketRadar radar, scanning")}
      {_hand()}
      {crafts.constellation()}
      <div class="tr-auth-copy">
        <div class="eyebrow">HYDERABAD · EVERY SCREEN · EVERY RELEASE</div>
        <h1>Never Miss<br>the <em>Moment</em></h1>
        <p class="lede">We watch the ticket counters so you don't have to. The moment a show opens at your theatre, the alert is already in your inbox.</p>
        <div class="tr-auth-feats">{_feats()}</div>
      </div>
      {_seats()}
      <div class="floor"></div>
      <div class="tr-auth-mark">SOME MOVIES ARE MEANT<br>TO BE EXPERIENCED TOGETHER</div>
    </div>"""


def _mobile_head() -> str:
    return f"""<div class="tr-auth-mhead">
      {radar.svg(size=124, state="scanning", cls="mini", label="TicketRadar radar, scanning")}
      <div class="eyebrow">HYDERABAD · EVERY RELEASE</div>
      <h1>Never Miss<br>the <em>Moment</em></h1>
      <p class="lede">Instant email alerts the moment tickets go live.</p>
      {_phone_strip()}
      {crafts.strip()}
    </div>"""


def _brand() -> str:
    return f"""<div class="tr-auth-brand">
      <div class="lock">{radar.svg(size=52, state="idle", cls="mark brandmark", label="TicketRadar")}
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


def _returning_from_google() -> bool:
    """Is this run the one Google sent back — ``?code``, ``?state`` or
    ``?error`` on the URL? Nothing may touch the state cookie on that run."""
    try:
        params = st.query_params.to_dict()
    except Exception:  # noqa: BLE001 - bare mode
        return False
    return any(k in params for k in ("code", "state", "error"))


def prepare_google() -> None:
    """Mint this session's ``state`` and queue its cookie — called by the
    gate *before* the bridge renders, so the cookie exists by the time the
    link below is drawn. Nothing happens unless Google is configured.

    Never on the return from Google. That run is a brand-new session, so a
    mint here would queue a *fresh* state, and the bridge writes queued
    cookies before it reads the jar — the cookie the link was made with
    would be gone before ``handle_google_return`` could compare it, and
    every sign-in would end in "didn't complete". The return must compare
    against the cookie the click left behind, and only then spend it.
    """
    if google.is_configured() and not _returning_from_google():
        session.oauth_state()


def _google_button() -> None:
    """The one control on the page that is a link rather than a button.

    A Streamlit button reruns the script; it cannot send the browser to
    Google. And on Community Cloud the app itself runs inside the host's
    iframe, sandboxed with ``allow-popups allow-popups-to-escape-sandbox``
    but **without** ``allow-top-navigation`` (read off the deployed host's
    own iframe chunk) — so ``target="_top"`` is refused silently, while a
    plain ``target="_blank"`` on the person's own click opens an unsandboxed
    tab: no script, no popup blocker, Google loads there and returns to the
    app's URL top-level in that tab. The anchor is styled to be
    indistinguishable from the button it replaces. When Google is not
    configured the button stays, and says so honestly, exactly as before.
    """
    ready, redirect = google_ready()
    if not ready:
        st.button("Continue with Google", key="auth_google", use_container_width=True, on_click=_google_notice)
        return
    url = google.authorization_url(session.oauth_state(), redirect)
    # The bridge opens this in a popup from the click; ``target="_blank"`` is
    # what happens when the popup is blocked. No ``noopener``: the popup
    # needs its opener to say it has finished (see the bridge).
    with st.container(key="auth_google"):
        C.html(f'<a class="tr-auth-google" href="{escape(url, quote=True)}" target="_blank" '
               f'data-testid="tr-google-signin">Continue with Google</a>')


def popup_done() -> None:
    """The popup's last paint: signed in, the opener has been told, and the
    bridge is closing this window. One line in the page's own voice, in
    case the browser keeps the window open."""
    st.markdown(CSS, unsafe_allow_html=True)
    C.html(
        '<div class="tr-auth-shell" style="min-height:100vh;display:flex;align-items:center;justify-content:center;">'
        f'<div class="tr-auth-done" style="justify-content:center;">{_icon("check", 16, "#3ED598", "2.4")}'
        '<span>SIGNED IN — YOU CAN CLOSE THIS WINDOW</span></div></div>'
    )


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

    # Whatever happens next, this run is spent: the next one mints a fresh
    # state and the bridge writes it before any link is drawn, so a retry
    # from this window starts consistent rather than one cookie behind.
    if error and not code:
        print(f"[auth] google return: error={error[:40]}", flush=True)
        session.clear_oauth_state()
        st.session_state[NOTICE_KEY] = ("info", google.MESSAGES["cancelled"] if error == "access_denied"
                                        else google.MESSAGES["failed"])
        st.rerun()
        return None

    if not session.oauth_state_matches(state):
        print("[auth] google return: state mismatch - code not exchanged", flush=True)
        session.clear_oauth_state()
        st.session_state[ERROR_KEY] = google.MESSAGES["state"]
        st.rerun()
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
        st.rerun()
        return None
    popup = session.bridge_has_opener()
    if popup:
        # Opened from the login page: don't draw the app in here. The next
        # run writes the session cookie, tells the opener, and closes.
        st.session_state[session.POPUP_DONE_KEY] = True
    print(f"[auth] google return: signed in popup={str(popup).lower()}", flush=True)
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
    """The wait, in two beats from one paint: the line in the panel shows
    at once; the veil over the room is in the same markup but its CSS
    holds it invisible for 600 ms, so a quick answer never shows it and a
    slow one dims the page without a second render or a timer."""
    placeholder.markdown(C.clean_html(
        f'<div class="tr-auth-busy">{radar.svg(size=26, state="detecting", cls="mark busy", label="")}<span>{C.e(text)}</span></div>'
        f'<div class="tr-auth-veil" role="status" aria-live="polite"><div class="box">'
        f'{radar.svg(size=120, state="detecting", cls="veil", label="")}'
        f'<div class="msg">{C.e(text)}</div><div class="sub">ONE MOMENT</div></div></div>'
    ), unsafe_allow_html=True)


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
    placeholder.markdown(C.clean_html(
        f'<div class="tr-auth-done">{radar.svg(size=26, state="success", cls="mark done", label="")}'
        f'<span>SIGNED IN — WELCOME, {C.e(user.first_name.upper())}</span></div>'
    ), unsafe_allow_html=True)
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

        _attempt(status, "TUNING INTO THE RADAR…", go)


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
        # Said here so the person hears it without a round trip; enforced in
        # ``FirebaseAuth.sign_up`` regardless, before Firebase is asked.
        if disposable.is_disposable(email):
            _fail(firebase.MESSAGES["DISPOSABLE_EMAIL"])
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

        _attempt(status, "CONNECTING TO TICKETRADAR…", go)


def _send_failure(exc: AuthError, *, created: bool = False) -> str:
    """What to say when the sign-in service did not accept a VERIFY_EMAIL
    request. Names the code and status, because that is what fixes the
    project configuration; never a key, a token, an address — or the
    name of the service behind the page."""
    lead = "Your account was created, but " if created else ""
    return (f"{lead}the verification email couldn't be sent: {exc.code} "
            f"(HTTP {exc.status}). {exc}")


def _accepted(email: str) -> str:
    """Accepted is not delivered — say exactly that, without saying "Inbox"."""
    return (f"Verification email sent to {email}. Check your Inbox, Spam, or Promotions folder — "
            "the sender may not say TicketRadar.")


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
            st.session_state[ERROR_KEY] = ("The verification email couldn't be sent: "
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

        _attempt(status, "SENDING YOUR RESET LINK…", go)


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
