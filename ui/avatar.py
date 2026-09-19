"""The person's TicketRadar face: a character they chose, or their initial.

A preference, not an identity. Firebase still says who somebody is; this
only decides what the account chip shows. The choice is one small field,
``avatar_key``, on the same ``users/{uid}`` document the app already reads
as its settings — so showing it costs no extra Firestore read, and it is
written through the same :func:`monitor.state.save_settings` the
notification email uses, once, and only when the key actually changed.

The value is never trusted on its own. :func:`chosen` hands it to
``assets_registry.resolve_avatar``, which answers with one of our own
files or nothing at all; a path, a URL, a scheme or a name we don't have
all fall back to the initial. Google's ``photo_url`` is a different thing
(provider data on :class:`auth.session.AuthUser`) and is neither read nor
written here.

The picker is a dialog opened from the account menu. Its thumbnails are
static files the browser caches, and it renders nothing until it is
opened. Choosing is local (``avatar_draft`` in session state); *Save* is
the one write.
"""

from __future__ import annotations

from typing import Callable

import streamlit as st

from monitor.state import save_settings
from ui import assets_registry as art
from ui import components as C
from ui import flow

#: The field on ``users/{uid}``. Empty or absent means "the initial".
SETTING = "avatar_key"
OPEN_KEY = "avatar_picker_open"
DRAFT_KEY = "avatar_draft"
_WARNED = "avatar_key_warned"
PER_ROW = 7

CATEGORY_TAGLINES = {
    "animation": "Animated favourites",
    "cartoon": "Doraemon and friends",
    "marvel": "Earth's mightiest",
    "dc": "Gotham's finest",
    "tollywood": "Tollywood",
}


# ── reading ───────────────────────────────────────────────────────────────
def chosen(settings: dict) -> art.Asset | None:
    """The avatar the settings name — validated — or None for the initial."""
    raw = settings.get(SETTING, "") if isinstance(settings, dict) else ""
    if not raw:
        return None
    asset = art.resolve_avatar(raw)
    if asset is None and not st.session_state.get(_WARNED):
        # Once per session, and never the value: it is the one thing on
        # the document a person could have typed.
        st.session_state[_WARNED] = True
        print(f"[avatar] ignoring an unregistered avatar_key ({type(raw).__name__}, "
              f"{len(raw) if isinstance(raw, str) else '-'} chars); showing the initial", flush=True)
    return asset


def current_key(settings: dict) -> str:
    asset = chosen(settings)
    return asset.key if asset else ""


def _initial(user) -> str:
    return (getattr(user, "first_name", "") or "")[:1].upper() or "·"


def mark(user, settings: dict, *, cls: str = "av") -> str:
    """The identity element: the chosen character, else the initial —
    the very markup the chip has always drawn for the initial."""
    asset = chosen(settings)
    if asset is None:
        return f'<div class="{cls}">{C.e(_initial(user))}</div>'
    return (f'<div class="{cls} has-avatar"><img src="{C.e(art.static_url(asset))}" '
            f'alt="{C.e(asset.label)}" loading="lazy" decoding="async"></div>')


# ── the picker's state ────────────────────────────────────────────────────
def open_picker(settings: dict) -> None:
    """An ``on_click``: open the dialog with the current choice as the draft."""
    st.session_state[OPEN_KEY] = True
    st.session_state[DRAFT_KEY] = current_key(settings)


def is_open() -> bool:
    return bool(st.session_state.get(OPEN_KEY))


def _choose(key: str) -> None:
    st.session_state[DRAFT_KEY] = key


def _close() -> None:
    st.session_state.pop(OPEN_KEY, None)
    st.session_state.pop(DRAFT_KEY, None)


def save(settings: dict, key: str, *, mirror: bool) -> bool:
    """Persist ``key`` if it differs from what is stored. Returns whether a
    write happened. Anything but a registered key (or "" for the initial)
    is refused here too, so the document can never hold a bad value."""
    if key and not art.is_avatar_key(key):
        return False
    if key == current_key(settings):
        return False
    save_settings({**settings, SETTING: key}, mirror=mirror)
    return True


