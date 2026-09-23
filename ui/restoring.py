"""The beat between opening TicketRadar and being back inside it.

A returning browser opens a brand-new Streamlit session, and the session
cookie only reaches Python through the browser bridge — one component round
trip and one more script run. On Community Cloud that is a few seconds, and
what filled them was a dark page, a skeleton bar and one line of text. It
read like something had failed.

This is the same few seconds, drawn on purpose: the radar that is the app's
own mark, sweeping; one cinematic line; then the honest one, *Restoring your
session…*; and a thin progress bar that never claims to be finished.

Nothing here waits. There is no timer, no fragment, no poll, no sleep and no
rerun asked for: the whole thing is one CSS animation on one block of markup,
and the moment the real restore answers, the gate renders the app instead and
this is gone — mid-sweep if that is where it is. The animation is the *cover*
for work already happening, never a reason for it to take longer. Its steps
are timed to be legible if the wait is long and to be skipped over entirely
if it is short, which is the good case.

Everything is ``transform`` and ``opacity`` — the compositor's two cheap
properties — so a slow phone spends its main thread on the restore, not on
this. ``prefers-reduced-motion`` stops the sweep and shows both lines at
once. No account data, nothing private: the radar and the brand are on the
login page too.
"""

from __future__ import annotations

import streamlit as st

from ui import components as C
from ui import radar

#: TicketRadar's own line. Not a quotation from anything — the app's voice,
#: saying what it is doing: the queue is the booking page it watches.
LEAD = "Waiting for the next showtime."
#: The plain one. It is what is actually happening, and it is what the
#: screen settles on for as long as the wait lasts.
STATUS = "Restoring your session…"


def markup() -> str:
    """The whole screen as one block of HTML, stylesheet included.

    One block on purpose: ``st.markdown`` is one element, and an element
    that appears on this run and is replaced by the app on the next is a
    clean swap rather than a pile of blocks the page has to reconcile.
    """
    return (
        CSS
        + '<div class="tr-boot" role="status" aria-live="polite" aria-label="Restoring your session">'
        + '<div class="tr-boot-stage">'
        + radar.svg(size=132, state="detecting", cls="boot", label="")
        + '</div>'
        + '<div class="tr-boot-eyebrow">TICKETRADAR</div>'
        + f'<div class="tr-boot-lines"><span class="l lead">{C.e(LEAD)}</span>'
        + f'<span class="l status">{C.e(STATUS)}</span></div>'
        + '<div class="tr-boot-bar"><span></span></div>'
        + '</div>'
    )


def screen() -> None:
    """Draw the beat: the brand in its usual place, the radar in the page."""
    with st.sidebar:
        C.logo()
    C.html(markup())


#: Folded into one payload with the radar's own rules, so the beat is a
#: single markdown element and there is nothing to lay out twice.
CSS = "<style>" + radar.CSS + """
.tr-boot { display:flex; flex-direction:column; align-items:center; justify-content:center; gap:0;
  min-height:min(62vh, 520px); padding:24px 16px 32px; text-align:center;
  animation: tr-boot-in .5s var(--tr-ease, cubic-bezier(.2,.7,.3,1)) both; }
.tr-boot-stage { position:relative; width:132px; height:132px; display:flex; align-items:center; justify-content:center; }
/* the dark the radar sits in — one soft pool of the brand's red, nothing moving */
.tr-boot-stage::before { content:""; position:absolute; inset:-46%; border-radius:50%; pointer-events:none;
  background:radial-gradient(circle at 50% 50%, rgba(255,51,85,.16), rgba(255,51,85,.04) 46%, transparent 70%); }
.tr-boot-stage .tr-radar { position:relative; }
.tr-boot-eyebrow { font-family:var(--tr-mono, ui-monospace, monospace); font-size:10px; letter-spacing:.42em;
  color:#FF8CA0; opacity:.75; margin-top:22px; padding-left:.42em; }
/* the two lines occupy one slot, so the second does not push the page */
.tr-boot-lines { position:relative; display:grid; width:100%; margin-top:12px; min-height:26px; }
.tr-boot-lines .l { grid-area:1/1; font-size:15.5px; font-weight:600; letter-spacing:-.012em;
  color:var(--tr-text-2, #C9C9D2); line-height:1.5; }
.tr-boot-lines .lead { animation: tr-boot-lead 4.6s var(--tr-ease, cubic-bezier(.2,.7,.3,1)) both; }
.tr-boot-lines .status { animation: tr-boot-status 4.6s var(--tr-ease, cubic-bezier(.2,.7,.3,1)) both; }
/* a bar that fills fast and then only creeps: it never pretends to be done */
.tr-boot-bar { width:min(212px, 62vw); height:2px; margin-top:20px; border-radius:2px; overflow:hidden;
  background:rgba(255,255,255,.07); }
.tr-boot-bar span { display:block; height:100%; width:100%; border-radius:2px; transform-origin:left center;
  background:linear-gradient(90deg, rgba(255,51,85,.25), #FF6B85 65%, #FF3355);
  animation: tr-boot-bar 9s cubic-bezier(.16,.9,.3,1) both; }
@keyframes tr-boot-in { from { opacity:0; transform:translateY(8px); } to { opacity:1; transform:none; } }
@keyframes tr-boot-lead { 0% { opacity:0; transform:translateY(6px); } 12%,46% { opacity:1; transform:none; }
  60%,100% { opacity:0; transform:translateY(-6px); } }
@keyframes tr-boot-status { 0%,52% { opacity:0; transform:translateY(6px); } 66%,100% { opacity:1; transform:none; } }
@keyframes tr-boot-bar { 0% { transform:scaleX(.04); } 35% { transform:scaleX(.55); }
  70% { transform:scaleX(.82); } 100% { transform:scaleX(.94); } }
@media (max-width: 768px) {
  .tr-boot { min-height:56vh; padding-top:12px; }
  .tr-boot-stage { width:106px; height:106px; }
  .tr-boot-stage .tr-radar { width:106px !important; }
  .tr-boot-lines .l { font-size:14px; }
}
@media (prefers-reduced-motion: reduce) {
  .tr-boot, .tr-boot-bar span { animation:none !important; }
  .tr-boot-lines { min-height:0; }
  .tr-boot-lines .l { grid-area:auto; animation:none !important; opacity:1 !important; transform:none !important; }
  .tr-boot-lines .lead { color:var(--tr-text-3, #8E8E98); }
  .tr-boot-bar span { transform:scaleX(.6); }
}
</style>"""

__all__ = ["CSS", "LEAD", "STATUS", "markup", "screen"]
