"""The name TicketRadar calls you by and the admin recognition state.

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
#: Long enough for a real name, short enough that the chip never becomes a
#: paragraph. The chip and the menu both ellipsise beyond their own width.
MAX_NAME = 40
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
    """The actual TicketRadar display name shown on the account chip."""
    return first_name(user, settings)


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


__all__ = [
    "MAX_NAME",
    "NAME_FIELD",
    "NAME_INPUT",
    "OPEN_KEY",
    "chip_label",
    "clean_name",
    "display_name",
    "first_name",
    "is_admin",
    "name_problem",
    "save_display_name",
    "settings_card",
    "stored_name",
]
