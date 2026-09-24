"""How many monitors one person may have running — and who is exempt.

An ordinary account may have :data:`ACTIVE_MONITOR_LIMIT` monitors ACTIVE at
once. Only running ones count: STOPPED and EXPIRED monitors hold no slot and
stay in My Monitors and History as they always have. The owner's account is
exempt, and it is identified by one thing only — the ``admin: true`` custom
claim on its Firebase account record, which ``accounts:lookup`` reports on
sign-in and on every restore, which only an administrator with the service
account can set, and which nothing a person can type, save or send can
imitate.

The guard is in the store (``upsert_monitor``, ``extend_monitor``), so every
path that can make a monitor ACTIVE is refused before a write — the page's
disabled button and its message are the explanation, not the enforcement.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from auth import firebase, session
from config.timezone import now_ist
from monitor import state as state_mod
from monitor.models import ANY_FORMAT, Monitor, MonitorStatus, MovieRef, TheatreTarget
from monitor.state import ACTIVE_MONITOR_LIMIT, MonitorLimitError, Scope
from tests.test_isolation import MemoryFirestore, PROJECT

FRIEND, OWNER = "uid-friend", "uid-owner"


@pytest.fixture(autouse=True)
def no_scope_left_behind():
    """Running ``app.py`` under AppTest registers the app's own scope provider
    process-wide, exactly as a deployment does. Every test here starts and
    ends with none, so nothing about *who is signed in* leaks into the
    next test — the same hygiene ``test_isolation`` and ``test_live_status``
    keep for their fixtures."""
    state_mod.set_scope_provider(None)
    yield
    state_mod.set_scope_provider(None)


def _monitor(title: str, *, owner: str, until: datetime | None = None) -> Monitor:
    return Monitor(
        movie=MovieRef(platform="bookmyshow", event_code="ET1", title=title,
                       region_code="HYD", region_slug="hyderabad", city="Hyderabad"),
        targets=[TheatreTarget("ALLU", "ALLU Cinemas", "Kokapet", ANY_FORMAT)],
        interval_minutes=10, monitor_until=until or now_ist() + timedelta(days=2),
        notify_email="w@example.com", owner_uid=owner)


@pytest.fixture(params=["firestore", "json"])
def account(request, monkeypatch):
    """A signed-in person on either backend. ``as_(uid, admin=...)`` is the
    scope the app registers from the session's AuthUser."""
    if request.param == "firestore":
        store = MemoryFirestore()
        monkeypatch.setattr(state_mod, "_transport", store)
        monkeypatch.setattr(state_mod, "_admin", None)
        monkeypatch.setattr(state_mod, "_owner_of", {})
        monkeypatch.setenv("FIREBASE_PROJECT_ID", PROJECT)
        project = PROJECT
    else:
        monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
        project = ""

    class Account:
        backend = request.param
        uid = FRIEND

        @staticmethod
        def as_(uid: str, *, admin: bool = False) -> None:
            Account.uid = uid
            state_mod.set_scope_provider(
                lambda: Scope(project, uid, lambda: f"id.{uid}", admin=admin))

        @staticmethod
        def create(title: str = "Film") -> Monitor:
            monitor = _monitor(title, owner=Account.uid)
            state_mod.upsert_monitor(monitor, mirror=False)
            return monitor

        @staticmethod
        def running() -> int:
            return state_mod.active_monitor_count(state_mod.load_monitors())

    yield Account
    state_mod.set_scope_provider(None)


# ──────────────────────────────────────────────────────────────────────────
# 1–3: the count
# ──────────────────────────────────────────────────────────────────────────
def test_a_person_with_nothing_running_can_create_one(account):
    account.as_(FRIEND)
    account.create()
    assert account.running() == 1


def test_five_minute_interval_is_admin_only_at_the_store_boundary(account):
    account.as_(FRIEND)
    member_monitor = _monitor("Five minute member", owner=FRIEND)
    member_monitor.interval_minutes = 5
    with pytest.raises(ValueError, match="admin account"):
        state_mod.upsert_monitor(member_monitor, mirror=False)
    assert state_mod.load_monitors() == []

    account.as_(OWNER, admin=True)
    admin_monitor = _monitor("Five minute admin", owner=OWNER)
    admin_monitor.interval_minutes = 5
    state_mod.upsert_monitor(admin_monitor, mirror=False)
    assert state_mod.load_monitors()[0].interval_minutes == 5


def test_the_fifth_is_allowed(account):
    account.as_(FRIEND)
    for i in range(ACTIVE_MONITOR_LIMIT - 1):
        account.create(f"Film {i}")
    assert account.running() == 4
    account.create("The fifth")
    assert account.running() == ACTIVE_MONITOR_LIMIT


