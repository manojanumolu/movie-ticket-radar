"""Per-user data: Firestore keyed on the Firebase UID.

Firestore is replaced by :class:`MemoryFirestore`, an in-memory stand-in for
the three REST calls the client makes (``GET`` a document, ``:runQuery``,
``:commit``) that also *enforces the rules in* ``firestore.rules`` the way
the service would: a person's ID token (``id.<uid>``) can only read and
write documents whose ``owner_uid`` is that UID, and can't query without
filtering on it; the worker's token (``admin``) bypasses the rules, as a
service account does.

Two things are therefore proven here, and it matters which is which:

* the *code* — the app never asks for anything but the signed-in person's
  documents, the worker writes each state document to its monitor's owner,
  history goes to the monitor's owner, settings live under ``users/{uid}``;
* the *rules* — a forged request outside those lines is refused by the
  simulated rules, which are a transcription of ``firestore.rules``.
  The real rules only bind once they are published in the Firebase console;
  ``test_the_rules_file_says_what_the_fake_enforces`` pins the file's text.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from config import firestore as fs
from config.timezone import now_ist
from monitor import state as state_mod
from monitor.models import ANY_FORMAT, TheatreTarget
from monitor.state import MonitorState, Scope

PROJECT = "ticketradar-test"
ROOT = f"{fs.BASE}/projects/{PROJECT}/databases/(default)/documents"


# ──────────────────────────────────────────────────────────────────────────
# The fake service
# ──────────────────────────────────────────────────────────────────────────
class Denied(Exception):
    pass


class MemoryFirestore:
    """``transport(method, url, token, body) -> (status, payload)``."""

    RULES_WRITE_DENIED = {"monitor_state"}          # users never create/update these
    RULES_UPDATE_DENIED = {"history"}               # append-only for users

    def __init__(self):
        self.docs: dict[str, dict[str, dict]] = {}   # collection -> id -> fields
        self.calls: list[tuple[str, str, str]] = []  # (method, path, who)

    # -- rules ------------------------------------------------------------
    @staticmethod
    def _who(token: str) -> str | None:
        """None = the worker's service account (rules bypassed)."""
        if token == "admin":
            return None
        if token.startswith("id."):
            return token[3:]
        raise Denied("unauthenticated")

    def _may_read(self, who, collection, fields) -> bool:
        if who is None:
            return True
        if collection == "users":
            return True  # checked by id below
        return fields.get(fs.OWNER) == who

    # -- transport --------------------------------------------------------
    def __call__(self, method: str, url: str, token: str, body):
        assert url.startswith(ROOT), url
        path = url[len(ROOT):]
        try:
            who = self._who(token)
        except Denied:
            return 401, {"error": {"message": "UNAUTHENTICATED"}}
        self.calls.append((method, path, who or "admin"))
        try:
            if method == "GET":
                return self._get(who, path)
            if path == ":runQuery":
                return self._query(who, body)
            if path == ":commit":
                return self._commit(who, body)
        except Denied as exc:
            return 403, {"error": {"message": f"PERMISSION_DENIED: {exc}"}}
        raise AssertionError(f"unexpected {method} {path}")

    def _get(self, who, path):
        collection, doc = path.strip("/").split("/")
        fields = self.docs.get(collection, {}).get(doc)
        if fields is None:
            return 404, {"error": {"message": "NOT_FOUND"}}
        if collection == "users" and who is not None and doc != who:
            raise Denied("not your user document")
        if not self._may_read(who, collection, fields):
            raise Denied("not the owner")
        return 200, {"name": f"{ROOT}/{collection}/{doc}",
                     "fields": {k: fs.encode(v) for k, v in fields.items()}}

    def _query(self, who, body):
        q = body["structuredQuery"]
        collection = q["from"][0]["collectionId"]
        where = q.get("where") or {}
        filters = ([where["fieldFilter"]] if "fieldFilter" in where else
                   [f["fieldFilter"] for f in where.get("compositeFilter", {}).get("filters", [])])
        equals = {f["field"]["fieldPath"]: fs.decode(f["value"]) for f in filters}
        if who is not None and equals.get(fs.OWNER) != who:
            # Firestore refuses a query the rules can't prove safe.
            raise Denied("query is not restricted to the caller's documents")
        rows = []
        for doc, fields in self.docs.get(collection, {}).items():
            if all(fields.get(k) == v for k, v in equals.items()):
                rows.append({"document": {"name": f"{ROOT}/{collection}/{doc}",
                                          "fields": {k: fs.encode(v) for k, v in fields.items()}}})
        limit = q.get("limit")
        return 200, rows[:limit] if limit else rows

    def _commit(self, who, body):
        staged = []
        for write in body["writes"]:
            if "delete" in write:
                collection, doc = write["delete"].split("/")[-2:]
                existing = self.docs.get(collection, {}).get(doc)
                if who is not None and existing is not None:
                    if collection == "users":
                        if doc != who:
                            raise Denied("not your user document")
                    elif collection in self.RULES_UPDATE_DENIED or existing.get(fs.OWNER) != who:
                        raise Denied("delete of a document you don't own")
                staged.append((collection, doc, None))
            else:
                collection, doc = write["update"]["name"].split("/")[-2:]
                fields = {k: fs.decode(v) for k, v in write["update"]["fields"].items()}
                existing = self.docs.get(collection, {}).get(doc)
                if who is not None:
                    if collection == "users":
                        if doc != who:
                            raise Denied("not your user document")
                    elif collection in self.RULES_WRITE_DENIED:
                        raise Denied(f"{collection} is written by the worker only")
                    elif fields.get(fs.OWNER) != who:
                        raise Denied("owner_uid must be the caller")
                    elif existing is not None and (collection in self.RULES_UPDATE_DENIED
                                                   or existing.get(fs.OWNER) != who):
                        raise Denied("update of a document you don't own")
                staged.append((collection, doc, fields))
        for collection, doc, fields in staged:            # atomic: nothing applied on a denial
            if fields is None:
                self.docs.get(collection, {}).pop(doc, None)
            else:
                self.docs.setdefault(collection, {})[doc] = fields
        return 200, {"writeResults": [{} for _ in staged]}


