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
from tests.conftest import APP_SCRIPT

PROJECT = "ticketradar-test"
ROOT = f"{fs.BASE}/projects/{PROJECT}/databases/(default)/documents"


# ──────────────────────────────────────────────────────────────────────────
# The fake service
# ──────────────────────────────────────────────────────────────────────────
class Invalid(Exception):
    """What Firestore answers when a document's shape is not storable."""


def reject_nested_arrays(fields: dict, path: str = "") -> None:
    """Firestore will not store an array directly inside an array, at any
    depth. The service refuses the whole write with INVALID_ARGUMENT, so the
    fake does too — otherwise a shape the real service rejects would sail
    through the suite, which is exactly how this went unnoticed."""

    def walk(value, where: str, in_array: bool) -> None:
        if isinstance(value, dict):
            if "arrayValue" in value:
                if in_array:
                    raise Invalid(f"array inside array at {where}")
                for i, item in enumerate(value["arrayValue"].get("values") or []):
                    walk(item, f"{where}[{i}]", True)
                return
            if "mapValue" in value:
                for k, v in (value["mapValue"].get("fields") or {}).items():
                    walk(v, f"{where}.{k}", False)
                return

    for key, value in (fields or {}).items():
        walk(value, f"{path}{key}", False)


class Denied(Exception):
    pass


