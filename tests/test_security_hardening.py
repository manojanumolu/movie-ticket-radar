"""Regression tests for the security hardening.

Each section is one finding of the security audit, tested at every layer
that now enforces it — the page, the store, the worker and (as text; the
emulator runs the real thing in ``tests/firestore_rules``) the rules:

  H1  alert and test-email recipients are the account's own verified address
  H2  the worker never trusts a stored monitor (owner, recipient, interval,
      end date, admin-only features, limits, unknown fields)
  M1  starting a workflow is authorised on the server
  M3  the admin-grant workflow never prints the account
  L1  monitors end within MAX_MONITOR_DAYS
  L4  a signed-in person's data never goes to the public repository's JSON

No test sends an email (``no_smtp`` in conftest makes a real SMTP connection
an error) or reaches GitHub or Firebase.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

from config import firestore as fs
from config import store
from config.timezone import IST, now_ist
from monitor import dispatch, policy
from monitor import state as state_mod
from monitor.models import ANY_FORMAT, MonitorStatus, TheatreTarget
from monitor.state import Scope
from notifications import email as mail
from tests.test_app import run, seeded  # noqa: F401 - fixture
from tests.test_isolation import PROJECT, cloud, monitor_for  # noqa: F401 - fixture

ROOT = Path(__file__).resolve().parent.parent


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────
class Directory:
    """A stand-in for Firebase Authentication's account records."""

    def __init__(self, **accounts: policy.OwnerIdentity):
        self.accounts = dict(accounts)
        self.asked: list[list[str]] = []

    def __call__(self, uids):
        self.asked.append(sorted(uids))
        return {u: self.accounts[u] for u in uids if u in self.accounts}


def verified(email: str, *, admin: bool = False) -> policy.OwnerIdentity:
    return policy.OwnerIdentity(email=email, email_verified=True, admin=admin)


def plant(cloud, monitor, **overrides) -> None:
    """Write a monitor document straight into the store — as someone calling
    Firestore's API directly would, with no app in between."""
    cloud.docs.setdefault("monitors", {})[monitor.id] = {**monitor.to_dict(), **overrides}


def run_worker(cloud, monkeypatch, at, payload_rows, *, directory=None):
    from monitor import checker
    from tests.conftest import build_payload

    cloud.as_worker()
    if directory is not None:
        policy.set_identity_resolver(directory)
    sent: list[tuple[str, str, str]] = []

    def notifier(monitor, change, *args, **kwargs):
        sent.append((monitor.id, monitor.notify_email, monitor.owner_uid))

    monkeypatch.setattr(checker, "get_provider",
                        lambda slug: _Provider([build_payload(payload_rows)] * 50))
    report = checker.run_once(at=at, notifier=notifier, force=True)
    return report, sent


class _Provider:
    """Hands out the same listing for every read."""

    def __init__(self, payloads):
        from tests.conftest import FakeResponse, FakeSession
        from platforms.bookmyshow import BookMyShowProvider

        self._inner = BookMyShowProvider(session=FakeSession([FakeResponse(200, p) for p in payloads]),
                                         sleeper=lambda s: None)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def live_target():
    return [TheatreTarget("ALLU", "Allu Cinemas", "Attapur, Hyderabad", ANY_FORMAT)]


# ══════════════════════════════════════════════════════════════════════════
# H1 · Recipients
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("good", ["a@example.com", "  first.last+tag@sub.example.co.in  "])
def test_a_single_address_is_accepted(good):
    assert policy.normalise_recipient(good) == good.strip()


@pytest.mark.parametrize("bad", [
    "", "   ", None, 42,
    "a@example.com, b@example.com",        # a list
    "a@example.com;b@example.com",
    "a@example.com b@example.com",
    "Alice <a@example.com>",               # a display name
    "a@example.com\nBcc: c@example.com",   # a header break
    "a@exa\r\nmple.com",
    "\"a,b\"@example.com",
    "no-at-sign.example.com",
    "a@localhost",
    "a@" + "x" * 250 + ".com",             # too long
])
def test_anything_but_one_address_is_refused(bad):
    with pytest.raises(policy.InvalidRecipient):
        policy.normalise_recipient(bad)


def test_the_transport_itself_refuses_several_recipients():
    """The last step before Gmail: whoever calls it, a list never reaches
    the To: line — and no SMTP connection is opened (conftest's ``no_smtp``
    would fail the test if one were)."""
    for to in ("a@example.com, victim@example.org", "Alice <a@example.com>", "a@example.com\nBcc: x@y.z"):
        with pytest.raises(mail.NotificationError):
            mail.send_email(to, "s", "<p>h</p>", "t")