# ──────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────
@pytest.fixture
def cloud(monkeypatch):
    """A configured project, the fake service, and helpers to act as a
    person (``as_user``) or as the worker (``as_worker``)."""
    store = MemoryFirestore()
    monkeypatch.setattr(state_mod, "_transport", store)
    monkeypatch.setattr(state_mod, "_admin", None)
    monkeypatch.setattr(state_mod, "_owner_of", {})
    monkeypatch.setenv("FIREBASE_PROJECT_ID", PROJECT)

    class Cloud:
        docs = store.docs
        calls = store.calls

        @staticmethod
        def as_user(uid: str) -> None:
            state_mod.set_scope_provider(lambda: Scope(PROJECT, uid, lambda: f"id.{uid}"))

        @staticmethod
        def as_worker() -> None:
            state_mod.set_scope_provider(None)
            client = fs.FirestoreClient(PROJECT, lambda: "admin", transport=store)
            monkeypatch.setattr(state_mod, "_admin", state_mod._FirestoreStore(client, uid=None))
            monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT", '{"client_email": "worker@test", "type": "service_account"}')

        @staticmethod
        def as_nobody() -> None:
            state_mod.set_scope_provider(None)
            monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT", raising=False)

    yield Cloud
    state_mod.set_scope_provider(None)


def monitor_for(make_monitor, uid: str, email: str):
    m = make_monitor(email=email)
    m.owner_uid = uid
    return m


# ──────────────────────────────────────────────────────────────────────────
# 1–2. A new account starts empty
# ──────────────────────────────────────────────────────────────────────────
def test_a_new_account_starts_with_nothing(cloud):
    cloud.as_user("uid-new")
    assert state_mod.backend_name() == "firestore"
    assert state_mod.load_monitors() == []
    assert state_mod.load_history() == []
    assert state_mod.load_state() == {}
    assert state_mod.load_settings() == {"notify_email": "", "default_interval": 10}


def test_the_legacy_json_files_are_not_read_once_firestore_is_the_store(cloud, make_monitor):
    """The old global data/*.json stays in the repository as test data and
    is invisible to every account."""
    cloud.as_nobody()
    assert state_mod.backend_name() == "json"
    state_mod.upsert_monitor(make_monitor(), mirror=False)          # a legacy, ownerless record
    state_mod.record_history(make_monitor(), "CREATED", "legacy", mirror=False)
    cloud.as_user("uid-new")
    assert state_mod.load_monitors() == [] and state_mod.load_history() == []


