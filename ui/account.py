"""The name TicketRadar calls you by — and the admin's account switcher.

Two small things live here, and both are *labels*, never authority.

**The display name.** Firebase keeps a ``displayName`` on every account, and
that is what the chip used to show. It is the wrong place to keep the one a
Google user wants to change: their Firebase record is filled in from their
Google profile, and TicketRadar has no business writing to Google. So the
TicketRadar name is one more field, ``display_name``, on the same
``users/{uid}`` document the app already reads as its settings — beside
``notify_email`` and ``avatar_key``, so showing it costs no extra read and
saving it is the one write ``monitor.state.save_settings`` already makes.
That field is authoritative for everything on screen; Firebase's own
``displayName`` is kept in step as a courtesy (it is what an email
sign-up wrote at the start, and what other Firebase tooling shows) and a
failure to update it never loses the name the person just chose. Google's
profile is neither read nor written.

**The admin.** Whether an account is the admin comes from one place and only
one: the ``admin: true`` custom claim on Firebase's own account record, read
at sign-in and again on every restore, carried on
:class:`auth.session.AuthUser` in server-side session state that no browser
can write (``auth.session.is_admin``). Nothing here — no email string, no
settings field, nothing typed on a page — decides it. What it changes is
what the menu *says* and which rows it draws; every authorization decision
(the category watch, the monitor ceiling, Firestore's own rules) still asks
the claim itself, exactly as before.

**Switching accounts** is a sign-out and a normal sign-in, nothing else. The
switcher remembers *addresses*, on the admin's own profile document, and an
address is not a credential: choosing one signs the current user out and
opens the login page with the box filled in. No credential is stored, no
token is kept, no session is fabricated, and no account is ever impersonated.
"""

from __future__ import annotations

from typing import Callable

import streamlit as st

from auth import firebase
from auth import session as auth_session
from auth.firebase import AuthError
from monitor.state import save_settings
from ui import components as C

#: The TicketRadar name, on ``users/{uid}``. Absent means "whatever Firebase
#: knows" — no migration, and an account that never opens Account settings
#: keeps looking exactly as it did.
NAME_FIELD = "display_name"
#: Addresses the admin's switcher offers, on the *admin's own* document.
#: Addresses only: never a credential, never a token, never another account's
#: data. Nothing reads this but the menu that wrote it.
ACCOUNTS_FIELD = "known_emails"

#: Long enough for a real name, short enough that the chip never becomes a
#: paragraph. The chip and the menu both ellipsise beyond their own width.
MAX_NAME = 40
MAX_ACCOUNTS = 8

#: What the account menu calls the admin account, whatever name it carries.
ADMIN_LABEL = "Admin"

OPEN_KEY = "acct_switch_open"
ADD_KEY = "acct_switch_add"
NAME_INPUT = "settings_display_name"


# ──────────────────────────────────────────────────────────────────────────
# The name
# ──────────────────────────────────────────────────────────────────────────
def clean_name(raw: object) -> str:
    """A name as it will be stored: whitespace collapsed, ends trimmed, and
    never longer than :data:`MAX_NAME`."""
    return " ".join(str(raw or "").split())[:MAX_NAME]


def name_problem(raw: object) -> str:
    """"" when the name is acceptable, else the reason it is not."""
    if not clean_name(raw):
        return "Enter a name — it can't be blank."
    return ""


def stored_name(settings: dict | None) -> str:
    """The TicketRadar name on the profile document, or ""."""
    if not isinstance(settings, dict):
        return ""
    return clean_name(settings.get(NAME_FIELD, ""))


def display_name(user, settings: dict | None = None) -> str:
    """What TicketRadar calls this person.

    The profile document first — that is the one they can change — then the
    name Firebase holds (the sign-up name, or the one Google supplied),
    then the local part of their address, so there is always something.
    """
    chosen = stored_name(settings)
    if chosen:
        return chosen
    firebase_name = clean_name(getattr(user, "display_name", ""))
    if firebase_name:
        return firebase_name
    email = str(getattr(user, "email", "") or "")
    return email.split("@")[0] if email else "there"


def first_name(user, settings: dict | None = None) -> str:
    name = display_name(user, settings)
    return name.split()[0] if name.split() else name


def is_admin(user) -> bool:
    """Firebase's own claim, as this session recorded it. Never an email."""
    return bool(getattr(user, "admin", False))


def chip_label(user, settings: dict | None = None) -> str:
    """The name on the chip: the admin is the admin, everybody else is
    themselves."""
    return ADMIN_LABEL if is_admin(user) else first_name(user, settings)


