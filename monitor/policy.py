"""What a monitor may be — the one definition every layer enforces.

Four places decide whether a monitor is acceptable, and they must agree:

* the **page** (``ui.flow``) — only offers what is allowed;
* the **store** (``monitor.state``) — refuses or normalises a write from the
  signed-in person before it leaves the app;
* **Firestore's rules** (``firestore.rules``) — refuse a document written
  straight through Firebase's API, bypassing the app;
* the **worker** (``monitor.checker``) — never trusts a document it reads,
  because the rules can be misdeployed and older documents predate them.

The numbers live here so the four cannot drift apart. ``firestore.rules``
cannot import Python, so ``tests/test_security_hardening.py`` reads the rules
file and checks it states the same values.

The worker is the last line: it binds every alert to the owner's *verified*
account address (read from Firebase Authentication with the service account,
never from the document), drops admin-only features from accounts without
the claim, holds ordinary accounts to their active-monitor limit, bounds the
end date and refuses documents that carry fields the application never
writes. Nothing here imports Streamlit or ``auth``: the worker must not.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable

from config.timezone import now_ist
from monitor.models import Monitor

# ──────────────────────────────────────────────────────────────────────────
# The numbers
# ──────────────────────────────────────────────────────────────────────────
#: The longest a monitor may run. Long enough to sit on a film announced a
#: month out (and the "+24 hours" extension stays far inside it); short
#: enough that a forgotten — or deliberately endless — monitor cannot keep
#: the worker re-dispatching itself for ever.
MAX_MONITOR_DAYS = 30
#: Check intervals an ordinary account may choose, and the administrator's.
MEMBER_INTERVALS = (10, 15, 30)
ADMIN_INTERVALS = (5, 10, 15, 30)
#: Running monitors an ordinary account may have at once.
ACTIVE_MONITOR_LIMIT = 5
#: Size bounds. Far above anything the wizard produces (a whole city's
#: theatres in every format is well under this) and far below abuse.
MAX_TARGETS = 200
MAX_DATE_CODES = 62
MAX_CATEGORIES = 20
MAX_TEXT = 500
MAX_EMAIL_LENGTH = 254

#: Every field ``Monitor.to_dict`` writes. A stored monitor with anything
#: else in it was not written by this application.
ALLOWED_MONITOR_FIELDS = frozenset({
    "id", "movie", "targets", "interval_minutes", "monitor_until",
    "start_immediately", "notify_email", "date_codes", "status", "created_at",
    "stopped_at", "stopped_reason", "first_check_requested_at", "problem",
    "owner_uid", "categories", "show_time",
})


# ──────────────────────────────────────────────────────────────────────────
# Recipients
# ──────────────────────────────────────────────────────────────────────────
class InvalidRecipient(ValueError):
    """Not exactly one plausible email address."""


#: One address: no display name, no list, no quoting, no whitespace. Commas,
#: semicolons, angle brackets and quotes are what turn a ``To`` header into
#: several recipients, so none of them can appear.
_EMAIL = re.compile(
    r"^[A-Za-z0-9.!#$%&*+/=?^_`{|}~-]+"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


def normalise_recipient(value: object) -> str:
    """The address, stripped — or :class:`InvalidRecipient`.

    Exactly one address is accepted. ``"a@x.com, b@y.com"``, ``"Name
    <a@x.com>"``, an address with a newline in it and anything else that is
    not one bare address is refused rather than "fixed".
    """
    if not isinstance(value, str):
        raise InvalidRecipient("The notification address is missing.")
    address = value.strip()
    if not address or len(address) > MAX_EMAIL_LENGTH or not _EMAIL.match(address):
        raise InvalidRecipient("That isn't a single valid email address.")
    return address


def same_address(a: str, b: str) -> bool:
    return bool(a) and bool(b) and a.strip().casefold() == b.strip().casefold()


def mask_email(address: str) -> str:
    """``someone@gmail.com`` → ``s***@gmail.com``: safe for a public log."""
    if not isinstance(address, str) or "@" not in address:
        return "***"
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}"


# ──────────────────────────────────────────────────────────────────────────
# End dates and intervals
# ──────────────────────────────────────────────────────────────────────────
def max_until(at: datetime | None = None) -> datetime:
    """The latest end time a monitor may have, measured from ``at``."""
    return (at or now_ist()) + timedelta(days=MAX_MONITOR_DAYS)


def until_allowed(until: datetime, at: datetime | None = None) -> bool:
    return until <= max_until(at)


class MonitorNotAllowed(ValueError):
    """A write the policy refuses, with the sentence to show."""


def assert_until_allowed(until: datetime, at: datetime | None = None) -> None:
    if not until_allowed(until, at):
        raise MonitorNotAllowed(
            f"A monitor can run for at most {MAX_MONITOR_DAYS} days — choose an earlier end date.")


def interval_allowed(minutes: int, *, admin: bool) -> bool:
    return minutes in (ADMIN_INTERVALS if admin else MEMBER_INTERVALS)


def shape_problem(monitor: Monitor) -> str:
    """Why this monitor's contents are out of bounds, or ``""``.

    Sizes and types only — the things no legitimate write ever exceeds.
    """
    if not monitor.targets:
        return "it has no targets"
    if len(monitor.targets) > MAX_TARGETS:
        return "it has too many targets"
    if len(monitor.date_codes) > MAX_DATE_CODES:
        return "it watches too many dates"
    if len(monitor.categories) > MAX_CATEGORIES:
        return "it watches too many categories"
    texts = [monitor.movie.title, monitor.movie.event_code, monitor.movie.source_url,
             monitor.movie.poster_url, monitor.show_time]
    texts += [t.venue_name for t in monitor.targets] + [t.fmt for t in monitor.targets]
    if any(len(str(text or "")) > MAX_TEXT for text in texts):
        return "a field is too long"
    if monitor.interval_minutes not in ADMIN_INTERVALS:
        return f"its interval ({monitor.interval_minutes} min) is not one the app offers"
    return ""


def unknown_fields(raw: dict[str, Any]) -> set[str]:
    return set(raw) - ALLOWED_MONITOR_FIELDS


# ──────────────────────────────────────────────────────────────────────────
# Who owns a monitor — as Firebase Authentication says, not the document
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class OwnerIdentity:
    """The owner's account record: the only source of the alert address."""

    email: str = ""
    email_verified: bool = False
    admin: bool = False
    disabled: bool = False

    @property
    def usable(self) -> bool:
        return bool(self.email) and self.email_verified and not self.disabled


