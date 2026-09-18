"""Disposable / temporary email domains — refused at sign-up.

Why
---
Sign-up requires a verified address, but a throwaway inbox can receive the
verification email just as well as a real one. Refusing the known
temporary-mail providers keeps accounts on addresses people actually keep.

What this is, and is not
------------------------
* Two **denylists**, one domain per line, ``#`` comments, auditable in a diff:

  - ``disposable_domains.txt`` — the project's own, curated by hand;
  - ``disposable_domains.community.txt`` — the community-maintained CC0
    blocklist (github.com/disposable-email-domains), vendored with its
    upstream commit in the header and refreshed by
    ``tools/refresh_disposable_list.py``. This is what knows the *mailbox*
    domains a service such as temp-mail.org hands out: that site's own
    name never appears in an address — its mailboxes live on a pool of
    random-looking domains (``duidir.com`` on the day this was written) that
    it rotates, and only a list maintained faster than the rotation sees them.

* One **allowlist**, ``disposable_allowlist.txt``: a domain there (or a
  subdomain of one) is always accepted, whatever the denylists say — the
  community list is broad and has carried a real provider before.

* A domain matches when it, or any parent domain of it, is listed — so
  ``anything.mailinator.com`` is refused along with ``mailinator.com``.
* It is deliberately **not** a guess about unfamiliar domains: a personal or
  company domain nobody here has heard of is a perfectly good permanent
  address. Only listed domains are refused.
* It **cannot** catch every temporary domain — providers register new ones
  daily, and a domain rotated in after the last refresh passes until the
  next one. It raises the bar; it is not a guarantee, and nothing here
  claims otherwise. No network is used at sign-up time.

The check runs in :meth:`auth.firebase.FirebaseAuth.sign_up`, on the server,
before Firebase is asked to create anything — so a refused sign-up leaves no
account and no document behind. The form checks first as well, only so the
person hears about it without a round trip.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from auth.firebase import EMAIL_RE

HERE = Path(__file__).parent
DENYLIST_FILE = HERE / "disposable_domains.txt"
COMMUNITY_FILE = HERE / "disposable_domains.community.txt"
ALLOWLIST_FILE = HERE / "disposable_allowlist.txt"

#: What the person is told. Deliberately says nothing about which list, or why
#: this domain — that is the denylist's business, not the sign-up form's.
MESSAGE = "Please use a permanent email address. Disposable or temporary email addresses are not supported."


def normalise_email(email: str) -> str:
    """Trim, and lower-case the domain (the part after the last ``@``).

    The local part is left as typed: mail servers are allowed to treat it as
    case-sensitive, and Firebase keeps what it was given. "" when the value
    is not shaped like an email address at all.
    """
    email = (email or "").strip()
    if not EMAIL_RE.match(email):
        return ""
    local, _, domain = email.rpartition("@")
    return f"{local}@{domain.lower()}"


def domain_of(email: str) -> str:
    """The lower-cased domain of a well-formed address, else ""."""
    normalised = normalise_email(email)
    return normalised.rpartition("@")[2] if normalised else ""


def _read_domains(path: Path) -> frozenset[str]:
    """One domain per line, ``#`` comments, lower-cased; empty if unreadable."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return frozenset()
    domains = set()
    for line in lines:
        entry = line.split("#", 1)[0].strip().lower()
        if entry:
            domains.add(entry)
    return frozenset(domains)


@lru_cache(maxsize=1)
def denylist() -> frozenset[str]:
    """Every listed domain — the curated list and the vendored community
    list together — read once per process."""
    return _read_domains(DENYLIST_FILE) | _read_domains(COMMUNITY_FILE)


@lru_cache(maxsize=1)
def allowlist() -> frozenset[str]:
    return _read_domains(ALLOWLIST_FILE)


def _listed(domain: str, listed: frozenset[str]) -> bool:
    """Is the domain, or any parent domain of it, in ``listed``?"""
    labels = domain.split(".")
    return any(".".join(labels[i:]) in listed for i in range(len(labels) - 1))


def is_disposable(email: str) -> bool:
    """Is this address on a listed disposable domain (or a subdomain of one)?

    A malformed address is not "disposable" — it is malformed, and the
    caller's own validation says so. False for it, so the two messages never
    trade places. An allowlisted domain is never disposable.
    """
    domain = domain_of(email)
    if not domain:
        return False
    if _listed(domain, allowlist()):
        return False
    return _listed(domain, denylist())


__all__ = ["ALLOWLIST_FILE", "COMMUNITY_FILE", "DENYLIST_FILE", "MESSAGE", "allowlist", "denylist",
           "domain_of", "is_disposable", "normalise_email"]