def save_display_name(user, settings: dict, raw: object, *, mirror: bool) -> str:
    """Store a new TicketRadar name. Returns "" on success, else the problem.

    The profile document is written first, because it is what the app shows.
    Firebase's own ``displayName`` is then brought into line — best effort:
    if that call fails the name the person chose is already safe, and the
    next sign-in reads it from the profile as always. The session's own
    :class:`auth.session.AuthUser` is updated last so the chip above the page
    changes on this very run, with no sign-out and no extra read.
    """
    problem = name_problem(raw)
    if problem:
        return problem
    name = clean_name(raw)
    if name == display_name(user, settings):
        return ""
    save_settings({**settings, NAME_FIELD: name}, mirror=mirror)
    try:
        firebase.FirebaseAuth().set_display_name(auth_session.id_token(), name)
    except AuthError as exc:                       # the profile value stands
        print(f"[account] Firebase display name not updated: {exc.code}", flush=True)
    auth_session.update_display_name(name)
    return ""


def settings_card(user, settings: dict, *, mirror: bool,
                  notify: Callable[[str, str], None]) -> None:
    """Account settings → Profile: the one name every page shows.

    Saving runs from the button's ``on_click``, so the single run that
    follows already draws the new name in the chip and the menu — no
    ``st.rerun()``, no second pass over the page, and nothing in the wizard
    or the monitor list is disturbed.
    """
    with st.container(border=True, key="trcard_profile"):
        C.step_header("user", "Profile", "The name TicketRadar calls you by.")
        C.html('<div class="tr-field-label">Display name</div>')
        st.text_input("Display name", value=display_name(user, settings),
                      key=NAME_INPUT, max_chars=MAX_NAME, placeholder="Your name",
                      label_visibility="collapsed")
        st.caption("This name appears across TicketRadar — the account menu, "
                   "your avatar and your alerts. It is only your TicketRadar "
                   "name; the account you sign in with doesn't change.")
        st.button("Save changes", key="save_display_name", type="primary",
                  use_container_width=True, icon=":material/save:",
                  help="Use this name across TicketRadar",
                  on_click=_save_name, args=(user, settings, mirror, notify))
        if is_admin(user):
            C.status_line("info", "This is the admin account. The menu says so "
                                  "whatever name you choose.")


def _save_name(user, settings: dict, mirror: bool, notify) -> None:
    raw = st.session_state.get(NAME_INPUT, "")
    problem = save_display_name(user, settings, raw, mirror=mirror)
    if problem:
        notify("error", problem)
        return
    notify("success", f"You're {clean_name(raw)} across TicketRadar now.")


# ──────────────────────────────────────────────────────────────────────────
# Switching accounts — admin only
# ──────────────────────────────────────────────────────────────────────────
def known_emails(settings: dict | None) -> list[str]:
    """The addresses the switcher offers, as stored. Strings only, deduped,
    and never more than :data:`MAX_ACCOUNTS`."""
    raw = settings.get(ACCOUNTS_FIELD, []) if isinstance(settings, dict) else []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for value in raw:
        email = str(value or "").strip().lower()
        if email and firebase.valid_email(email) and email not in out:
            out.append(email)
    return out[:MAX_ACCOUNTS]


def open_switcher(user, settings: dict, *, mirror: bool) -> None:
    """An ``on_click``: open the switcher, remembering the account it was
    opened from so the way back is always on the list. Admin only — and
    checked again when the dialog draws, and again when it acts."""
    if not is_admin(user):
        return
    st.session_state[OPEN_KEY] = True
    email = str(getattr(user, "email", "") or "").strip().lower()
    known = known_emails(settings)
    if email and email not in known and len(known) < MAX_ACCOUNTS:
        _remember(settings, known + [email], mirror=mirror)


def is_open() -> bool:
    return bool(st.session_state.get(OPEN_KEY))


def _close() -> None:
    st.session_state.pop(OPEN_KEY, None)


def _remember(settings: dict, emails: list[str], *, mirror: bool) -> None:
    save_settings({**settings, ACCOUNTS_FIELD: emails[:MAX_ACCOUNTS]}, mirror=mirror)
    settings[ACCOUNTS_FIELD] = emails[:MAX_ACCOUNTS]      # the page already in hand


def _switch_to(email: str) -> None:
    """Leave this account and open the login page with ``email`` in its box.

    This is a sign-out. The Firebase session is dropped, the cookie is
    cleared and every trace of the previous person goes with it; what
    survives is one address, which proves nothing and unlocks nothing. The
    next person signs in through Firebase exactly as they always would.
    An empty address is an ordinary sign-out with nothing carried over.
    """
    if email:
        auth_session.request_switch(email)
    else:
        auth_session.sign_out("account_switch")