@pytest.fixture
def outbox(monkeypatch):
    """Capture test emails instead of sending them, with a fresh limiter on
    a clock the test drives."""
    sent: list[str] = []
    clock = {"now": 1000.0}
    monkeypatch.setattr(mail, "send_test_email", lambda to: sent.append(to))
    monkeypatch.setattr(mail, "_TEST_LIMITER", mail.TestEmailLimiter(clock=lambda: clock["now"]))
    return sent, clock


def test_the_test_email_goes_to_the_account_and_nowhere_else(outbox):
    sent, _ = outbox
    assert mail.send_account_test_email("uid-a", "a@example.com") == "a@example.com"
    assert sent == ["a@example.com"]
    with pytest.raises(mail.NotificationError):
        mail.send_account_test_email("uid-b", "b@example.com, victim@example.org")
    assert sent == ["a@example.com"]


def test_the_test_email_is_rate_limited_on_the_server(outbox):
    sent, clock = outbox
    mail.send_account_test_email("uid-a", "a@example.com")
    with pytest.raises(mail.NotificationError, match="Try again in"):
        mail.send_account_test_email("uid-a", "a@example.com")          # straight away
    for _ in range(2):
        clock["now"] += mail.TestEmailLimiter.GAP + 1
        mail.send_account_test_email("uid-a", "a@example.com")
    clock["now"] += mail.TestEmailLimiter.GAP + 1
    with pytest.raises(mail.NotificationError, match="this hour"):
        mail.send_account_test_email("uid-a", "a@example.com")          # 4th in the hour
    assert len(sent) == 3
    clock["now"] += mail.TestEmailLimiter.WINDOW
    mail.send_account_test_email("uid-a", "a@example.com")              # the hour has passed
    assert len(sent) == 4


def test_the_test_email_limit_holds_across_accounts_for_the_whole_server(outbox):
    sent, clock = outbox
    for i in range(mail.TestEmailLimiter.GLOBAL_PER_HOUR):
        mail.send_account_test_email(f"uid-{i}", f"u{i}@example.com")
    with pytest.raises(mail.NotificationError, match="paused"):
        mail.send_account_test_email("uid-new", "new@example.com")
    assert len(sent) == mail.TestEmailLimiter.GLOBAL_PER_HOUR
    with pytest.raises(mail.NotificationError):
        mail.send_account_test_email("", "anon@example.com")               # nobody signed in


def test_settings_test_email_uses_the_signed_in_account_across_sessions(outbox, signed_in):
    """The page offers no recipient box to edit; the button sends to the
    session's account. A second browser session is limited too — the limit
    lives in the server process, not in the session."""
    sent, _ = outbox
    first = run("Settings")
    box = first.text_input(key="settings_email")
    assert box.disabled and box.value == signed_in.email
    assert "save_settings" not in {b.key for b in first.button}
    first.session_state["settings_email"] = "victim@example.org"        # a forged widget value
    first.button(key="test_email").click().run()
    assert sent == [signed_in.email]

    second = run("Settings")                                             # a new session, same account
    second.button(key="test_email").click().run()
    assert sent == [signed_in.email]
    assert any("Try again in" in e.value for e in second.error)


def test_every_running_monitor_a_person_saves_is_addressed_to_them(make_monitor):
    """The store binds the recipient, whatever the monitor object carried —
    including an older running monitor saved alongside (the whole list is
    written on every save). A stopped monitor is left as it was."""
    state_mod.set_scope_provider(lambda: Scope("", "uid-a", lambda: "", email="a@example.com"))
    try:
        legacy = make_monitor(email="old@example.org", owner_uid="uid-a")
        stopped = make_monitor(email="kept@example.org", owner_uid="uid-a")
        stopped.status = MonitorStatus.STOPPED
        state_mod.save_monitors([legacy, stopped], mirror=False)
        new = make_monitor(email="victim@example.org, x@example.org", owner_uid="uid-a")
        state_mod.upsert_monitor(new, mirror=False)
        stored = {m.id: m for m in state_mod.load_monitors()}
    finally:
        state_mod.set_scope_provider(None)
    assert stored[new.id].notify_email == "a@example.com"
    assert stored[legacy.id].notify_email == "a@example.com"
    assert stored[stopped.id].notify_email == "kept@example.org"


def test_starting_a_monitor_ignores_a_forged_recipient(seeded, signed_in):
    app = run(step=5, location="hyderabad", movie_id=seeded, theatres=["ALLU"],
              formats={"ALLU": [ANY_FORMAT]})
    app.session_state["notify_email"] = "victim@example.org, other@example.org"
    app.button(key="start").click().run()
    assert not app.exception, [str(e) for e in app.exception]
    [monitor] = state_mod.load_monitors()
    assert monitor.notify_email == signed_in.email


