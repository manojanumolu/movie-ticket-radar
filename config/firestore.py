"""Cloud Firestore over REST — user-owned data, keyed on the Firebase UID.

Two callers, two credentials, one client:

* **The Streamlit app** talks to Firestore as the signed-in person, with the
  Firebase *ID token* their sign-in produced. Firestore's security rules
  (``firestore.rules``) then only let that token read and write documents
  whose ``owner_uid`` is the token's UID. No service account exists in the
  app, so there is nothing there that could bypass the rules.
* **The worker** (GitHub Actions) needs every active monitor, whoever owns
  it, so it authenticates with a service account handed to it as a secret
  (``FIREBASE_SERVICE_ACCOUNT``, the JSON, in the environment only). The
  Admin path bypasses rules by design; it is the only privileged caller and
  it never runs where a browser could reach it.

The REST surface used is deliberately tiny — ``:runQuery``, ``:commit`` and
``GET`` of one document — so a test can stand in for the whole service with
a small in-memory fake. Documents are plain dicts; Firestore's typed value
format is folded away by :func:`encode` / :func:`decode`.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests
from requests.adapters import HTTPAdapter

BASE = "https://firestore.googleapis.com/v1"
TIMEOUT = 15.0
#: Every user-owned document carries this field; the rules and the client
#: both key ownership on it.
OWNER = "owner_uid"

#: Firestore forbids an array directly inside an array, and two of our fields
#: are naturally lists of pairs — ``variants`` is ``[[code, label], …]`` and
#: ``time_links`` is ``[[label, url], …]``. Writing them as they stand is
#: rejected with INVALID_ARGUMENT, so on the wire each pair becomes a small
#: map with these keys, and comes back as a pair on the way out. The
#: application's own representation never changes, and neither does the JSON
#: store: this is purely how the two fields are spelled inside Firestore.
PAIR_FIELDS: dict[str, tuple[str, str]] = {
    "variants": ("code", "label"),
    "time_links": ("label", "url"),
}


class FirestoreError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


# ──────────────────────────────────────────────────────────────────────────
# Values
# ──────────────────────────────────────────────────────────────────────────
def _is_pair_list(value: Any) -> bool:
    """A non-empty list whose every item is a two-item sequence."""
    if not isinstance(value, (list, tuple)) or not value:
        return False
    return all(isinstance(item, (list, tuple)) and len(item) == 2 for item in value)


def encode(value: Any, *, field: str = "") -> dict[str, Any]:
    """A Python value → Firestore's typed ``Value``.

    ``field`` is the name the value is stored under. It matters only for
    :data:`PAIR_FIELDS`, whose list-of-pairs shape has to become a list of
    maps — Firestore will not take an array inside an array.
    """
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, (list, tuple)):
        if field in PAIR_FIELDS and _is_pair_list(value):
            first, second = PAIR_FIELDS[field]
            return {"arrayValue": {"values": [
                {"mapValue": {"fields": {first: encode(pair[0]), second: encode(pair[1])}}}
                for pair in value
            ]}}
        return {"arrayValue": {"values": [encode(v) for v in value]}}
    if isinstance(value, dict):
        # The key travels with the value so a pair field is recognised however
        # deeply it sits — ``movie.variants``, ``targets.<key>.time_links``.
        return {"mapValue": {"fields": {str(k): encode(v, field=str(k)) for k, v in value.items()}}}
    return {"stringValue": str(value)}


def decode(value: dict[str, Any], *, field: str = "") -> Any:
    """Firestore's typed ``Value`` → a Python value.

    A :data:`PAIR_FIELDS` field comes back as the list of pairs the
    application has always seen, whichever way it was written — a document
    stored before this encoding still reads correctly.
    """
    if "nullValue" in value:
        return None
    if "booleanValue" in value:
        return bool(value["booleanValue"])
    if "integerValue" in value:
        return int(value["integerValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "stringValue" in value:
        return value["stringValue"]
    if "timestampValue" in value:
        return value["timestampValue"]
    if "arrayValue" in value:
        items = value["arrayValue"].get("values") or []
        if field in PAIR_FIELDS:
            first, second = PAIR_FIELDS[field]
            pairs = []
            for item in items:
                if isinstance(item, dict) and "mapValue" in item:
                    inner = item["mapValue"].get("fields") or {}
                    pairs.append([decode(inner.get(first, {"stringValue": ""})),
                                  decode(inner.get(second, {"stringValue": ""}))])
                else:                      # an older document, or a plain value
                    pairs.append(decode(item))
            return pairs
        return [decode(v) for v in items]
    if "mapValue" in value:
        return {k: decode(v, field=k) for k, v in (value["mapValue"].get("fields") or {}).items()}
    return None


def fields_of(document: dict[str, Any]) -> dict[str, Any]:
    return {k: decode(v, field=k) for k, v in (document.get("fields") or {}).items()}


def doc_id(document: dict[str, Any]) -> str:
    return str(document.get("name", "")).rsplit("/", 1)[-1]


# ──────────────────────────────────────────────────────────────────────────
# Credentials
# ──────────────────────────────────────────────────────────────────────────
TokenGetter = Callable[[], str]


def service_account_token_getter(info: dict[str, Any]) -> TokenGetter:
    """An OAuth2 access token for the worker, from service-account JSON.
    ``google-auth`` is imported here and nowhere else, so the app never
    needs it and the worker only needs it when it actually has a key."""
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/datastore"])
    state: dict[str, Any] = {"token": "", "expires": 0.0}

    def token() -> str:
        if not state["token"] or time.time() > state["expires"]:
            creds.refresh(Request())
            state["token"] = creds.token
            expiry = creds.expiry.timestamp() if creds.expiry else time.time() + 3000
            state["expires"] = expiry - 120
        return str(state["token"])

    return token


def service_account_status() -> tuple[dict[str, Any] | None, str]:
    """``(info, problem)`` for ``FIREBASE_SERVICE_ACCOUNT``.

    ``problem`` is ``""`` when the value is structurally usable and otherwise
    says *what* is wrong with it — never any part of what it contains. It
    exists because a worker that cannot use its credential falls back to the
    legacy JSON files, and that fallback is indistinguishable from a healthy
    idle run unless somebody says out loud why it happened.

    The acceptance test is unchanged: a JSON object with a ``client_email``.
    """
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
    if not raw:
        return None, "FIREBASE_SERVICE_ACCOUNT is not set"
    try:
        info = json.loads(raw)
    except ValueError:
        return None, "FIREBASE_SERVICE_ACCOUNT is not valid JSON"
    if not isinstance(info, dict):
        return None, "FIREBASE_SERVICE_ACCOUNT is not a JSON object"
    if not info.get("client_email"):
        # By far the easiest one to do by accident: the Firebase *web app*
        # config is also JSON, also comes from the Firebase console, and has
        # no signing identity in it at all.
        return None, ("FIREBASE_SERVICE_ACCOUNT has no client_email — this looks like a "
                      "Firebase web app config rather than a service-account key")
    return info, ""


def service_account_from_env() -> dict[str, Any] | None:
    """The worker's credential: ``FIREBASE_SERVICE_ACCOUNT`` holds the JSON
    (a GitHub Actions secret). Never a file in the repository."""
    info, problem = service_account_status()
    if problem == "FIREBASE_SERVICE_ACCOUNT is not valid JSON":
        print(f"[firestore] {problem}", flush=True)
    return info


def project_from_env() -> str:
    return os.environ.get("FIREBASE_PROJECT_ID", "").strip()


# ──────────────────────────────────────────────────────────────────────────
# Transport — one function, so the tests can replace the service
# ──────────────────────────────────────────────────────────────────────────
#: One connection pool for the whole process, shared by every thread.
#:
#: ``requests.request`` opens a Session, uses it once and closes it, so every
#: Firestore call paid for a fresh TCP connection and TLS handshake. Keeping
#: the connection alive removes that handshake — but only if the thing holding
#: it outlives the caller, and this is where a thread-local one was not enough.
#:
#: Streamlit gives each browser interaction a **new script thread**: with
#: ``runner.fastReruns`` (the default, and this app does not override it) a
#: rerun stops the current ScriptRunner and starts another, and the old
#: thread exits. A thread-local session therefore began every click with an
#: empty pool and paid a full handshake — measured at ~490 ms against the live
#: endpoint, on top of the round trip, once per click. Only the *pool* kept at
#: module scope survives that.
#:
#: The PoolManager inside an ``HTTPAdapter`` is built for concurrent use, so
#: the adapter is the shared part. Each thread still gets its own
#: ``requests.Session``, so no requests-level state — cookies, default
#: headers, auth — is ever shared between threads, and so between users. What
#: they share is TCP connections to firestore.googleapis.com, which carry no
#: identity of their own: authorization is a per-request header, set below in
#: :func:`_http`, and Firestore evaluates its rules per request against that
#: token.
#:
#: ``pool_maxsize`` has room for several concurrent reads across several
#: simultaneous browser sessions; beyond it urllib3 opens a connection per
#: request as before, so the ceiling costs latency, never correctness.
POOL_MAXSIZE = 32
_ADAPTER = HTTPAdapter(pool_connections=4, pool_maxsize=POOL_MAXSIZE)
_sessions = threading.local()


def _session() -> requests.Session:
    session = getattr(_sessions, "session", None)
    if session is None:
        session = requests.Session()
        # Mount the shared adapter over both schemes so nothing can fall back
        # to this session's own private pool.
        session.mount("https://", _ADAPTER)
        session.mount("http://", _ADAPTER)
        _sessions.session = session
    return session


def _http(method: str, url: str, token: str, body: dict[str, Any] | None) -> tuple[int, Any]:
    response = _session().request(method, url, json=body, timeout=TIMEOUT,
                                  headers={"Authorization": f"Bearer {token}"})
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    return response.status_code, payload


# ──────────────────────────────────────────────────────────────────────────
# Client
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class FirestoreClient:
    project_id: str
    token: TokenGetter
    transport: Callable[..., tuple[int, Any]] = _http

    @property
    def root(self) -> str:
        return f"projects/{self.project_id}/databases/(default)/documents"

    def _url(self, suffix: str) -> str:
        return f"{BASE}/{self.root}{suffix}"

    def _call(self, method: str, suffix: str, body: dict[str, Any] | None = None) -> Any:
        try:
            status, payload = self.transport(method, self._url(suffix), self.token(), body)
        except (requests.RequestException, OSError) as exc:
            raise FirestoreError(f"Firestore unreachable ({type(exc).__name__})") from None
        if status == 404 and method == "GET":
            return None
        if status >= 400:
            error = (payload or {}).get("error") if isinstance(payload, dict) else None
            message = (error or {}).get("message") or f"HTTP {status}"
            print(f"[firestore] {method} {suffix.split('?')[0]}: HTTP {status} {message[:120]}", flush=True)
            raise FirestoreError(str(message), status)
        return payload

    # -- documents ----------------------------------------------------------
    def get(self, collection: str, doc: str) -> dict[str, Any] | None:
        payload = self._call("GET", f"/{collection}/{doc}")
        return fields_of(payload) if payload else None

    def query(self, collection: str, *, equals: dict[str, Any] | None = None,
              limit: int = 0) -> dict[str, dict[str, Any]]:
        """``{doc_id: fields}`` for a collection, optionally filtered on
        equality — only ever ``owner_uid`` or ``status`` here, each of which
        Firestore indexes on its own."""
        query: dict[str, Any] = {"from": [{"collectionId": collection}]}
        filters = [
            {"fieldFilter": {"field": {"fieldPath": k}, "op": "EQUAL", "value": encode(v)}}
            for k, v in (equals or {}).items()
        ]
        if len(filters) == 1:
            query["where"] = filters[0]
        elif filters:
            query["where"] = {"compositeFilter": {"op": "AND", "filters": filters}}
        if limit:
            query["limit"] = limit
        rows = self._call("POST", ":runQuery", {"structuredQuery": query}) or []
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            document = row.get("document") if isinstance(row, dict) else None
            if document:
                out[doc_id(document)] = fields_of(document)
        return out

    def commit(self, writes: list[tuple[str, str, dict[str, Any] | None]]) -> None:
        """Apply ``[(collection, doc, fields-or-None-to-delete), …]`` atomically."""
        if not writes:
            return
        body = {"writes": [
            {"delete": f"{self.root}/{collection}/{doc}"} if fields is None else
            {"update": {"name": f"{self.root}/{collection}/{doc}",
                        "fields": {k: encode(v, field=k) for k, v in fields.items()}}}
            for collection, doc, fields in writes
        ]}
        # Firestore accepts up to 500 writes per commit; ours are a handful.
        self._call("POST", ":commit", body)

    def set(self, collection: str, doc: str, fields: dict[str, Any]) -> None:
        self.commit([(collection, doc, fields)])

    def delete(self, collection: str, doc: str) -> None:
        self.commit([(collection, doc, None)])


__all__ = [
    "OWNER",
    "PAIR_FIELDS",
    "FirestoreClient",
    "FirestoreError",
    "decode",
    "encode",
    "project_from_env",
    "service_account_from_env",
    "service_account_status",
    "service_account_token_getter",
]