# ──────────────────────────────────────────────────────────────────────────
# 3–5, 7. Ownership is the UID, and nothing crosses it
# ──────────────────────────────────────────────────────────────────────────
def test_creating_a_monitor_stores_the_owner_uid(cloud, make_monitor):
    cloud.as_user("uid-a")
    m = make_monitor(email="a@example.com")                          # no owner set by the caller…
    state_mod.upsert_monitor(m, mirror=False)
    assert cloud.docs["monitors"][m.id][fs.OWNER] == "uid-a"          # …the store stamps the caller's UID
    assert m.owner_uid == "uid-a"
    [stored] = state_mod.load_monitors()
    assert stored.id == m.id and stored.owner_uid == "uid-a" and stored.notify_email == "a@example.com"


def test_user_b_cannot_read_user_a_s_monitors_state_history_or_settings(cloud, make_monitor):
    cloud.as_user("uid-a")
    a = monitor_for(make_monitor, "uid-a", "a@example.com")
    state_mod.upsert_monitor(a, mirror=False)
    state_mod.record_history(a, "CREATED", "Monitor created.", mirror=False)
    state_mod.save_settings({"notify_email": "a@example.com", "default_interval": 15}, mirror=False)
    cloud.as_worker()
    state_mod.save_state({a.id: MonitorState(check_count=3)}, mirror=False)

    cloud.as_user("uid-b")
    assert state_mod.load_monitors() == []
    assert state_mod.get_monitor(a.id) is None
    assert state_mod.load_history() == []
    assert state_mod.load_state() == {}
    assert state_mod.get_monitor_state(a.id).check_count == 0
    assert state_mod.load_settings()["notify_email"] == ""
    # B cannot stop, extend or delete A's monitor either — it simply isn't there for B.
    assert state_mod.stop_monitor(a.id, mirror=False) is None
    assert state_mod.extend_monitor(a.id, mirror=False) is None
    state_mod.delete_monitor(a.id, mirror=False)
    assert a.id in cloud.docs["monitors"] and a.id in cloud.docs["monitor_state"]
    # And A still sees everything.
    cloud.as_user("uid-a")
    assert [m.id for m in state_mod.load_monitors()] == [a.id]
    assert state_mod.load_history()[0]["message"] == "Monitor created."
    assert state_mod.load_state()[a.id].check_count == 3
    assert state_mod.load_settings()["default_interval"] == 15


def test_a_person_cannot_write_under_another_uid(cloud, make_monitor):
    cloud.as_user("uid-b")
    stolen = monitor_for(make_monitor, "uid-a", "a@example.com")
    with pytest.raises(PermissionError):
        state_mod.upsert_monitor(stolen, mirror=False)
    assert "monitors" not in cloud.docs


def test_the_simulated_rules_refuse_forged_requests(cloud):
    """Straight at the service, past the app's own checks: what the rules
    in firestore.rules do to a request that isn't the owner's."""
    client = fs.FirestoreClient(PROJECT, lambda: "id.uid-b", transport=state_mod._transport)
    cloud.docs.setdefault("monitors", {})["m1"] = {fs.OWNER: "uid-a", "movie": {}}
    cloud.docs.setdefault("users", {})["uid-a"] = {"notify_email": "a@example.com"}
    with pytest.raises(fs.FirestoreError, match="PERMISSION_DENIED"):
        client.query("monitors")                                     # unfiltered → refused
    with pytest.raises(fs.FirestoreError, match="PERMISSION_DENIED"):
        client.get("monitors", "m1")                                 # somebody else's document
    with pytest.raises(fs.FirestoreError, match="PERMISSION_DENIED"):
        client.set("monitors", "m2", {fs.OWNER: "uid-a"})            # writing under another UID
    with pytest.raises(fs.FirestoreError, match="PERMISSION_DENIED"):
        client.set("monitor_state", "m2", {fs.OWNER: "uid-b"})       # worker-only collection
    with pytest.raises(fs.FirestoreError, match="PERMISSION_DENIED"):
        client.get("users", "uid-a")                                 # another person's settings
    assert client.query("monitors", equals={fs.OWNER: "uid-b"}) == {}