# ══════════════════════════════════════════════════════════════════════════
# H2 · The worker never trusts a stored monitor
# ══════════════════════════════════════════════════════════════════════════
def test_the_worker_emails_the_owners_verified_address_not_the_documents(cloud, make_monitor, monkeypatch, at):
    from tests.conftest import ALLU_LIVE

    a = monitor_for(make_monitor, "uid-a", "a@example.com")
    a.targets = live_target()
    plant(cloud, a, notify_email="victim@example.org")                   # written around the app
    b = monitor_for(make_monitor, "uid-b", "b@example.com")
    b.targets = live_target()
    plant(cloud, b)
    directory = Directory(**{"uid-a": verified("a@example.com"), "uid-b": verified("b@example.com")})
    report, sent = run_worker(cloud, monkeypatch, at, ALLU_LIVE, directory=directory)
    assert sorted(sent) == sorted([(a.id, "a@example.com", "uid-a"), (b.id, "b@example.com", "uid-b")])
    assert not report.rejected
    assert directory.asked == [["uid-a", "uid-b"]]                       # one batched lookup


@pytest.mark.parametrize("identity, why", [
    (None, "no owner account"),
    (policy.OwnerIdentity(email="a@example.com", email_verified=False), "not verified"),
    (policy.OwnerIdentity(email="a@example.com", email_verified=True, disabled=True), "disabled"),
    (policy.OwnerIdentity(email="", email_verified=True), "not verified"),
])
def test_a_monitor_whose_owner_is_not_a_verified_account_is_never_checked(
        cloud, make_monitor, monkeypatch, at, identity, why):
    from tests.conftest import ALLU_LIVE

    m = monitor_for(make_monitor, "uid-a", "a@example.com")
    m.targets = live_target()
    plant(cloud, m)
    directory = Directory(**({"uid-a": identity} if identity else {}))
    report, sent = run_worker(cloud, monkeypatch, at, ALLU_LIVE, directory=directory)
    assert sent == [] and report.checked == [] and report.fetches == 0
    assert why in report.rejected[m.id]
    assert report.monitors == []                                         # cannot keep a segment alive


def test_a_monitor_with_no_owner_is_refused_by_the_worker(cloud, make_monitor, monkeypatch, at):
    from tests.conftest import ALLU_LIVE

    m = monitor_for(make_monitor, "", "a@example.com")
    m.targets = live_target()
    plant(cloud, m)
    report, sent = run_worker(cloud, monkeypatch, at, ALLU_LIVE, directory=Directory())
    assert sent == [] and m.id in report.rejected


@pytest.mark.parametrize("interval", [0, 1, 7, 60, -5])
def test_an_interval_the_app_never_offers_is_refused(cloud, make_monitor, monkeypatch, at, interval):
    from tests.conftest import ALLU_LIVE

    m = monitor_for(make_monitor, "uid-a", "a@example.com")
    m.targets = live_target()
    plant(cloud, m, interval_minutes=interval)
    report, sent = run_worker(cloud, monkeypatch, at, ALLU_LIVE,
                              directory=Directory(**{"uid-a": verified("a@example.com", admin=True)}))
    assert sent == [] and "interval" in report.rejected[m.id]


def test_admin_only_features_are_dropped_for_ordinary_accounts_and_kept_for_the_admin(
        cloud, make_monitor, monkeypatch, at):
    from tests.conftest import ALLU_LIVE

    member = monitor_for(make_monitor, "uid-a", "a@example.com")
    admin = monitor_for(make_monitor, "uid-admin", "boss@example.com")
    for m in (member, admin):
        m.targets = live_target()
        plant(cloud, m, interval_minutes=5, categories=["0000000002|GOLD"], show_time="07:15 PM")
    directory = Directory(**{"uid-a": verified("a@example.com"),
                             "uid-admin": verified("boss@example.com", admin=True)})
    report, _ = run_worker(cloud, monkeypatch, at, ALLU_LIVE, directory=directory)
    by_id = {m.id: m for m in report.monitors}
    assert by_id[member.id].interval_minutes == 10
    assert by_id[member.id].categories == [] and by_id[member.id].show_time == ""
    assert by_id[admin.id].interval_minutes == 5
    assert by_id[admin.id].categories == ["0000000002|GOLD"]


def test_an_ordinary_account_is_held_to_its_active_monitor_limit_by_the_worker(
        cloud, make_monitor, monkeypatch, at):
    from tests.conftest import NOT_ON_SALE

    ids = []
    for i in range(policy.ACTIVE_MONITOR_LIMIT + 3):
        m = monitor_for(make_monitor, "uid-a", "a@example.com")
        m.targets = live_target()
        m.created_at = at - timedelta(minutes=100 - i)
        plant(cloud, m)
        ids.append(m.id)
    report, _ = run_worker(cloud, monkeypatch, at, NOT_ON_SALE,
                           directory=Directory(**{"uid-a": verified("a@example.com")}))
    assert {m.id for m in report.monitors} == set(ids[:policy.ACTIVE_MONITOR_LIMIT])   # the oldest five
    assert set(report.rejected) == set(ids[policy.ACTIVE_MONITOR_LIMIT:])