def test_the_sixth_is_refused_before_anything_is_written(account):
    account.as_(FRIEND)
    for i in range(ACTIVE_MONITOR_LIMIT):
        account.create(f"Film {i}")
    before = {m.id for m in state_mod.load_monitors()}

    with pytest.raises(MonitorLimitError) as exc:
        account.create("One too many")

    assert exc.value.limit == ACTIVE_MONITOR_LIMIT
    assert str(exc.value) == ("Active monitor limit reached — you can have up to 5 active "
                              "monitors at a time. Stop an existing monitor to start another.")
    assert {m.id for m in state_mod.load_monitors()} == before   # nothing written
    assert account.running() == ACTIVE_MONITOR_LIMIT


# ──────────────────────────────────────────────────────────────────────────
# 4–6: only ACTIVE counts
# ──────────────────────────────────────────────────────────────────────────
def test_stopped_monitors_hold_no_slot(account):
    account.as_(FRIEND)
    for i in range(ACTIVE_MONITOR_LIMIT):
        state_mod.stop_monitor(account.create(f"Old {i}").id, mirror=False)
    assert account.running() == 0
    assert len(state_mod.load_monitors()) == ACTIVE_MONITOR_LIMIT      # still there

    for i in range(ACTIVE_MONITOR_LIMIT):
        account.create(f"New {i}")
    assert account.running() == ACTIVE_MONITOR_LIMIT
    assert len(state_mod.load_monitors()) == 2 * ACTIVE_MONITOR_LIMIT


def test_expired_monitors_hold_no_slot(account):
    account.as_(FRIEND)
    for i in range(ACTIVE_MONITOR_LIMIT):
        # ACTIVE in the store, past its end time: expiry flips it on the
        # next render/tick, and it must not count either way.
        state_mod.upsert_monitor(_monitor(f"Over {i}", owner=FRIEND,
                                          until=now_ist() - timedelta(minutes=1)), mirror=False)
    assert account.running() == 0
    for i in range(ACTIVE_MONITOR_LIMIT):
        account.create(f"New {i}")
    assert account.running() == ACTIVE_MONITOR_LIMIT
    state_mod.expire_due_monitors(mirror=False)
    statuses = {m.status for m in state_mod.load_monitors()}
    assert statuses == {MonitorStatus.ACTIVE, MonitorStatus.EXPIRED}


def test_stopping_one_frees_a_slot(account):
    account.as_(FRIEND)
    made = [account.create(f"Film {i}") for i in range(ACTIVE_MONITOR_LIMIT)]
    with pytest.raises(MonitorLimitError):
        account.create("Blocked")

    state_mod.stop_monitor(made[0].id, mirror=False)

    account.create("Now allowed")
    assert account.running() == ACTIVE_MONITOR_LIMIT


def test_extending_a_finished_monitor_takes_a_slot_like_any_other(account):
    """Extend makes an EXPIRED or STOPPED monitor ACTIVE again — the one other
    way to gain a running monitor, and it is refused the same way."""
    account.as_(FRIEND)
    finished = account.create("Finished")
    state_mod.stop_monitor(finished.id, mirror=False)
    for i in range(ACTIVE_MONITOR_LIMIT):
        account.create(f"Film {i}")

    with pytest.raises(MonitorLimitError):
        state_mod.extend_monitor(finished.id, 24, mirror=False)
    assert state_mod.get_monitor(finished.id).status is MonitorStatus.STOPPED

    # Extending one that is *already* running changes no count and is fine.
    running = next(m for m in state_mod.load_monitors() if m.is_running())
    assert state_mod.extend_monitor(running.id, 24, mirror=False) is not None


def test_re_saving_a_running_monitor_at_the_limit_is_not_refused(account):
    """A retry or a cleared problem rewrites an existing running monitor. It
    already holds its slot; the guard counts the *others*."""
    account.as_(FRIEND)
    made = [account.create(f"Film {i}") for i in range(ACTIVE_MONITOR_LIMIT)]
    made[0].set_problem("RETRY_FAILED", "x")
    state_mod.upsert_monitor(made[0], mirror=False)         # no raise
    assert account.running() == ACTIVE_MONITOR_LIMIT