class IdentityUnavailable(Exception):
    """The account records could not be read this tick (network, permission).
    Nothing is checked or sent for the affected monitors; the next tick
    tries again."""


#: ``resolver([uid, …]) -> {uid: OwnerIdentity}``. A UID missing from the
#: answer has no account — its monitors are refused.
IdentityResolver = Callable[[list[str]], dict[str, OwnerIdentity]]

#: The resolver for a store with no Firebase behind it — the JSON files on a
#: developer's machine and the test-suite. There is no account record there,
#: so the stored address is *validated* (one address, well-formed) rather
#: than bound. The production worker never selects this: it refuses to run
#: on JSON inside Actions (``monitor.worker.preflight``).
LOCAL = "local"


def admin_claim(custom_attributes: Any) -> bool:
    """Exactly ``{"admin": true}`` in the account's custom claims. (The
    worker's own copy of ``auth.firebase.admin_claim``: the worker never
    imports ``auth``.)"""
    if not custom_attributes:
        return False
    try:
        claims = json.loads(custom_attributes) if isinstance(custom_attributes, str) else custom_attributes
    except (TypeError, ValueError):
        return False
    return isinstance(claims, dict) and claims.get("admin") is True


IDENTITY_LOOKUP = "https://identitytoolkit.googleapis.com/v1/projects/{project}/accounts:lookup"
IDENTITY_SCOPES = ("https://www.googleapis.com/auth/identitytoolkit",
                   "https://www.googleapis.com/auth/cloud-platform")


def identity_token_getter(info: dict[str, Any]) -> Callable[[], str]:
    """An access token able to read account records — the same credential
    and scopes ``tools/grant_admin.py`` uses."""
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_info(info, scopes=list(IDENTITY_SCOPES))
    state: dict[str, Any] = {"token": "", "expires": 0.0}

    def token() -> str:
        if not state["token"] or time.time() > state["expires"]:
            creds.refresh(Request())
            state["token"] = creds.token
            expiry = creds.expiry.timestamp() if creds.expiry else time.time() + 3000
            state["expires"] = expiry - 120
        return str(state["token"])

    return token