def test_the_admin_has_no_active_monitor_limit_in_the_worker(cloud, make_monitor, monkeypatch, at):
    from tests.conftest import NOT_ON_SALE

    for _ in range(policy.ACTIVE_MONITOR_LIMIT + 2):
        m = monitor_for(make_monitor, "uid-admin", "boss@example.com")
        m.targets = live_target()
        plant(cloud, m)
    report, _ = run_worker(cloud, monkeypatch, at, NOT_ON_SALE,
                           directory=Directory(**{"uid-admin": verified("boss@example.com", admin=True)}))
    assert len(report.monitors) == policy.ACTIVE_MONITOR_LIMIT + 2 and not report.rejected


def test_an_endless_monitor_is_pulled_back_to_the_limit_and_saved(cloud, make_monitor, monkeypatch, at):
    from tests.conftest import NOT_ON_SALE

    m = monitor_for(make_monitor, "uid-a", "a@example.com")
    m.targets = live_target()
    plant(cloud, m, monitor_until="2099-01-01T00:00:00+05:30")
    report, _ = run_worker(cloud, monkeypatch, at, NOT_ON_SALE,
                           directory=Directory(**{"uid-a": verified("a@example.com")}))
    assert m.id in {x.id for x in report.monitors}
    stored = cloud.docs["monitors"][m.id]["monitor_until"]
    assert datetime.fromisoformat(stored) == policy.max_until(at)
    assert stored < "2099"


@pytest.mark.parametrize("abuse", [
    {"targets": []},
    {"targets": [TheatreTarget(f"V{i}", "V", "", ANY_FORMAT).to_dict() for i in range(policy.MAX_TARGETS + 1)]},
    {"date_codes": [f"2026{i:04d}" for i in range(policy.MAX_DATE_CODES + 1)]},
])
def test_oversized_documents_are_refused(cloud, make_monitor, monkeypatch, at, abuse):
    from tests.conftest import NOT_ON_SALE

    m = monitor_for(make_monitor, "uid-a", "a@example.com")
    m.targets = live_target()
    plant(cloud, m, **abuse)
    report, sent = run_worker(cloud, monkeypatch, at, NOT_ON_SALE,
                              directory=Directory(**{"uid-a": verified("a@example.com")}))
    assert m.id in report.rejected and sent == [] and report.fetches == 0


def test_a_document_with_fields_the_app_never_writes_is_skipped_unread(cloud, make_monitor, monkeypatch, at, capsys):
    from tests.conftest import ALLU_LIVE

    m = monitor_for(make_monitor, "uid-a", "a@example.com")
    m.targets = live_target()
    plant(cloud, m, **{"cc": "victim@example.org", "::error::injected": 1})
    report, sent = run_worker(cloud, monkeypatch, at, ALLU_LIVE,
                              directory=Directory(**{"uid-a": verified("a@example.com")}))
    assert sent == [] and report.monitors == []
    out = capsys.readouterr().out
    assert "2 field(s) the application never writes" in out
    assert "victim" not in out and "::error::injected" not in out    # the writer's text stays out of the log


def test_the_worker_log_never_holds_a_full_address(cloud, make_monitor, monkeypatch, at, capsys):
    from tests.conftest import ALLU_LIVE

    m = monitor_for(make_monitor, "uid-a", "a@example.com")
    m.targets = live_target()
    plant(cloud, m, notify_email="victim@example.org")
    run_worker(cloud, monkeypatch, at, ALLU_LIVE,
               directory=Directory(**{"uid-a": verified("alice.private@example.com")}))
    out = capsys.readouterr().out
    assert "alice.private@example.com" not in out and "victim@example.org" not in out
    assert "a***@example.com" in out


def test_an_unreachable_account_directory_defers_without_checking_or_spinning(
        cloud, make_monitor, monkeypatch, at):
    """Fail closed: nothing is checked or sent while owners cannot be
    verified — but the monitors are postponed, not refused, and the segment
    keeps polling at its ordinary pace rather than retrying in a loop."""
    from monitor import worker
    from tests.conftest import ALLU_LIVE

    m = monitor_for(make_monitor, "uid-a", "a@example.com")
    m.targets = live_target()
    plant(cloud, m)

    def down(uids):
        raise policy.IdentityUnavailable("HTTP 503")

    report, sent = run_worker(cloud, monkeypatch, at, ALLU_LIVE, directory=down)
    assert sent == [] and report.checked == [] and report.deferred == [m.id]

    waits = []
    later = at + timedelta(minutes=55)
    ticks = iter([at, at, at, at, later, later, later, later])
    loop = worker.run_loop(max_minutes=50, poll_seconds=30, use_git=False, chain=False,
                           notifier=lambda *a: None, clock=lambda: next(ticks),
                           sleeper=waits.append)
    assert waits == [30.0] and loop.checks == 0


