"""Disposable / temporary email domains — refused at sign-up.

Why
---
Sign-up requires a verified address, but a throwaway inbox can receive the
verification email just as well as a real one. Refusing the well-known
temporary-mail providers keeps accounts on addresses people actually keep.

What this is, and is not
------------------------
* A **denylist**, kept in ``disposable_domains.txt`` next to this module: one
  domain per line, ``#`` comments, auditable in a diff. No network call, no
  paid API, no package.
* A domain matches when it, or any parent domain of it, is listed — so
  ``anything.mailinator.com`` is refused along with ``mailinator.com``.
* It is deliberately **not** a guess about unfamiliar domains: a personal or
  company domain nobody here has heard of is a perfectly good permanent
  address. Only listed domains are refused.
* It **cannot** catch every temporary domain — new ones appear daily. It
  raises the bar; it is not a guarantee, and nothing here claims otherwise.

The check runs in :meth:`auth.firebase.FirebaseAuth.sign_up`, on the server,
before Firebase is asked to create anything — so a refused sign-up leaves no
account and no document behind. The form checks first as well, only so the
person hears about it without a round trip.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from auth.firebase import EMAIL_RE

DENYLIST_FILE = Path(__file__).parent / "disposable_domains.txt"

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


@lru_cache(maxsize=1)
def denylist() -> frozenset[str]:
    """The listed domains, read once per process."""
    try:
        lines = DENYLIST_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return frozenset()
    domains = set()
    for line in lines:
        entry = line.split("#", 1)[0].strip().lower()
        if entry:
            domains.add(entry)
    return frozenset(domains)


def is_disposable(email: str) -> bool:
    """Is this address on a listed disposable domain (or a subdomain of one)?

    A malformed address is not "disposable" — it is malformed, and the
    caller's own validation says so. False for it, so the two messages never
    trade places.
    """
    domain = domain_of(email)
    if not domain:
        return False
    listed = denylist()
    labels = domain.split(".")
    return any(".".join(labels[i:]) in listed for i in range(len(labels) - 1))


__all__ = ["DENYLIST_FILE", "MESSAGE", "denylist", "domain_of", "is_disposable", "normalise_email"]