class FirebaseIdentityResolver:
    """Reads account records with the service account, a few minutes at a
    time. One batched ``accounts:lookup`` per tick at most; the answer is
    reused for :attr:`TTL` seconds so a 30-second tick does not ask again."""

    TTL = 600.0
    BATCH = 100
    TIMEOUT = 15.0

    def __init__(self, project: str, token: Callable[[], str], *,
                 post: Callable[..., Any] | None = None, clock: Callable[[], float] = time.monotonic):
        self.project = project
        self.token = token
        self._post = post
        self._clock = clock
        self._cache: dict[str, tuple[float, OwnerIdentity | None]] = {}

    def _request(self, uids: list[str]) -> list[dict[str, Any]]:
        if self._post is None:
            import requests

            self._post = requests.post
        try:
            response = self._post(IDENTITY_LOOKUP.format(project=self.project),
                                  json={"localId": uids},
                                  headers={"Authorization": f"Bearer {self.token()}"},
                                  timeout=self.TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - network, TLS, a refused credential
            raise IdentityUnavailable(f"account lookup failed ({type(exc).__name__})") from None
        status = getattr(response, "status_code", 0)
        if status >= 400:
            raise IdentityUnavailable(f"account lookup answered HTTP {status}")
        try:
            body = response.json()
        except ValueError:
            raise IdentityUnavailable("account lookup answered with no JSON") from None
        users = body.get("users") if isinstance(body, dict) else None
        return users if isinstance(users, list) else []

    def __call__(self, uids: list[str]) -> dict[str, OwnerIdentity]:
        now = self._clock()
        wanted = sorted({u for u in uids if u})
        stale = [u for u in wanted if u not in self._cache or now - self._cache[u][0] > self.TTL]
        for i in range(0, len(stale), self.BATCH):
            batch = stale[i:i + self.BATCH]
            found: dict[str, OwnerIdentity] = {}
            for user in self._request(batch):
                uid = str(user.get("localId", ""))
                if uid:
                    found[uid] = OwnerIdentity(
                        email=str(user.get("email") or ""),
                        email_verified=user.get("emailVerified") is True,
                        admin=admin_claim(user.get("customAttributes")),
                        disabled=user.get("disabled") is True,
                    )
            for uid in batch:
                self._cache[uid] = (now, found.get(uid))
        return {u: ident for u in wanted if (ident := self._cache[u][1]) is not None}


class _Unavailable:
    """The production resolver when the credential itself is unusable:
    every lookup fails, so nothing is checked and nothing is sent."""

    def __init__(self, reason: str):
        self.reason = reason

    def __call__(self, uids: list[str]) -> dict[str, OwnerIdentity]:
        raise IdentityUnavailable(self.reason)


_override: IdentityResolver | str | None = None
_built: tuple[str, IdentityResolver] | None = None
_lock = threading.Lock()


def set_identity_resolver(resolver: IdentityResolver | str | None) -> None:
    """Tests (and only tests) choose the resolver: a callable, :data:`LOCAL`,
    or ``None`` to go back to the automatic choice."""
    global _override
    _override = resolver


def worker_resolver(backend: str) -> IdentityResolver | str:
    """The resolver for the worker's store.

    Firestore — production — always gets Firebase's account records. If the
    service account is unusable the resolver fails every lookup: the worker
    then checks and sends nothing rather than trust a stored address. The
    JSON store has no accounts and gets :data:`LOCAL`.
    """
    global _built
    if _override is not None:
        return _override
    if backend != "firestore":
        return LOCAL
    from config import firestore as fs

    project = fs.project_from_env()
    with _lock:
        if _built is not None and _built[0] == project:
            return _built[1]
        info, problem = fs.service_account_status()
        if problem or not project:
            resolver: IdentityResolver = _Unavailable(problem or "FIREBASE_PROJECT_ID is not set")
        else:
            try:
                resolver = FirebaseIdentityResolver(project, identity_token_getter(info or {}))
            except Exception as exc:  # noqa: BLE001 - a key google-auth refuses
                resolver = _Unavailable(f"the service account was rejected ({type(exc).__name__})")
        _built = (project, resolver)
        return resolver


# ──────────────────────────────────────────────────────────────────────────
# The worker's screen
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class Screened:
    """What a tick may work on.

    ``eligible``  every monitor the tick may touch: the not-running ones as
                  they were, and the running ones that passed (normalised).
    ``deferred``  running monitors whose owner could not be looked up this
                  tick — not checked, not refused; tried again next tick.
    ``rejected``  monitor id → why it was refused. Never checked, never
                  emailed, and never what keeps a segment alive.
    ``clamped``   monitors whose end date was pulled back to the cap — the
                  only change the worker writes back to a monitor.
    """

    eligible: list[Monitor] = field(default_factory=list)
    deferred: list[Monitor] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)
    clamped: list[Monitor] = field(default_factory=list)


def _log(monitor: Monitor, message: str) -> None:
    print(f"[policy] {monitor.id[:8]} {message}", flush=True)