# ──────────────────────────────────────────────────────────────────────────
# 7: the owner's account
# ──────────────────────────────────────────────────────────────────────────
def test_the_admin_account_has_no_application_limit(account):
    account.as_(OWNER, admin=True)
    for i in range(ACTIVE_MONITOR_LIMIT + 3):
        account.create(f"Film {i}")
    assert account.running() == ACTIVE_MONITOR_LIMIT + 3
    assert state_mod.active_monitor_limit() is None
    assert state_mod.monitor_limit_reached(state_mod.load_monitors()) is False


def test_the_admins_monitors_do_not_count_against_a_friend(account):
    """Isolation, unchanged: each person is counted over their own documents."""
    account.as_(OWNER, admin=True)
    for i in range(ACTIVE_MONITOR_LIMIT + 2):
        account.create(f"Owner {i}")
    account.as_(FRIEND)
    for i in range(ACTIVE_MONITOR_LIMIT):
        account.create(f"Friend {i}")
    assert account.running() == ACTIVE_MONITOR_LIMIT
    with pytest.raises(MonitorLimitError):
        account.create("Friend too many")


def test_the_worker_and_the_command_line_are_not_limited(account, monkeypatch):
    """No signed-in scope → nobody to limit. The worker never creates
    monitors; it only ever flips them to EXPIRED and re-saves."""
    state_mod.set_scope_provider(None)
    if account.backend == "firestore":
        from config import firestore as fs
        monkeypatch.setattr(fs, "service_account_token_getter", lambda info: (lambda: "admin"))
        monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT",
                           '{"client_email": "worker@test", "type": "service_account"}')
    assert state_mod.active_monitor_limit() is None
    for i in range(ACTIVE_MONITOR_LIMIT + 1):
        state_mod.upsert_monitor(_monitor(f"CLI {i}", owner=FRIEND), mirror=False)


# ──────────────────────────────────────────────────────────────────────────
# 8: the role cannot be claimed, typed, saved or sent
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("attributes", [
    None, "", "{}", '{"admin": false}', '{"admin": "true"}', '{"admin": 1}',
    '{"admin": {"x": 1}}', '{"Admin": true}', "not json", '["admin"]', 42,
])
def test_only_the_literal_claim_counts(attributes):
    assert firebase.admin_claim(attributes) is False


def test_the_literal_claim_counts():
    assert firebase.admin_claim('{"admin": true}') is True
    assert firebase.admin_claim('{"admin": true, "other": "x"}') is True
    assert firebase.admin_claim({"admin": True}) is True


def test_the_claim_comes_from_the_account_record_and_nowhere_else(monkeypatch):
    """Sign-in, restore and the verified-return all read it from
    ``accounts:lookup``. The refresh exchange — the only thing the cookie
    can produce — knows nothing about it."""
    from tests.test_auth import FakeFirebase

    fake = FakeFirebase()
    fake.add("owner@example.com", "Popcorn2026", uid=OWNER, name="Owner")
    fake.add("friend@example.com", "Popcorn2026", uid=FRIEND, name="Friend")
    monkeypatch.setattr(firebase, "_post", fake)
    client = firebase.FirebaseAuth("k")

    assert client.sign_in("friend@example.com", "Popcorn2026").admin is False
    assert client.sign_in("owner@example.com", "Popcorn2026").admin is False   # not yet granted

    fake.set_claims("owner@example.com", {"admin": True})                       # the administrator
    owner = client.sign_in("owner@example.com", "Popcorn2026")
    assert owner.admin is True
    assert client.sign_in("friend@example.com", "Popcorn2026").admin is False

    refreshed = client.refresh(owner.refresh_token)
    assert refreshed.admin is False                        # the token exchange cannot say
    assert client.lookup(refreshed.id_token)["admin"] is True                  # the record can
    assert client.account_verified(owner.refresh_token).admin is True

    # …and it is what AuthUser carries, so is_admin() is the record's answer.
    assert session._from_credentials(owner).admin is True
    assert session._from_credentials(refreshed).admin is False


def test_saving_admin_into_your_own_documents_changes_nothing(account):
    """The only documents a person can write are their own settings and
    monitors. Putting ``admin: true`` in either is just data."""
    account.as_(FRIEND)
    state_mod.save_settings({"notify_email": "f@example.com", "admin": True}, mirror=False)
    for i in range(ACTIVE_MONITOR_LIMIT):
        account.create(f"Film {i}")
    forged = _monitor("Forged", owner=FRIEND)
    forged.owner_uid = FRIEND
    with pytest.raises(MonitorLimitError):
        state_mod.upsert_monitor(forged, mirror=False)
    assert state_mod.active_monitor_limit() == ACTIVE_MONITOR_LIMIT