def test_the_rules_file_says_what_the_fake_enforces():
    rules = Path("firestore.rules").read_text(encoding="utf-8")
    assert "request.auth != null" in rules
    assert "resource.data.owner_uid == request.auth.uid" in rules
    assert "request.resource.data.owner_uid == request.auth.uid" in rules
    assert re.search(r"match /users/\{uid\} \{\s*allow read, write: if signedIn\(\) && request.auth.uid == uid;", rules)
    assert re.search(r"match /monitor_state/\{id\} \{[^}]*allow create, update: if false;", rules, re.S)
    assert re.search(r"match /history/\{id\} \{[^}]*allow update, delete: if false;", rules, re.S)
    assert re.search(r"match /\{document=\*\*\} \{\s*allow read, write: if false;", rules)


# ──────────────────────────────────────────────────────────────────────────
# 6. Shared data stays shared
# ──────────────────────────────────────────────────────────────────────────
def test_the_catalogue_is_global_and_untouched_by_the_user_store(cloud, provider_factory, monkeypatch):
    from monitor import catalogue
    from tests.conftest import QUICKBOOK_HYD, build_quickbook

    monkeypatch.setattr(catalogue, "get_provider", lambda slug: provider_factory([QUICKBOOK_HYD]))
    catalogue.sync_region("hyderabad", mirror=False, detail=False)
    cloud.as_user("uid-a")
    titles_a = {catalogue.movie_from_entry(e).title for e in catalogue.list_entries("hyderabad")}
    cloud.as_user("uid-b")
    titles_b = {catalogue.movie_from_entry(e).title for e in catalogue.list_entries("hyderabad")}
    assert titles_a == titles_b == {"Mandaadi", "Hanuman Ansh"}
    assert "catalogue" not in cloud.docs                              # never went near Firestore
    assert build_quickbook([]) is not None


# ──────────────────────────────────────────────────────────────────────────
# 8–9. The worker sees every owner's monitors and notifies each owner
# ──────────────────────────────────────────────────────────────────────────
def test_the_worker_processes_every_owners_monitors_and_notifies_each_owner(cloud, make_monitor, provider_factory, monkeypatch, at):
    from monitor import checker
    from tests.conftest import ALLU_LIVE, NOT_ON_SALE, build_payload

    cloud.as_user("uid-a")
    a = monitor_for(make_monitor, "uid-a", "a@example.com")
    a.targets = [TheatreTarget("ALLU", "Allu Cinemas", "Attapur, Hyderabad", ANY_FORMAT)]
    state_mod.upsert_monitor(a, mirror=False)
    cloud.as_user("uid-b")
    b = monitor_for(make_monitor, "uid-b", "b@example.com")
    b.targets = [TheatreTarget("ALLU", "Allu Cinemas", "Attapur, Hyderabad", ANY_FORMAT)]
    state_mod.upsert_monitor(b, mirror=False)

    cloud.as_worker()
    assert {m.id for m in state_mod.load_monitors()} == {a.id, b.id}
    sent: list[tuple[str, str]] = []

    def notifier(monitor, change, *args, **kwargs):
        sent.append((monitor.notify_email, monitor.owner_uid))
        return True

    # First pass: nothing on sale yet, for both.
    monkeypatch.setattr(checker, "get_provider",
                        lambda slug: provider_factory([build_payload(NOT_ON_SALE), build_payload(NOT_ON_SALE)]))
    checker.run_once(at=at, notifier=notifier, force=True)
    assert set(cloud.docs["monitor_state"]) == {a.id, b.id}
    assert cloud.docs["monitor_state"][a.id][fs.OWNER] == "uid-a"     # each state document to its owner
    assert cloud.docs["monitor_state"][b.id][fs.OWNER] == "uid-b"
    # Second pass: tickets go live — each owner is told at their own address.
    monkeypatch.setattr(checker, "get_provider",
                        lambda slug: provider_factory([build_payload(ALLU_LIVE), build_payload(ALLU_LIVE)]))
    report = checker.run_once(at=at + timedelta(minutes=15), notifier=notifier, force=True)
    assert sorted(sent) == [("a@example.com", "uid-a"), ("b@example.com", "uid-b")]
    assert not report.failed
    # History landed with each owner, and each sees only their own.
    cloud.as_user("uid-a")
    assert {h["monitor_id"] for h in state_mod.load_history()} == {a.id}
    assert state_mod.load_state()[a.id].check_count == 2
    cloud.as_user("uid-b")
    assert {h["monitor_id"] for h in state_mod.load_history()} == {b.id}