def screen(monitors: Iterable[Monitor], *, at: datetime,
           resolver: IdentityResolver | str) -> Screened:
    """Decide, for one worker tick, which running monitors may be checked
    and where each one's alert may go."""
    out = Screened()
    running: list[Monitor] = []
    for monitor in monitors:
        (running if monitor.is_running(at) else out.eligible).append(monitor)

    local = resolver == LOCAL
    identities: dict[str, OwnerIdentity] = {}
    if not local and running:
        try:
            identities = resolver([m.owner_uid for m in running if m.owner_uid])  # type: ignore[operator]
        except IdentityUnavailable as exc:
            print(f"[policy] owner accounts could not be read ({exc}); "
                  f"{len(running)} monitor(s) wait for the next tick", flush=True)
            out.deferred = running
            return out

    passed: list[tuple[Monitor, OwnerIdentity | None]] = []
    for monitor in running:
        problem = shape_problem(monitor)
        if problem:
            out.rejected[monitor.id] = problem
            _log(monitor, f"refused: {problem}")
            continue

        identity: OwnerIdentity | None = None
        if local:
            try:
                monitor.notify_email = normalise_recipient(monitor.notify_email)
            except InvalidRecipient:
                out.rejected[monitor.id] = "its recipient is not a single valid address"
                _log(monitor, "refused: recipient is not a single valid address")
                continue
        else:
            identity = identities.get(monitor.owner_uid) if monitor.owner_uid else None
            if identity is None or not identity.usable:
                reason = ("it has no owner account" if identity is None
                          else "its owner's account is disabled" if identity.disabled
                          else "its owner's email address is not verified")
                out.rejected[monitor.id] = reason
                _log(monitor, f"refused: {reason}")
                continue
            try:
                bound = normalise_recipient(identity.email)
            except InvalidRecipient:
                out.rejected[monitor.id] = "its owner's account address is not usable"
                _log(monitor, "refused: the owner's account address is not usable")
                continue
            if not same_address(monitor.notify_email, bound):
                # Alerts go to the account's own verified address, whatever
                # the document says. Never to a third party.
                _log(monitor, f"recipient bound to the owner's verified address {mask_email(bound)}")
            monitor.notify_email = bound
            if not identity.admin:
                if monitor.interval_minutes not in MEMBER_INTERVALS:
                    _log(monitor, f"interval {monitor.interval_minutes} min is admin-only; checking every 10")
                    monitor.interval_minutes = 10
                if monitor.categories or monitor.show_time:
                    _log(monitor, "category watch is admin-only; watching as an ordinary monitor")
                    monitor.categories, monitor.show_time = [], ""

        if not until_allowed(monitor.monitor_until, at):
            monitor.monitor_until = max_until(at)
            out.clamped.append(monitor)
            _log(monitor, f"end date pulled back to the {MAX_MONITOR_DAYS}-day limit")
        passed.append((monitor, identity))

    if local:
        out.eligible.extend(m for m, _ in passed)
        return out

    # Ordinary accounts: at most ACTIVE_MONITOR_LIMIT running monitors, the
    # oldest first — the ones the app would have accepted.
    by_owner: dict[str, list[Monitor]] = {}
    for monitor, identity in passed:
        if identity is not None and identity.admin:
            out.eligible.append(monitor)
        else:
            by_owner.setdefault(monitor.owner_uid, []).append(monitor)
    for owned in by_owner.values():
        owned.sort(key=lambda m: m.created_at)
        out.eligible.extend(owned[:ACTIVE_MONITOR_LIMIT])
        for monitor in owned[ACTIVE_MONITOR_LIMIT:]:
            out.rejected[monitor.id] = "its owner is over the active monitor limit"
            _log(monitor, "refused: the owner is over the active monitor limit")
            if monitor in out.clamped:
                out.clamped.remove(monitor)
    return out


__all__ = [
    "ACTIVE_MONITOR_LIMIT",
    "ADMIN_INTERVALS",
    "ALLOWED_MONITOR_FIELDS",
    "FirebaseIdentityResolver",
    "IdentityUnavailable",
    "InvalidRecipient",
    "LOCAL",
    "MAX_MONITOR_DAYS",
    "MEMBER_INTERVALS",
    "MonitorNotAllowed",
    "OwnerIdentity",
    "Screened",
    "assert_until_allowed",
    "interval_allowed",
    "mask_email",
    "max_until",
    "normalise_recipient",
    "same_address",
    "screen",
    "set_identity_resolver",
    "shape_problem",
    "unknown_fields",
    "until_allowed",
    "worker_resolver",
]