def test_nothing_on_a_page_can_set_the_session_flag():
    """The flag is a field of the AuthUser in server-side session state,
    built only from Firebase credentials. Its default is False."""
    user = session.AuthUser(uid=FRIEND, email="f@example.com")
    assert user.admin is False
    creds = firebase.Credentials(uid=FRIEND, email="f@example.com", display_name="",
                                 id_token="t", refresh_token="r", expires_in=3600,
                                 email_verified=True)
    assert creds.admin is False
    assert session._from_credentials(creds).admin is False


# ──────────────────────────────────────────────────────────────────────────
# The page: the message, the disabled button — and the admin sees neither
# ──────────────────────────────────────────────────────────────────────────
def _five_running_for(uid: str, data_dir) -> None:
    monitors = [_monitor(f"Film {i}", owner=uid).to_dict() for i in range(ACTIVE_MONITOR_LIMIT)]
    (data_dir / "monitors.json").write_text(json.dumps(monitors), encoding="utf-8")


def test_my_monitors_still_lists_everything_at_the_limit(isolated_data, signed_in, seeded_app):
    """The limit is on *starting* monitors. Five active ones are listed as
    always, with Stop and Delete — nothing about what a person sees changes."""
    from tests.test_app import section, text

    _five_running_for(signed_in.uid, isolated_data)
    app = seeded_app(page="My Monitors")
    assert not app.exception
    assert section(text(app), "Active") == "5"
    assert sum(1 for b in app.button if b.key.startswith("m_stop_")) == ACTIVE_MONITOR_LIMIT


def test_start_button_is_disabled_at_the_limit_and_enabled_for_admin(isolated_data, signed_in, seeded_app):
    _five_running_for(signed_in.uid, isolated_data)
    wizard = dict(step=5, location="hyderabad", movie_id=seeded_app.movie_id,
                  theatres=["ALLU"], formats={"ALLU": [ANY_FORMAT]})

    app = seeded_app(**wizard)
    assert not app.exception
    assert next(b for b in app.button if b.key == "start").disabled is True
    assert any("Active monitor limit reached — you can have up to 5 active monitors "
               "at a time. Stop an existing monitor to start another." in w.value
               for w in app.warning)

    signed_in.admin = True                                  # what the record would say
    app = seeded_app(**wizard)
    assert not app.exception
    assert next(b for b in app.button if b.key == "start").disabled is False
    assert not any("Active monitor limit" in w.value for w in app.warning)


def test_below_the_limit_the_page_says_nothing(isolated_data, signed_in, seeded_app):
    app = seeded_app(step=5, location="hyderabad", movie_id=seeded_app.movie_id,
                     theatres=["ALLU"], formats={"ALLU": [ANY_FORMAT]})
    assert next(b for b in app.button if b.key == "start").disabled is False
    assert not any("Active monitor limit" in w.value for w in app.warning)


@pytest.fixture
def seeded_app(provider_factory, monkeypatch):
    """test_app's ``seeded`` catalogue plus its ``run`` helper, in one."""
    pytest.importorskip("streamlit.testing.v1")
    from monitor import catalogue
    from tests.conftest import ALLU_LIVE, QUICKBOOK_HYD, build_payload, hyd_detail
    from tests.test_app import run

    provider = provider_factory([QUICKBOOK_HYD] + hyd_detail(build_payload(ALLU_LIVE)))
    monkeypatch.setattr(catalogue, "get_provider", lambda slug: provider)
    catalogue.sync_region("hyderabad", mirror=False, detail=True)
    entry = next(e for e in catalogue.list_entries("hyderabad")
                 if catalogue.movie_from_entry(e).title == "Mandaadi")
    run.movie_id = catalogue.movie_from_entry(entry).id
    return run


# ──────────────────────────────────────────────────────────────────────────
# The administrator's tool: safe without credentials, and honest about claims
# ──────────────────────────────────────────────────────────────────────────
def test_grant_admin_refuses_to_run_without_the_service_account(monkeypatch, capsys):
    from tools import grant_admin

    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT", raising=False)
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    assert grant_admin.main(["--email", "x@example.com", "--grant"]) == 2
    assert "FIREBASE_SERVICE_ACCOUNT" in capsys.readouterr().err


def test_grant_admin_reads_claims_the_way_the_app_does():
    from tools import grant_admin

    assert grant_admin.claims_of({"customAttributes": '{"admin": true}'}) == {"admin": True}
    assert grant_admin.claims_of({}) == {}
    assert grant_admin.claims_of({"customAttributes": "junk"}) == {}
    assert firebase.admin_claim(json.dumps(grant_admin.claims_of(
        {"customAttributes": '{"admin": true}'}))) is True