def test_refused_monitors_do_not_keep_a_segment_alive(cloud, make_monitor, monkeypatch, at):
    from monitor import worker

    m = monitor_for(make_monitor, "uid-a", "a@example.com")
    m.targets = live_target()
    plant(cloud, m, interval_minutes=1)
    cloud.as_worker()
    policy.set_identity_resolver(Directory(**{"uid-a": verified("a@example.com")}))
    loop = worker.run_loop(max_minutes=50, poll_seconds=30, use_git=False, chain=False,
                           notifier=lambda *a: None, clock=lambda: at, sleeper=lambda s: None)
    assert loop.stopped_reason == "nothing running" and loop.ticks == 1


def test_production_selects_firebase_accounts_and_fails_closed_without_a_credential(monkeypatch):
    policy.set_identity_resolver(None)
    assert policy.worker_resolver("json") == policy.LOCAL
    monkeypatch.setenv("FIREBASE_PROJECT_ID", PROJECT)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT", raising=False)
    resolver = policy.worker_resolver("firestore")
    assert resolver != policy.LOCAL
    with pytest.raises(policy.IdentityUnavailable):
        resolver(["uid-a"])


class _Response:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


def test_the_firebase_account_lookup_reads_verification_admin_and_disabled():
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append((url, json, headers))
        return _Response(200, {"users": [
            {"localId": "uid-a", "email": "a@example.com", "emailVerified": True,
             "customAttributes": '{"admin": true}'},
            {"localId": "uid-b", "email": "b@example.com", "emailVerified": False},
            {"localId": "uid-c", "email": "c@example.com", "emailVerified": True, "disabled": True},
            {"localId": "uid-d", "email": "d@example.com", "emailVerified": True,
             "customAttributes": '{"admin": "true"}'},
        ]})

    clock = {"t": 0.0}
    lookup = policy.FirebaseIdentityResolver(PROJECT, lambda: "tok", post=post, clock=lambda: clock["t"])
    found = lookup(["uid-a", "uid-b", "uid-c", "uid-d", "uid-missing"])
    assert found["uid-a"] == policy.OwnerIdentity("a@example.com", True, admin=True)
    assert not found["uid-b"].usable and not found["uid-c"].usable
    assert found["uid-d"].admin is False                                  # only the boolean true counts
    assert "uid-missing" not in found
    assert calls[0][0].endswith(f"/projects/{PROJECT}/accounts:lookup")
    assert calls[0][2] == {"Authorization": "Bearer tok"}
    lookup(["uid-a"])                                                     # cached
    assert len(calls) == 1
    clock["t"] += policy.FirebaseIdentityResolver.TTL + 1
    lookup(["uid-a"])
    assert len(calls) == 2


def test_a_failing_account_lookup_is_an_unavailable_answer_never_an_empty_one():
    for post in (lambda *a, **k: _Response(403, {}),
                 lambda *a, **k: (_ for _ in ()).throw(OSError("down"))):
        with pytest.raises(policy.IdentityUnavailable):
            policy.FirebaseIdentityResolver(PROJECT, lambda: "tok", post=post)(["uid-a"])


# ══════════════════════════════════════════════════════════════════════════
# M1 · Starting a workflow is authorised on the server
# ══════════════════════════════════════════════════════════════════════════
@pytest.fixture
def github(monkeypatch):
    """Record what would have been dispatched; never reach GitHub."""
    calls = []
    monkeypatch.setattr(store, "github_token", lambda: "test-token")

    class Workflow:
        def __init__(self, name):
            self.name = name

        def create_dispatch(self, ref, inputs):
            calls.append((self.name, dict(inputs)))
            return True

    class Repo:
        def get_workflow(self, name):
            return Workflow(name)

    class Github:
        def __init__(self, token):
            pass

        def get_repo(self, name):
            return Repo()

    import github as pygithub

    monkeypatch.setattr(pygithub, "Github", Github)
    monkeypatch.setattr(dispatch, "_last", {})
    return calls


def as_account(uid, *, admin=False):
    state_mod.set_scope_provider(lambda: Scope("", uid, lambda: "", admin=admin, email=f"{uid}@example.com"))


def test_nothing_outside_actions_dispatches_without_the_authorisation_layer(github):
    assert store.dispatch_workflow("catalogue-sync.yml", {"city": "hyderabad"})[0] is False
    assert store.request_check_now()[0] is False
    assert github == []