class MemoryFirestore:
    """``transport(method, url, token, body) -> (status, payload)``."""

    RULES_WRITE_DENIED = {"monitor_state"}          # users never create/update these
    RULES_UPDATE_DENIED = {"history"}               # append-only: never rewritten
    #: Collections whose read and delete rules are ``ownsExisting()`` — they
    #: dereference ``resource.data``, so the operation is denied outright when
    #: the document does not exist. ``users`` is not one of them: its rule
    #: compares the *path* to the UID and never touches ``resource``, so a
    #: person reading their own settings before they have any gets NOT_FOUND.
    RULES_NEED_EXISTING = {"monitors", "monitor_state", "history"}
    # Nothing is delete-denied outright: every collection gates delete on the
    # existing document's owner, so an account can take its own records with
    # it. ``history`` is deletable but still not *updatable*.

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
        except Invalid as exc:
            return 400, {"error": {"message": f"INVALID_ARGUMENT: {exc}"}}
        raise AssertionError(f"unexpected {method} {path}")

    def _get(self, who, path):
        collection, doc = path.strip("/").split("/")
        fields = self.docs.get(collection, {}).get(doc)
        if fields is None:
            if who is not None and collection in self.RULES_NEED_EXISTING:
                # The real service, on the real rules. ``ownsExisting()`` reads
                # ``resource.data.owner_uid``, and on a document that is not
                # there ``resource`` is null: the expression errors, the rule
                # denies, and Firestore answers PERMISSION_DENIED — *not*
                # NOT_FOUND. "Never written" and "not yours" are the same
                # answer, which is what the app has to be written against.
                raise Denied("no such document (rules read resource.data)")
            return 404, {"error": {"message": "NOT_FOUND"}}
        if collection == "users" and who is not None and doc != who:
            raise Denied("not your user document")
        if not self._may_read(who, collection, fields):
            raise Denied("not the owner")
        return 200, {"name": f"{ROOT}/{collection}/{doc}",
                     "fields": {k: fs.encode(v, field=k) for k, v in fields.items()}}

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
                                          "fields": {k: fs.encode(v, field=k) for k, v in fields.items()}}})
        limit = q.get("limit")
        return 200, rows[:limit] if limit else rows

    def _commit(self, who, body):
        staged = []
        for write in body["writes"]:
            if "delete" in write:
                collection, doc = write["delete"].split("/")[-2:]
                existing = self.docs.get(collection, {}).get(doc)
                if who is not None:
                    if collection == "users":
                        if doc != who:
                            raise Denied("not your user document")
                    elif existing is None:
                        if collection in self.RULES_NEED_EXISTING:
                            # Same null ``resource`` as a read: deleting a
                            # document that was never written is refused, and
                            # a commit is atomic, so it takes the rest with it.
                            raise Denied("delete of a document that does not exist")
                    elif existing.get(fs.OWNER) != who:
                        raise Denied("delete of a document you don't own")
                staged.append((collection, doc, None))
            else:
                collection, doc = write["update"]["name"].split("/")[-2:]
                reject_nested_arrays(write["update"]["fields"])
                fields = {k: fs.decode(v, field=k) for k, v in write["update"]["fields"].items()}
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
    # The app uses Firestore only when a deployment explicitly opts in after
    # creating the database and publishing its rules.
    monkeypatch.setenv("TICKETRADAR_FIRESTORE_ENABLED", "true")

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
def test_json_compatibility_store_is_private_per_signed_in_account(tmp_path, monkeypatch, make_monitor):
    """Before Firestore is enabled, the JSON compatibility path is UID-scoped."""
    monkeypatch.setattr(state_mod, "MONITORS_FILE", tmp_path / "monitors.json")
    monkeypatch.setattr(state_mod, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(state_mod, "SETTINGS_FILE", tmp_path / "settings.json")
    history: list[dict] = [{"message": "old shared event"}]
    monkeypatch.setattr(state_mod, "_json_load_history", lambda: list(history))
    monkeypatch.setattr(state_mod, "_json_save_history", lambda value, **_: history.__setitem__(slice(None), value))
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT", raising=False)

    state_mod.set_scope_provider(lambda: Scope("", "uid-a", lambda: "", firestore_enabled=False))
    a = make_monitor(email="a@example.com", owner_uid="")
    state_mod.upsert_monitor(a, mirror=False)
    state_mod.record_history(a, "CREATED", "A's monitor", mirror=False)
    state_mod.save_settings({"notify_email": "a@example.com", "default_interval": 15}, mirror=False)
    state_mod.save_state({a.id: MonitorState(check_count=2)}, mirror=False)

    state_mod.set_scope_provider(lambda: Scope("", "uid-b", lambda: "", firestore_enabled=False))
    assert state_mod.load_monitors() == []
    assert state_mod.load_history() == []
    assert state_mod.load_state() == {}
    assert state_mod.load_settings() == {"notify_email": "", "default_interval": 10}
    state_mod.set_scope_provider(None)


def test_json_compatibility_delete_removes_only_the_owners_state(tmp_path, monkeypatch, make_monitor):
    monkeypatch.setattr(state_mod, "MONITORS_FILE", tmp_path / "monitors.json")
    monkeypatch.setattr(state_mod, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT", raising=False)

    state_mod.set_scope_provider(lambda: Scope("", "uid-a", lambda: "", firestore_enabled=False))
    a = make_monitor(email="a@example.com", owner_uid="")
    state_mod.upsert_monitor(a, mirror=False)
    state_mod.save_state({a.id: MonitorState(check_count=2)}, mirror=False)

    state_mod.set_scope_provider(lambda: Scope("", "uid-b", lambda: "", firestore_enabled=False))
    b = make_monitor(email="b@example.com", owner_uid="")
    state_mod.upsert_monitor(b, mirror=False)
    state_mod.save_state({b.id: MonitorState(check_count=3)}, mirror=False)

    state_mod.set_scope_provider(lambda: Scope("", "uid-a", lambda: "", firestore_enabled=False))
    state_mod.delete_monitor(a.id, mirror=False)
    assert state_mod.load_monitors() == []
    assert state_mod.load_state() == {}

    state_mod.set_scope_provider(lambda: Scope("", "uid-b", lambda: "", firestore_enabled=False))
    assert [monitor.id for monitor in state_mod.load_monitors()] == [b.id]
    assert state_mod.load_state()[b.id].check_count == 3
    state_mod.set_scope_provider(None)


def test_creating_a_monitor_stores_the_owner_uid(cloud, make_monitor):
    cloud.as_user("uid-a")
    m = make_monitor(email="a@example.com", owner_uid="")                          # no owner set by the caller…
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


def test_a_document_that_does_not_exist_is_denied_not_missing(cloud):
    """Why the fake answers 403 to a read of nothing, and why that matters.

    ``ownsExisting()`` is ``resource.data.owner_uid == request.auth.uid``. On
    a document that was never written ``resource`` is null, the dereference
    errors, and an erroring rule denies: the service answers PERMISSION_DENIED
    where a bare ``allow read: if true`` would have answered NOT_FOUND. Every
    collection gated on ``ownsExisting()`` behaves this way, and a caller
    reading one document by id cannot tell "not yours" from "not there".

    ``users/{uid}`` is the exception that proves it is the rule and not the
    service: its rule compares the path to the UID and never touches
    ``resource``, so reading your own settings before you have any is an
    ordinary 404.
    """
    rules = Path("firestore.rules").read_text(encoding="utf-8")
    for collection in MemoryFirestore.RULES_NEED_EXISTING:
        assert re.search(rf"match /{collection}/\{{id\}} \{{[^}}]*allow read[^;]*ownsExisting\(\)",
                         rules, re.S), collection

    mine = fs.FirestoreClient(PROJECT, lambda: "id.uid-a", transport=state_mod._transport)
    for collection in sorted(MemoryFirestore.RULES_NEED_EXISTING):
        with pytest.raises(fs.FirestoreError, match="PERMISSION_DENIED"):
            mine.get(collection, "never-written")
        # The client may say it knows that, and then both answers are None.
        assert mine.get(collection, "never-written", absent_if_denied=True) is None

    # users/{uid}: no resource in the rule, so a plain not-found.
    assert mine.get("users", "uid-a") is None


def test_the_rules_file_says_what_the_fake_enforces():
    rules = Path("firestore.rules").read_text(encoding="utf-8")
    assert "request.auth != null" in rules
    assert "resource.data.owner_uid == request.auth.uid" in rules
    assert "request.resource.data.owner_uid == request.auth.uid" in rules
    assert re.search(r"match /users/\{uid\} \{\s*allow read, write: if signedIn\(\) && request.auth.uid == uid;", rules)
    assert re.search(r"match /monitor_state/\{id\} \{[^}]*allow create, update: if false;", rules, re.S)
    # history: readable and creatable by its owner, deletable by its owner so
    # account deletion can take it, and never updatable.
    assert re.search(r"match /history/\{id\} \{[^}]*allow delete: if ownsExisting\(\);", rules, re.S)
    assert re.search(r"match /history/\{id\} \{[^}]*allow update: if false;", rules, re.S)
    assert not re.search(r"match /history/\{id\} \{[^}]*allow update, delete: if false;", rules, re.S)
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

    app = AppTest.from_file(APP_SCRIPT, default_timeout=60).run()      # registers the app's own scope
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


# ──────────────────────────────────────────────────────────────────────────
# Deleting an account, against Firestore and its rules
#
# The rules used to close `history` to deletion outright, so `purge_user_data`
# — which deletes the owner's history as part of Delete Account — was refused,
# and because a commit is atomic *nothing* was removed. These pin the fix: the
# owner may delete their own history and nobody else's.
# ──────────────────────────────────────────────────────────────────────────
def _seed(cloud, make_monitor, uid: str, email: str):
    """One monitor, its state, a history entry and a settings document."""
    cloud.as_user(uid)
    monitor = monitor_for(make_monitor, uid, email)
    state_mod.upsert_monitor(monitor, mirror=False)
    state_mod.record_history(monitor, "CREATED", f"{uid} monitor created.", mirror=False)
    state_mod.save_settings({"notify_email": email, "default_interval": 15}, mirror=False)
    # State belongs to the worker, as the rules require.
    cloud.as_worker()
    state_mod.save_state({monitor.id: MonitorState(check_count=3)}, mirror=False)
    cloud.as_user(uid)
    return monitor


def test_delete_account_removes_the_owners_monitors_history_and_user_document(cloud, make_monitor):
    """The whole purge, atomically, against the rules."""
    a = _seed(cloud, make_monitor, "uid-a", "a@example.com")

    cloud.as_user("uid-a")
    removed = state_mod.purge_user_data(mirror=False)
    assert removed["monitors"] == 1 and removed["history"] == 1 and removed["settings"] == 1

    # Nothing of A's is left in any collection.
    assert a.id not in cloud.docs.get("monitors", {})
    assert a.id not in cloud.docs.get("monitor_state", {})
    assert [d for d in cloud.docs.get("history", {}).values() if d.get(fs.OWNER) == "uid-a"] == []
    assert "uid-a" not in cloud.docs.get("users", {})

    # …and the account's own view agrees.
    assert state_mod.load_monitors() == []
    assert state_mod.load_state() == {}
    assert state_mod.load_history() == []


def test_an_owner_may_delete_their_own_history_document(cloud, make_monitor):
    """The single permission the fix adds, on its own."""
    _seed(cloud, make_monitor, "uid-a", "a@example.com")
    cloud.as_user("uid-a")
    [(doc_id, doc)] = list(cloud.docs["history"].items())
    assert doc[fs.OWNER] == "uid-a"

    client = fs.FirestoreClient(PROJECT, lambda: "id.uid-a", transport=state_mod._transport)
    client.delete("history", doc_id)                       # allowed: it is theirs
    assert doc_id not in cloud.docs["history"]


def test_an_owner_may_not_delete_another_accounts_history(cloud, make_monitor):
    """B holds a valid token and still cannot remove A's record."""
    _seed(cloud, make_monitor, "uid-a", "a@example.com")
    [(a_doc, _)] = list(cloud.docs["history"].items())

    client = fs.FirestoreClient(PROJECT, lambda: "id.uid-b", transport=state_mod._transport)
    with pytest.raises(fs.FirestoreError) as caught:
        client.delete("history", a_doc)
    assert caught.value.status == 403
    assert a_doc in cloud.docs["history"]                  # untouched


def test_an_unauthenticated_caller_may_not_delete_a_history_document(cloud, make_monitor):
    """No token at all: refused before ownership is even considered."""
    _seed(cloud, make_monitor, "uid-a", "a@example.com")
    [(a_doc, _)] = list(cloud.docs["history"].items())

    client = fs.FirestoreClient(PROJECT, lambda: "", transport=state_mod._transport)
    with pytest.raises(fs.FirestoreError):
        client.delete("history", a_doc)
    assert a_doc in cloud.docs["history"]


def test_history_may_still_never_be_rewritten(cloud, make_monitor):
    """Delete is now allowed; update is still not, so a record cannot be
    edited after the fact — only removed with the account."""
    _seed(cloud, make_monitor, "uid-a", "a@example.com")
    [(a_doc, fields)] = list(cloud.docs["history"].items())

    client = fs.FirestoreClient(PROJECT, lambda: "id.uid-a", transport=state_mod._transport)
    with pytest.raises(fs.FirestoreError):
        client.set("history", a_doc, {**fields, "message": "rewritten"})
    assert cloud.docs["history"][a_doc]["message"] != "rewritten"


def test_deleting_one_account_leaves_the_other_account_whole(cloud, make_monitor):
    """A's deletion must not reach B's monitors, state, history or settings."""
    a = _seed(cloud, make_monitor, "uid-a", "a@example.com")
    b = _seed(cloud, make_monitor, "uid-b", "b@example.com")

    cloud.as_user("uid-a")
    state_mod.purge_user_data(mirror=False)

    cloud.as_user("uid-b")
    assert [m.id for m in state_mod.load_monitors()] == [b.id]
    assert state_mod.load_state()[b.id].check_count == 3
    assert [h["message"] for h in state_mod.load_history()] == ["uid-b monitor created."]
    assert state_mod.load_settings()["notify_email"] == "b@example.com"
    assert a.id != b.id


def test_a_refused_purge_leaves_every_account_untouched(cloud, make_monitor):
    """Atomicity: if any write in the purge is refused, nothing is applied —
    which is exactly what used to happen to the whole Delete Account."""
    _seed(cloud, make_monitor, "uid-a", "a@example.com")
    b = _seed(cloud, make_monitor, "uid-b", "b@example.com")
    before = {c: dict(d) for c, d in cloud.docs.items()}

    # A commit that mixes A's own document with one of B's: the rules refuse
    # the second, and the first must not be applied either.
    [a_doc] = [k for k, v in cloud.docs["history"].items() if v[fs.OWNER] == "uid-a"]
    [b_doc] = [k for k, v in cloud.docs["history"].items() if v[fs.OWNER] == "uid-b"]
    client = fs.FirestoreClient(PROJECT, lambda: "id.uid-a", transport=state_mod._transport)
    with pytest.raises(fs.FirestoreError):
        client.commit([("history", a_doc, None), ("history", b_doc, None)])

    assert cloud.docs["history"] == before["history"]      # nothing removed at all
    assert b.id in cloud.docs["monitors"]


# ──────────────────────────────────────────────────────────────────────────
# Firestore forbids an array inside an array
#
# `movie.variants` is [[code, label], …] and `targets.*.time_links` is
# [[label, url], …]. Written as they stand, Firestore answers INVALID_ARGUMENT
# and the whole commit fails — so on the wire each pair becomes a small map,
# and comes back a pair. The application and the JSON store never see the
# difference; only Firestore's spelling changes.
# ──────────────────────────────────────────────────────────────────────────
VARIANTS = [["ET00517001", "Barco Laser 4K Atmos"], ["ET00517002", "Dolby Cinema"]]
TIME_LINKS = [["03:40 PM", "https://in.bookmyshow.com/a"],
              ["11:00 PM", "https://in.bookmyshow.com/b"]]


def _arrays_inside_arrays(value, where="") -> list[str]:
    """Every place an encoded document puts an array directly inside one."""
    found: list[str] = []

    def walk(node, path, in_array):
        if not isinstance(node, dict):
            return
        if "arrayValue" in node:
            if in_array:
                found.append(path)
            for i, item in enumerate(node["arrayValue"].get("values") or []):
                walk(item, f"{path}[{i}]", True)
        elif "mapValue" in node:
            for k, v in (node["mapValue"].get("fields") or {}).items():
                walk(v, f"{path}.{k}", False)

    for k, v in (value or {}).items():
        walk(v, f"{where}{k}", False)
    return found


def test_a_pair_field_is_written_as_maps_not_as_a_nested_array():
    encoded = fs.encode(VARIANTS, field="variants")
    values = encoded["arrayValue"]["values"]
    assert all("mapValue" in v for v in values)                  # maps, not arrays
    assert [set(v["mapValue"]["fields"]) for v in values] == [{"code", "label"}] * 2
    assert fs.decode(encoded, field="variants") == VARIANTS      # and back again

    encoded = fs.encode(TIME_LINKS, field="time_links")
    values = encoded["arrayValue"]["values"]
    assert all("mapValue" in v for v in values)
    assert [set(v["mapValue"]["fields"]) for v in values] == [{"label", "url"}] * 2
    assert fs.decode(encoded, field="time_links") == TIME_LINKS


def test_an_ordinary_array_is_untouched():
    """Only the two pair fields change shape; every other list is as it was."""
    for field in ("", "time_labels", "date_codes", "showtime_keys", "targets"):
        encoded = fs.encode(["a", "b"], field=field)
        assert [v["stringValue"] for v in encoded["arrayValue"]["values"]] == ["a", "b"]
        assert fs.decode(encoded, field=field) == ["a", "b"]
    # …and a pair field that is empty, or not actually pairs, is left alone.
    assert fs.decode(fs.encode([], field="variants"), field="variants") == []
    assert fs.decode(fs.encode(["solo"], field="variants"), field="variants") == ["solo"]


def test_a_document_stored_before_this_encoding_still_reads_back():
    """Backward compatible: a pair field written the old way still decodes."""
    legacy = {"arrayValue": {"values": [fs.encode(["ET1", "IMAX"])]}}
    assert fs.decode(legacy, field="variants") == [["ET1", "IMAX"]]


def test_a_monitor_with_variants_round_trips_through_firestore(cloud, make_monitor):
    """Python → encode → fake Firestore → decode → Python, unchanged."""
    from monitor.models import MovieRef

    cloud.as_user("uid-a")
    monitor = monitor_for(make_monitor, "uid-a", "a@example.com")
    monitor.movie = replace(monitor.movie, variants=tuple(tuple(p) for p in VARIANTS))
    state_mod.upsert_monitor(monitor, mirror=False)

    # Nothing stored is an array inside an array.
    stored = cloud.docs["monitors"][monitor.id]
    assert _arrays_inside_arrays({k: fs.encode(v, field=k) for k, v in stored.items()}) == []

    [back] = state_mod.load_monitors()
    assert back.movie.variants == tuple(tuple(p) for p in VARIANTS)
    assert [list(p) for p in back.movie.variants] == VARIANTS     # the app's own shape
    assert back.movie.variant_codes == ("ET00517001", "ET00517002")
    assert isinstance(back.movie, MovieRef)


def test_state_with_time_links_round_trips_through_firestore(cloud, make_monitor):
    cloud.as_user("uid-a")
    monitor = monitor_for(make_monitor, "uid-a", "a@example.com")
    state_mod.upsert_monitor(monitor, mirror=False)

    ms = MonitorState(check_count=2)
    target = ms.target("ALLU::Dolby Cinema")
    target.time_links = [list(p) for p in TIME_LINKS]
    target.time_labels = ["03:40 PM", "11:00 PM"]
    cloud.as_worker()                                   # only the worker writes state
    state_mod.save_state({monitor.id: ms}, mirror=False)

    stored = cloud.docs["monitor_state"][monitor.id]
    assert _arrays_inside_arrays({k: fs.encode(v, field=k) for k, v in stored.items()}) == []

    cloud.as_user("uid-a")
    back = state_mod.load_state()[monitor.id].targets["ALLU::Dolby Cinema"]
    assert back.time_links == TIME_LINKS                # exactly the app's shape
    assert back.time_labels == ["03:40 PM", "11:00 PM"]


def test_the_fake_refuses_a_nested_array_anywhere_in_a_document(cloud):
    """The guard itself: a shape the real service rejects must not pass here.

    Built by hand, bypassing ``encode``'s pair handling, so it is the raw
    nested array Firestore refuses.
    """
    client = fs.FirestoreClient(PROJECT, lambda: "id.uid-a", transport=state_mod._transport)
    nested = {"arrayValue": {"values": [fs.encode(["x", "y"])]}}      # array in array

    at_top = {"owner_uid": fs.encode("uid-a"), "bad": nested}
    buried = {"owner_uid": fs.encode("uid-a"),
              "movie": {"mapValue": {"fields": {"deep": {"mapValue": {"fields": {"bad": nested}}}}}}}

    for raw in (at_top, buried):
        with pytest.raises(fs.FirestoreError) as caught:
            client._call("POST", ":commit", {"writes": [
                {"update": {"name": f"{client.root}/monitors/x", "fields": raw}}]})
        assert caught.value.status == 400
        assert "INVALID_ARGUMENT" in str(caught.value)
    assert "x" not in cloud.docs.get("monitors", {})                 # nothing applied


def test_the_json_store_representation_is_unchanged(make_monitor, tmp_path, monkeypatch):
    """The JSON files keep the list-of-pairs spelling they always had — the
    map form exists only inside Firestore."""
    import json

    monkeypatch.setattr(state_mod, "MONITORS_FILE", tmp_path / "monitors.json")
    state_mod.set_scope_provider(None)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT", raising=False)

    monitor = make_monitor(owner_uid="uid-a")
    monitor.movie = replace(monitor.movie, variants=tuple(tuple(p) for p in VARIANTS))
    state_mod.save_monitors([monitor], mirror=False)

    raw = json.loads((tmp_path / "monitors.json").read_text(encoding="utf-8"))
    assert raw[0]["movie"]["variants"] == VARIANTS      # lists of pairs, as before
    assert all(isinstance(p, list) for p in raw[0]["movie"]["variants"])