def test_the_worker_expires_and_keeps_the_owner(cloud, make_monitor):
    cloud.as_user("uid-a")
    a = monitor_for(make_monitor, "uid-a", "a@example.com")
    a.monitor_until = now_ist() - timedelta(minutes=1)
    state_mod.upsert_monitor(a, mirror=False)
    cloud.as_worker()
    _, expired = state_mod.expire_due_monitors(mirror=False)
    assert [m.id for m in expired] == [a.id]
    assert cloud.docs["monitors"][a.id][fs.OWNER] == "uid-a" and cloud.docs["monitors"][a.id]["status"] == "EXPIRED"
    cloud.as_user("uid-a")
    assert state_mod.load_history()[0]["kind"] == "EXPIRED"


def test_history_is_capped_per_person_and_newest_first(cloud, make_monitor):
    cloud.as_user("uid-a")
    a = monitor_for(make_monitor, "uid-a", "a@example.com")
    base = now_ist()
    for i in range(60):
        state_mod.record_history(a, "NOTE", f"event {i}", at=base + timedelta(minutes=i), mirror=False)
    history = state_mod.load_history()
    assert len(history) == state_mod.HISTORY_LIMIT
    assert history[0]["message"] == "event 59"


# ──────────────────────────────────────────────────────────────────────────
# Through the app: My Monitors and History are the signed-in person's
# ──────────────────────────────────────────────────────────────────────────
AppTest = pytest.importorskip("streamlit.testing.v1").AppTest


def test_my_monitors_and_history_show_only_the_signed_in_persons_records(cloud, make_monitor, monkeypatch):
    from auth import firebase, session
    from tests.test_auth import FakeFirebase, body, settle

    fb = FakeFirebase()
    fb.add("ravi@example.com", "Popcorn2026", uid="uid-ravi", name="Ravi Teja")
    fb.add("sita@example.com", "Interval99", uid="uid-sita", name="Sita Devi")
    monkeypatch.setenv("FIREBASE_WEB_API_KEY", "test-web-api-key")
    monkeypatch.setattr(firebase, "_post", fb)
    monkeypatch.setattr(session, "restore", lambda: None)

    cloud.as_user("uid-ravi")
    ravi = monitor_for(make_monitor, "uid-ravi", "ravi@example.com")
    ravi.movie = replace(ravi.movie, title="Ravi's Film")
    state_mod.upsert_monitor(ravi, mirror=False)
    state_mod.record_history(ravi, "CREATED", "Monitor created.", mirror=False)

    def sign_in(app, email, password):
        app.text_input(key="auth_email").set_value(email)
        app.text_input(key="auth_password").set_value(password)
        app.button(key="auth_signin").click().run()
        return settle(app)

    def page(app, name):
        app.session_state["page"] = name
        return app.run()

    app = AppTest.from_file("app.py", default_timeout=60).run()      # registers the app's own scope
    app = sign_in(app, "sita@example.com", "Interval99")
    assert app.session_state["auth_user"].uid == "uid-sita"
    assert "Ravi's Film" not in body(page(app, "My Monitors"))
    assert "Nothing yet" in body(page(app, "History")) and "Ravi's Film" not in body(app)
    assert "Ravi's Film" not in body(page(app, "Home"))

    app.button(key="auth_signout").click().run()
    app = settle(app)
    app = sign_in(app, "ravi@example.com", "Popcorn2026")
    assert app.session_state["auth_user"].uid == "uid-ravi"
    assert "Ravi's Film" in body(page(app, "My Monitors"))
    assert "Ravi's Film" in body(page(app, "History"))
    # Every Firestore call the app made carried Ravi's or Sita's own token,
    # filtered on their own UID — never an unscoped read.
    assert all(who in ("uid-ravi", "uid-sita") for _, _, who in cloud.calls)


def test_the_app_scope_is_the_verified_uid_and_nothing_else(monkeypatch):
    """``app._scope()`` answers from the Firebase session only."""
    import importlib
    import streamlit as st

    from auth import firebase, session

    monkeypatch.setenv("FIREBASE_PROJECT_ID", PROJECT)
    monkeypatch.setattr(st, "session_state", {})
    app = importlib.import_module("app") if "app" in __import__("sys").modules else None
    if app is None:
        pytest.skip("app.py is exercised through AppTest above")
    assert app._scope() is None                                       # nobody signed in
    st.session_state[session.USER_KEY] = session.AuthUser(uid="uid-x", email="x@example.com")
    scope = app._scope()
    assert (scope.project_id, scope.uid) == (PROJECT, "uid-x")
    assert firebase.config().project_id == PROJECT