def test_an_ordinary_account_cannot_check_everything_or_refresh_the_catalogue(github):
    as_account("uid-a")
    try:
        with pytest.raises(dispatch.DispatchNotAllowed):
            dispatch.check_now("")
        with pytest.raises(dispatch.DispatchNotAllowed):
            dispatch.refresh_catalogue("hyderabad")
    finally:
        state_mod.set_scope_provider(None)
    assert github == []


def test_nobody_signed_in_can_dispatch_anything(github):
    state_mod.set_scope_provider(None)
    with pytest.raises(dispatch.DispatchNotAllowed):
        dispatch.check_now("abc")
    with pytest.raises(dispatch.DispatchNotAllowed):
        dispatch.refresh_catalogue("hyderabad")
    assert github == []


def test_the_admin_can_check_everything_and_refresh_the_catalogue(github):
    as_account("uid-admin", admin=True)
    try:
        assert dispatch.check_now("")[0] is True
        assert dispatch.refresh_catalogue("hyderabad")[0] is True
    finally:
        state_mod.set_scope_provider(None)
    assert github == [("bookmyshow-monitor.yml", {"force": "true", "dry_run": "false", "monitor_id": ""}),
                      ("catalogue-sync.yml", {"city": "hyderabad"})]


def test_an_owner_may_ask_for_their_own_running_monitor_once_a_minute(github, make_monitor):
    as_account("uid-a")
    try:
        mine = make_monitor(owner_uid="uid-a", email="uid-a@example.com")
        state_mod.upsert_monitor(mine, mirror=False)
        assert dispatch.check_now(mine.id)[0] is True
        with pytest.raises(dispatch.DispatchNotAllowed, match="Try again"):
            dispatch.check_now(mine.id)
        dispatch._last.clear()
        state_mod.stop_monitor(mine.id, mirror=False)
        with pytest.raises(dispatch.DispatchNotAllowed):
            dispatch.check_now(mine.id)                                    # not running any more
    finally:
        state_mod.set_scope_provider(None)
    assert len(github) == 1 and github[0][1]["monitor_id"] == mine.id


def test_nobody_can_ask_for_someone_elses_monitor(github, make_monitor):
    as_account("uid-b")
    try:
        theirs = make_monitor(owner_uid="uid-b", email="uid-b@example.com")
        state_mod.upsert_monitor(theirs, mirror=False)
    finally:
        state_mod.set_scope_provider(None)
    as_account("uid-a")
    try:
        with pytest.raises(dispatch.DispatchNotAllowed):
            dispatch.check_now(theirs.id)
    finally:
        state_mod.set_scope_provider(None)
    assert github == []


def test_the_settings_buttons_are_the_admins_alone(signed_in, github):
    member = run("Settings")
    keys = {b.key for b in member.button}
    assert "sync_now" not in keys and "run_now" not in keys

    signed_in.admin = True
    admin = run("Settings")
    assert {"sync_now", "run_now"} <= {b.key for b in admin.button}
    admin.button(key="run_now").click().run()
    assert github and github[-1][0] == "bookmyshow-monitor.yml" and github[-1][1]["monitor_id"] == ""


def test_the_token_guidance_asks_for_actions_only():
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "Actions → Read and write" in env and "no Contents" in env
    assert "Contents: read & write" not in (ROOT / "config" / "store.py").read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════
# L1 · End dates
# ══════════════════════════════════════════════════════════════════════════
def test_the_store_refuses_a_monitor_ending_beyond_the_limit(make_monitor):
    as_account("uid-test-1")
    try:
        far = make_monitor(until=now_ist() + timedelta(days=policy.MAX_MONITOR_DAYS + 5))
        with pytest.raises(policy.MonitorNotAllowed):
            state_mod.upsert_monitor(far, mirror=False)
        ok = make_monitor(until=now_ist() + timedelta(days=policy.MAX_MONITOR_DAYS - 1))
        state_mod.upsert_monitor(ok, mirror=False)
        assert [m.id for m in state_mod.load_monitors()] == [ok.id]
    finally:
        state_mod.set_scope_provider(None)


def test_extending_cannot_push_a_monitor_past_the_limit(make_monitor):
    as_account("uid-test-1")
    try:
        m = make_monitor(until=now_ist() + timedelta(days=policy.MAX_MONITOR_DAYS - 0.5))
        state_mod.upsert_monitor(m, mirror=False)
        state_mod.stop_monitor(m.id, mirror=False)
        with pytest.raises(policy.MonitorNotAllowed):
            state_mod.extend_monitor(m.id, 24, mirror=False)
        assert state_mod.get_monitor(m.id).status is MonitorStatus.STOPPED   # nothing changed
    finally:
        state_mod.set_scope_provider(None)