# ── the picker ────────────────────────────────────────────────────────────
def _tile(asset: art.Asset | None, user, selected: bool) -> str:
    if asset is None:
        face = f'<span class="initial">{C.e(_initial(user))}</span>'
        name = "Your initial"
    else:
        face = (f'<img src="{C.e(art.static_url(asset))}" alt="" loading="lazy" decoding="async">')
        name = asset.label
    check = f'<span class="ok">{C.icon("check", 11, "#fff", "2.6")}</span>'
    return (f'<div class="tr-avt{" selected" if selected else ""}">'
            f'<div class="ring">{face}{check}</div><div class="n">{C.e(name)}</div></div>')


def _preview(user, draft: str) -> str:
    asset = art.resolve_avatar(draft)
    face = (f'<img src="{C.e(art.static_url(asset))}" alt="" decoding="async">' if asset
            else f'<span class="initial">{C.e(_initial(user))}</span>')
    what = f"{asset.label} · {art.label_for(asset.category)}" if asset else "Your initial — no character yet"
    return f"""<div class="tr-avp-head">
      <div class="big">{face}</div>
      <div><div class="eyebrow">YOUR TICKETRADAR AVATAR</div>
      <div class="who">{C.e(getattr(user, "first_name", "") or "You")}</div>
      <div class="what">{C.e(what)}</div></div>
    </div>"""


def _body(user, settings: dict, *, mirror: bool, notify: Callable[[str, str], None] | None) -> None:
    draft = st.session_state.get(DRAFT_KEY, current_key(settings))
    C.html(CSS + _preview(user, draft))
    C.html('<div class="tr-avp-title">Choose your avatar</div>')

    # The way back to the letter, first, in the same grid as the characters.
    C.html('<div class="tr-avp-cat"><span>Default</span><em>The first letter of your name</em></div>')
    for column, _none in flow.grid([None], PER_ROW, "av_default"):
        with column:
            flow.pick("av_initial", "Your initial", lambda: C.html(_tile(None, user, draft == "")),
                      on_click=_choose, args=("",))

    for category, assets in art.avatars_by_category().items():      # empty folders never appear
        C.html(f'<div class="tr-avp-cat"><span>{C.e(art.label_for(category))}</span>'
               f'<em>{C.e(CATEGORY_TAGLINES.get(category, ""))}</em></div>')
        for column, asset in flow.grid(assets, PER_ROW, f"av_{category}"):
            with column:
                flow.pick(f"av_{asset.key}", asset.label,
                          lambda a=asset: C.html(_tile(a, user, draft == a.key)),
                          on_click=_choose, args=(asset.key,))

    with st.container(key="trpair_avatar_foot"):
        a, b = st.columns(2, gap="small")
        if a.button("Cancel", key="avatar_cancel", use_container_width=True):
            _close()
            st.rerun()
        if b.button("Save", key="avatar_save", type="primary", use_container_width=True,
                    icon=":material/check:"):
            changed = save(settings, draft, mirror=mirror)
            _close()
            if changed and notify:
                notify("success", "Avatar updated.")
            st.rerun()


def picker(user, settings: dict, *, mirror: bool, notify: Callable[[str, str], None] | None = None) -> None:
    """Draw the dialog if it is open. Nothing at all otherwise."""
    if not is_open():
        return

    @st.dialog("Your avatar", width="large")
    def _dialog() -> None:
        _body(user, settings, mirror=mirror, notify=notify)

    _dialog()