def dialog(user, settings: dict, *, mirror: bool) -> None:
    """Draw the switcher if it is open, and only for the admin."""
    if not is_open():
        return
    if not is_admin(user):
        _close()
        return

    @st.dialog("Switch account")
    def _dialog() -> None:
        _body(user, settings, mirror=mirror)

    _dialog()


def _body(user, settings: dict, *, mirror: bool) -> None:
    current = str(getattr(user, "email", "") or "").strip().lower()
    C.html(SWITCH_CSS)
    C.html(
        '<div class="tr-switch-head">'
        f'<div class="t">Signed in as <b>{C.e(current)}</b></div>'
        '<div class="s">Pick another TicketRadar account. This signs you out here '
        'and opens the sign-in page with that address filled in — you sign in with '
        'its own sign-in, as always. No credential is ever stored.</div></div>'
    )

    others = [e for e in known_emails(settings) if e != current]
    if others:
        C.html('<div class="tr-switch-label">Your accounts</div>')
        for email in others:
            row, remove = st.columns([5, 1], gap="small", vertical_alignment="center")
            with row:
                if st.button(email, key=f"switch_to_{email}", use_container_width=True,
                             icon=":material/switch_account:",
                             help=f"Sign out and sign in as {email}"):
                    _switch_to(email)
                    _close()
                    st.rerun()
            with remove:
                if st.button("", key=f"switch_forget_{email}", use_container_width=True,
                             icon=":material/close:", help="Forget this address"):
                    _remember(settings, [e for e in known_emails(settings) if e != email],
                              mirror=mirror)
                    st.rerun()
    else:
        C.html('<div class="tr-switch-empty">No other accounts remembered yet — '
               'add one below.</div>')

    C.html('<div class="tr-switch-label">Add an account</div>')
    add = st.text_input("Add an account", key=ADD_KEY, placeholder="other@example.com",
                        label_visibility="collapsed", autocomplete="off")
    a, b = st.columns(2, gap="small")
    if a.button("Remember", key="switch_remember", use_container_width=True,
                icon=":material/bookmark_add:",
                help="Keep this address on the list. It is an address, not a login."):
        email = str(add or "").strip().lower()
        if not firebase.valid_email(email):
            C.html('<div class="tr-switch-bad">That doesn\'t look like an email address.</div>')
        elif len(known_emails(settings)) >= MAX_ACCOUNTS:
            C.html(f'<div class="tr-switch-bad">That\'s {MAX_ACCOUNTS} accounts — '
                   'forget one first.</div>')
        else:
            _remember(settings, known_emails(settings) + [email], mirror=mirror)
            st.session_state.pop(ADD_KEY, None)
            st.rerun()
    if b.button("Use another account", key="switch_other", use_container_width=True,
                icon=":material/logout:", help="Sign out and sign in as somebody else"):
        _switch_to("")
        _close()
        st.rerun()

    if st.button("Cancel", key="switch_cancel", use_container_width=True):
        _close()
        st.rerun()


SWITCH_CSS = """<style>
.tr-switch-head .t { font-size:14px; font-weight:700; color:var(--tr-text); letter-spacing:-.01em; }
.tr-switch-head .t b { color:#FF8CA0; font-weight:700; }
.tr-switch-head .s { font-size:12.5px; color:var(--tr-text-3); margin-top:6px; line-height:1.55; }
.tr-switch-label { font-family:var(--tr-mono); font-size:10px; letter-spacing:.26em; text-transform:uppercase;
  color:var(--tr-text-3); margin:14px 0 2px; }
.tr-switch-empty { font-size:12.5px; color:var(--tr-text-4); padding:6px 2px; }
.tr-switch-bad { font-size:12.5px; color:#FF8A8A; margin-top:6px; }
[class*="st-key-switch_to_"] button { justify-content:flex-start !important; }
[class*="st-key-switch_forget_"] button { color:var(--tr-text-4) !important; }
[class*="st-key-switch_cancel"] button { border-color:transparent !important; color:var(--tr-text-3) !important; }
</style>"""


__all__ = [
    "ACCOUNTS_FIELD",
    "ADMIN_LABEL",
    "MAX_NAME",
    "NAME_FIELD",
    "NAME_INPUT",
    "OPEN_KEY",
    "chip_label",
    "clean_name",
    "dialog",
    "display_name",
    "first_name",
    "is_admin",
    "is_open",
    "known_emails",
    "name_problem",
    "open_switcher",
    "save_display_name",
    "settings_card",
    "stored_name",
]