def test_the_wizard_offers_no_end_date_beyond_the_limit(seeded):
    app = run(step=5, location="hyderabad", movie_id=seeded, theatres=["ALLU"],
              formats={"ALLU": [ANY_FORMAT]})
    picker = app.date_input(key="until_date")
    assert picker.max == policy.max_until().date()
    picker.set_value(policy.max_until().date()).run()
    app.button(key="start").click().run()
    [monitor] = state_mod.load_monitors()
    assert monitor.monitor_until <= policy.max_until()


# ══════════════════════════════════════════════════════════════════════════
# Rules ⇄ policy: the file states the same limits the code enforces
# ══════════════════════════════════════════════════════════════════════════
def test_the_rules_file_states_the_policys_limits():
    rules = (ROOT / "firestore.rules").read_text(encoding="utf-8")
    fields = re.search(r"function monitorFields\(\) \{\s*return \[(.*?)\];", rules, re.S).group(1)
    assert set(re.findall(r"'([a-z_]+)'", fields)) == policy.ALLOWED_MONITOR_FIELDS
    assert f"d.interval_minutes in {list(policy.MEMBER_INTERVALS)}" in rules
    assert "isAdmin() && d.interval_minutes == 5" in rules and min(policy.ADMIN_INTERVALS) == 5
    assert f"d.targets.size() <= {policy.MAX_TARGETS}" in rules
    assert f"d.date_codes.size() <= {policy.MAX_DATE_CODES}" in rules
    assert f"d.categories.size() <= {policy.MAX_CATEGORIES}" in rules
    assert f"d.notify_email.size() <= {policy.MAX_EMAIL_LENGTH}" in rules
    assert f"duration.value({policy.MAX_MONITOR_DAYS + 2}, 'd')" in rules
    assert "d.notify_email.lower() == request.auth.token.email.lower()" in rules
    assert "request.auth.token.email_verified == true" in rules
    for collection in ("monitors", "history"):
        assert re.search(rf"match /{collection}/\{{id\}} \{{[^}}]*allow create: if ownsIncoming\(\)", rules, re.S)
    assert "verified() && request.resource.data.owner_uid == request.auth.uid" in rules


def test_the_emulator_suite_is_in_the_repository():
    suite = ROOT / "tests" / "firestore_rules" / "rules.test.mjs"
    text = suite.read_text(encoding="utf-8")
    assert "initializeTestEnvironment" in text and "firestore.rules" in text
    assert text.count("test(") >= 14


# ══════════════════════════════════════════════════════════════════════════
# L4 · No signed-in person's data in the public repository
# ══════════════════════════════════════════════════════════════════════════
def test_a_deployment_without_firestore_refuses_rather_than_fall_back_to_json(make_monitor):
    state_mod.set_scope_provider(lambda: Scope("", "uid-a", lambda: "", firestore_enabled=False,
                                               deployment=True, email="a@example.com"))
    try:
        with pytest.raises(state_mod.StorageUnavailable):
            state_mod.load_monitors()
        with pytest.raises(state_mod.StorageUnavailable):
            state_mod.upsert_monitor(make_monitor(owner_uid="uid-a"), mirror=True)
    finally:
        state_mod.set_scope_provider(None)
    assert not store.MONITORS_FILE.exists()


def test_the_app_fails_closed_on_a_host_with_sign_in_but_no_firestore(monkeypatch, isolated_data):
    monkeypatch.delenv("TICKETRADAR_LOCAL_JSON_STORE", raising=False)
    monkeypatch.setenv("FIREBASE_WEB_API_KEY", "test-key")
    monkeypatch.delenv("TICKETRADAR_FIRESTORE_ENABLED", raising=False)
    pushed = []
    monkeypatch.setattr(store, "push_to_github", lambda *a, **k: pushed.append(a) or True)
    app = run("Home")
    assert not app.exception, [str(e) for e in app.exception]
    assert any("storage isn't available" in e.value for e in app.error)
    assert "start" not in {b.key for b in app.button}
    assert pushed == [] and not (isolated_data / "monitors.json").exists()


def test_a_signed_in_persons_json_writes_are_never_mirrored_to_github(monkeypatch, make_monitor):
    pushed = []
    monkeypatch.setattr(store, "push_to_github", lambda path, body, message: pushed.append(path.name) or True)
    monkeypatch.setattr(store, "github_token", lambda: "test-token")
    state_mod.set_scope_provider(lambda: Scope("", "uid-a", lambda: "", firestore_enabled=False,
                                               email="a@example.com"))
    try:
        m = make_monitor(owner_uid="uid-a")
        state_mod.upsert_monitor(m, mirror=True)
        state_mod.record_history(m, "CREATED", "x", mirror=True)
        state_mod.save_settings({"display_name": "A"}, mirror=True)
        state_mod.stop_monitor(m.id, mirror=True)
        state_mod.purge_user_data(mirror=True)
    finally:
        state_mod.set_scope_provider(None)
    assert pushed == []