#: The picker's own stylesheet — inside the dialog, so it travels only when
#: the dialog does. The tiles use the theme's ``pick_`` mechanics (a
#: transparent button stretched over the tile; focus ring on the pair).
CSS = """<style>
/* the dialog at a collection's width: seven faces a row, not spread across a wide screen */
[data-testid="stDialog"] > div { max-width:1040px !important; }
.tr-avp-head { display:flex; align-items:center; gap:18px; padding:4px 2px 14px; border-bottom:1px solid var(--tr-border); margin-bottom:10px; }
.tr-avp-head .big { width:96px; height:96px; border-radius:50%; flex:none; overflow:hidden; display:flex; align-items:center; justify-content:center;
  background:radial-gradient(circle at 50% 35%, #2A1620, #120B10 70%); border:2px solid rgba(255,51,85,.6);
  box-shadow: 0 0 0 5px rgba(255,51,85,.08), 0 18px 40px -18px rgba(255,51,85,.9); }
.tr-avp-head .big img { width:100%; height:100%; object-fit:cover; display:block; }
.tr-avp-head .initial, .tr-avt .initial { font-family:var(--tr-mono); font-weight:500; color:#fff; }
.tr-avp-head .big .initial { font-size:38px; }
.tr-avp-head .eyebrow { font-family:var(--tr-mono); font-size:10px; letter-spacing:.3em; color:#FF8CA0; }
.tr-avp-head .who { font-size:22px; font-weight:800; letter-spacing:-.03em; color:var(--tr-text); margin-top:4px; }
.tr-avp-head .what { font-size:13px; color:var(--tr-text-3); margin-top:2px; }
.tr-avp-title { font-size:15px; font-weight:800; letter-spacing:-.01em; color:var(--tr-text); margin:2px 0 -4px; }
.tr-avp-cat { display:flex; align-items:baseline; gap:10px; margin:12px 0 -6px; }
.tr-avp-cat span { font-family:var(--tr-mono); font-size:10.5px; letter-spacing:.26em; text-transform:uppercase; color:var(--tr-text-2); }
.tr-avp-cat em { font-style:normal; font-size:11.5px; color:var(--tr-text-4); }
.tr-avt { display:flex; flex-direction:column; align-items:center; gap:7px; padding:10px 4px 9px; border-radius:14px; border:1px solid transparent;
  transition: transform var(--tr-fast) var(--tr-ease), background var(--tr-fast) var(--tr-ease), border-color var(--tr-fast) var(--tr-ease); }
.tr-avt .ring { position:relative; width:84px; height:84px; border-radius:50%; overflow:hidden; background:#150B10; border:2px solid rgba(255,255,255,.12);
  box-shadow: 0 12px 26px -16px rgba(0,0,0,1); display:flex; align-items:center; justify-content:center;
  transition: border-color var(--tr-fast) var(--tr-ease), box-shadow var(--tr-fast) var(--tr-ease), transform var(--tr-fast) var(--tr-ease); }
.tr-avt .ring img { width:100%; height:100%; object-fit:cover; display:block; }
.tr-avt .ring .initial { font-size:29px; }
.tr-avt .n { font-size:11.5px; font-weight:600; color:var(--tr-text-2); text-align:center; line-height:1.2; max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.tr-avt .ok { position:absolute; right:2px; bottom:2px; width:20px; height:20px; border-radius:50%; display:none; align-items:center; justify-content:center;
  background:linear-gradient(180deg,#FF6E88,#D6133F); border:2px solid #14101A; box-shadow:0 4px 10px -4px rgba(255,51,85,.9); }
[class*="st-key-pick_av_"]:hover .tr-avt { background:rgba(255,255,255,.04); border-color:var(--tr-border); transform:translateY(-3px); }
[class*="st-key-pick_av_"]:hover .tr-avt .ring { border-color:rgba(255,107,133,.5); }
.tr-avt.selected { background:rgba(255,51,85,.07); border-color:rgba(255,51,85,.35); }
.tr-avt.selected .ring { border-color:#FF3355; box-shadow: 0 0 0 4px rgba(255,51,85,.16), 0 14px 30px -14px rgba(255,51,85,.95); transform:translateY(-2px); }
.tr-avt.selected .ok { display:flex; }
.tr-avt.selected .n { color:#fff; }
[class*="st-key-pick_av_"]:has(button:focus-visible) { outline:2px solid var(--tr-accent); outline-offset:2px; border-radius:14px; }
[class*="st-key-trpair_avatar_foot"] { margin-top:14px; }
@media (min-width: 769px) and (max-width: 819px) {
  /* seven 84px faces need a 92px column; a small tablet has ~85px, so the face gives a little */
  .tr-avt { padding-left:2px; padding-right:2px; }
  .tr-avt .ring { width:78px; height:78px; }
}
@media (max-width: 768px) {
  .tr-avp-head .big { width:72px; height:72px; }
  .tr-avp-head .big .initial { font-size:30px; }
  .tr-avp-head .who { font-size:18px; }
  .tr-avt .ring { width:70px; height:70px; }
  [class*="st-key-trgrid_av_"] [data-testid="stColumn"] { --tr-cols: 3; }
}
@media (prefers-reduced-motion: reduce) { .tr-avt, .tr-avt .ring { transition:none; } }
</style>"""

__all__ = ["DRAFT_KEY", "OPEN_KEY", "SETTING", "chosen", "current_key", "is_open", "mark",
           "open_picker", "picker", "save"]