# ══════════════════════════════════════════════════════════════════════════
# M3 · The admin-grant workflow never prints the account
# ══════════════════════════════════════════════════════════════════════════
def test_the_grant_workflow_passes_the_account_through_neither_env_nor_command_line():
    text = (ROOT / ".github" / "workflows" / "grant-admin.yml").read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    inputs = doc[True]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"account", "action"}
    step = next(s for s in doc["jobs"]["claim"]["steps"] if s.get("name") == "Manage admin claim")
    assert "inputs.account" not in json.dumps(step) and "INPUT_EMAIL" not in json.dumps(step)
    assert "--from-event" in step["run"]
    assert "inputs.email" not in text


def test_grant_admin_masks_the_account_before_printing_anything(tmp_path, monkeypatch, capsys):
    from tools import grant_admin

    event = tmp_path / "event.json"
    event.write_text(json.dumps({"inputs": {"account": "alice.private@example.com", "action": "show"}}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", PROJECT)
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT", '{"client_email": "w@test", "type": "service_account"}')
    monkeypatch.setattr(grant_admin, "access_token", lambda info: "tok")
    asked = []

    def call(project, token, action, body):
        asked.append(body)
        return {"users": [{"localId": "uid-alice-0123456789", "email": "alice.private@example.com"}]}

    monkeypatch.setattr(grant_admin, "call", call)
    assert grant_admin.main(["--from-event"]) == 0
    lines = capsys.readouterr().out.splitlines()
    masks = [line for line in lines if line.startswith("::add-mask::")]
    assert lines[0] == "::add-mask::alice.private@example.com"           # before anything else
    assert "::add-mask::uid-alice-0123456789" in masks
    visible = [line for line in lines if not line.startswith("::add-mask::")]
    assert visible and not any("alice.private" in line or "uid-alice-0123456789" in line for line in visible)
    assert asked == [{"email": ["alice.private@example.com"]}]


def test_grant_admin_finds_an_account_by_uid(tmp_path, monkeypatch, capsys):
    from tools import grant_admin

    event = tmp_path / "event.json"
    event.write_text(json.dumps({"inputs": {"account": "uid-alice-0123456789"}}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("FIREBASE_PROJECT_ID", PROJECT)
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT", '{"client_email": "w@test", "type": "service_account"}')
    monkeypatch.setattr(grant_admin, "access_token", lambda info: "tok")
    asked = []
    monkeypatch.setattr(grant_admin, "call", lambda p, t, a, body: asked.append(body) or
                        {"users": [{"localId": "uid-alice-0123456789", "email": "a@example.com"}]})
    assert grant_admin.main(["--from-event"]) == 0
    assert asked == [{"localId": ["uid-alice-0123456789"]}]
    assert "uid-alice-0123456789" not in capsys.readouterr().out


def test_no_workflow_hands_an_email_input_to_a_step():
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"inputs\.[a-z_]*email", text), path.name


# ══════════════════════════════════════════════════════════════════════════
# Unchanged: two accounts stay apart, and the admin keeps the admin's tools
# ══════════════════════════════════════════════════════════════════════════
def test_two_accounts_still_see_only_their_own_monitors(cloud, make_monitor):
    cloud.as_user("uid-a")
    a = monitor_for(make_monitor, "uid-a", "a@example.com")
    state_mod.upsert_monitor(a, mirror=False)
    cloud.as_user("uid-b")
    b = monitor_for(make_monitor, "uid-b", "b@example.com")
    state_mod.upsert_monitor(b, mirror=False)
    assert [m.id for m in state_mod.load_monitors()] == [b.id]
    state_mod.delete_monitor(a.id, mirror=False)                          # not theirs: a no-op
    cloud.as_user("uid-a")
    assert [m.id for m in state_mod.load_monitors()] == [a.id]


def test_the_admin_can_still_save_a_five_minute_category_watch(make_monitor):
    as_account("uid-test-1", admin=True)
    try:
        m = make_monitor(interval=5)
        m.categories = ["0000000002|GOLD"]
        state_mod.upsert_monitor(m, mirror=False)
        [stored] = state_mod.load_monitors()
    finally:
        state_mod.set_scope_provider(None)
    assert stored.interval_minutes == 5 and stored.categories == ["0000000002|GOLD"]


def test_an_ordinary_account_cannot_save_a_five_minute_or_odd_interval(make_monitor):
    as_account("uid-test-1")
    try:
        with pytest.raises(ValueError):
            state_mod.upsert_monitor(make_monitor(interval=5), mirror=False)
        with pytest.raises(policy.MonitorNotAllowed):
            state_mod.upsert_monitor(make_monitor(interval=1), mirror=False)
    finally:
        state_mod.set_scope_provider(None)
