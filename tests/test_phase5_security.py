"""Phase 5 security remediation — one regression test per confirmed fix.

Each section names the original audit finding. The rules-side checks of the
same limits run in the Firebase emulator (``tests/firestore_rules``); the
real-browser check of the cookie bridge is
``tests/test_bridge_origin_browser.py``. Everything here is local: in-memory
Firestore, fake Firebase answers, no network.
"""

from __future__ import annotations

import base64
import json
import re
import time
import types
from datetime import timedelta
from pathlib import Path

import pytest

from auth import firebase, google, session
from auth.firebase import AuthError, Credentials
from monitor import checker, dispatch, policy
from monitor import state as state_mod
from monitor.checker import run_once
from monitor.models import (
    ANY_FORMAT,
    Availability,
    Monitor,
    MovieRef,
    Showtime,
    Snapshot,
    TheatreTarget,
    Venue,
)
from monitor.state import MonitorState, Scope
from tests.test_isolation import PROJECT
from tests.test_worker_reads import OWNER, firestore_worker  # noqa: F401 - a fixture

ROOT = Path(__file__).resolve().parent.parent
#: The real ``restore``, taken at import — before the suite's ``signed_in``
#: fixture replaces it for each test.
REAL_RESTORE = session.restore


def _film(code="ET00512345", variants=()) -> MovieRef:
    return MovieRef(platform="bookmyshow", event_code=code, title=f"Film {code}", region_code="HYD",
                    region_slug="hyderabad", city="Hyderabad", variants=tuple(variants))


def _siblings(n: int) -> tuple:
    return tuple((f"ET9{i:07d}", "4DX 3D") for i in range(n))


def _monitor(at, *, movie=None, dates=(), owner=OWNER, interval=10) -> Monitor:
    return Monitor(movie=movie or _film(), targets=[TheatreTarget("ALLU", "ALLU Cinemas", "Kokapet", ANY_FORMAT)],
                   interval_minutes=interval, monitor_until=at + timedelta(days=2), date_codes=list(dates),
                   notify_email="w@example.com", owner_uid=owner)


def _dates(n: int) -> list[str]:
    """``n`` distinct show dates (the cost counts distinct dates)."""
    from datetime import date

    first = date(2026, 12, 1)
    return [(first + timedelta(days=i)).strftime("%Y%m%d") for i in range(n)]


# ══════════════════════════════════════════════════════════════════════════
# SEC-01 · One crafted monitor must not stall the worker
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("variants,dates,problem", [
    (13, 0, "too many sibling events"),
    (5000, 30, "too many sibling events"),
    (2, 41, "would cost 123 requests"),
    (1, 62, "would cost 124 requests"),
], ids=["13 siblings", "the audit's 5000x30", "41 dates x 3 events", "62 dates x 2 events"])
def test_an_oversized_or_expensive_monitor_is_refused(at, variants, dates, problem):
    m = _monitor(at, movie=_film(variants=_siblings(variants)), dates=_dates(dates))
    assert problem in policy.shape_problem(m)


@pytest.mark.parametrize("variants,dates", [(12, 0), (2, 40), (5, 20), (0, 62)])
def test_a_legitimate_monitor_at_the_limits_is_accepted(at, variants, dates):
    m = _monitor(at, movie=_film(variants=_siblings(variants)), dates=_dates(dates))
    assert policy.shape_problem(m) == ""
    assert policy.request_cost(m) <= policy.MAX_REQUESTS_PER_CHECK


@pytest.mark.parametrize("code", ["ET1\r\nX-Injected: 1", "ET12a", "../../etc", "", "ET" + "1" * 13])
def test_an_event_code_must_be_one_bookmyshow_issues(at, code):
    assert "event code" in policy.shape_problem(_monitor(at, movie=_film(code=code)))
    if code:   # (an empty sibling code is dropped by the model before anything reads it)
        assert "event code" in policy.shape_problem(
            _monitor(at, movie=_film(variants=((code, "x"),))))


@pytest.mark.parametrize("date", ["2026-12-01", "20261\n01", "tomorrow", "19991231"])
def test_a_watched_date_must_be_a_date(at, date):
    assert policy.shape_problem(_monitor(at, dates=[date])) == "a watched date is not a date"


def test_the_worker_refuses_the_audit_monitor_without_making_its_requests(firestore_worker, monkeypatch):  # noqa: F811 - the imported fixture
    """The audit's reproduction E, end to end: the crafted document is
    refused by the worker's screen, the honest monitor beside it is checked,
    and exactly one listing read is made."""
    w = firestore_worker
    crafted = w.seed(_monitor(w.now, movie=_film("ET00500001", _siblings(5000)), dates=_dates(30),
                              owner="uid-attacker"), checked=False)
    honest = w.seed(_monitor(w.now, movie=_film("ET00500002")), checked=False)
    reads = []

    def fetch(movie, date_codes=None):
        reads.append(movie.event_code)
        return checker.Listing(snapshot=Snapshot(movie=movie, venues=[], showtimes=[]))

    monkeypatch.setattr(checker, "fetch_listing", fetch)
    report = run_once(at=w.now, force=True, notifier=lambda m, c: None)
    assert "too many sibling events" in report.rejected[crafted.id]
    assert report.checked == [honest.id] and reads == ["ET00500002"]


def test_saving_an_expensive_monitor_is_refused_with_a_reason(monkeypatch, at):
    """The app says so before saving; the worker is not the first to know."""
    state_mod.set_scope_provider(lambda: Scope(PROJECT, "uid-test-1", lambda: "id.uid-test-1",
                                               firestore_enabled=False, email="w@example.com", admin=True))
    try:
        with pytest.raises(policy.MonitorNotAllowed, match="Watch fewer dates"):
            state_mod.upsert_monitor(_monitor(at, movie=_film(variants=_siblings(2)), dates=_dates(41),
                                              owner="uid-test-1"), mirror=False)
        with pytest.raises(policy.MonitorNotAllowed, match="too many sibling events"):
            state_mod.upsert_monitor(_monitor(at, movie=_film(variants=_siblings(13)), owner="uid-test-1"),
                                     mirror=False)
        state_mod.upsert_monitor(_monitor(at, movie=_film(variants=_siblings(5)), dates=_dates(20),
                                          owner="uid-test-1"), mirror=False)
    finally:
        state_mod.set_scope_provider(None)


# ── The per-tick request budget: fairness between accounts ──────────────────
def _bookable(movie, date_codes=None):
    show = Showtime("ALLU", "ALLU Cinemas", "S1", "20261201", "07:30 PM", "1930", "Dolby Cinema",
                    Availability.AVAILABLE)
    return checker.Listing(snapshot=Snapshot(movie=movie, venues=[Venue("ALLU", "ALLU Cinemas")],
                                             showtimes=[show]))


def test_one_accounts_expensive_monitors_wait_their_turn(firestore_worker, monkeypatch):  # noqa: F811 - the imported fixture
    """Three accounts, each with a monitor costing 120 requests (the per-check
    maximum), and one cheap monitor. The budget (240) takes the two most
    overdue reads this tick; the rest wait, untouched, and are read first on
    the next tick. Nobody is starved and nobody is checked twice."""
    w = firestore_worker
    monkeypatch.setattr(checker, "fetch_listing", lambda movie, date_codes=None: (
        reads.append(movie.event_code), _bookable(movie))[1])
    reads: list[str] = []
    heavy = [w.seed(_monitor(w.now, movie=_film(f"ET0060000{i}", _siblings(2)), dates=_dates(40),
                             owner=f"uid-heavy-{i}"), checked=False) for i in range(3)]
    light = w.seed(_monitor(w.now, movie=_film("ET00700001")), checked=False)

    first = run_once(at=w.now, force=True, notifier=lambda m, c: None)
    assert first.budgeted_requests <= policy.TICK_REQUEST_BUDGET
    assert len(first.budget_deferred) == 2
    waiting = {m.id for g in first.budget_deferred for m in g.monitors}
    for monitor_id in waiting:
        # Not checked: at most the blank record every running monitor gets.
        record = MonitorState.from_dict(w.docs.get("monitor_state", {}).get(monitor_id, {}))
        assert record.check_count == 0 and record.last_check_at is None and not record.targets

    second = run_once(at=w.now + timedelta(seconds=30), notifier=lambda m, c: None)
    assert waiting <= set(second.checked), "what waited is read first next tick"
    assert set(first.checked) | set(second.checked) == {m.id for m in [*heavy, light]}
    assert len(reads) == len(set(reads)) == 4, "every read made once"


def test_a_tick_with_room_for_everything_is_unchanged(firestore_worker, monkeypatch):  # noqa: F811 - the imported fixture
    w = firestore_worker
    monkeypatch.setattr(checker, "fetch_listing", _bookable)
    for i in range(4):
        w.seed(_monitor(w.now, movie=_film(f"ET0080000{i}")), checked=False)
    report = run_once(at=w.now, force=True, notifier=lambda m, c: None)
    assert report.budget_deferred == [] and len(report.checked) == 4


def test_a_single_read_over_budget_is_still_made_so_nothing_starves(monkeypatch):
    monkeypatch.setattr(policy, "TICK_REQUEST_BUDGET", 5)
    group = types.SimpleNamespace(key=types.SimpleNamespace(request_count=50), monitors=[])
    taken, left = checker._within_budget([group], {})
    assert taken == [group] and left == []


def test_the_rules_state_the_same_bounds_as_the_policy():
    rules = (ROOT / "firestore.rules").read_text(encoding="utf-8")
    assert f"m.variants.size() <= {policy.MAX_VARIANTS}" in rules
    assert f"requestCost(d) <= {policy.MAX_REQUESTS_PER_CHECK}" in rules
    assert "validHistory(request.resource.data)" in rules


# ══════════════════════════════════════════════════════════════════════════
# SEC-02 · The cookie bridge answers its own app only (static half; the
# browser half is tests/test_bridge_origin_browser.py)
# ══════════════════════════════════════════════════════════════════════════
BRIDGE = (ROOT / "auth" / "bridge" / "index.html").read_text(encoding="utf-8")


def test_the_bridge_checks_source_and_origin_before_acting():
    on_message = BRIDGE[BRIDGE.index("function onMessage"):]
    assert on_message.index("if (!trusted(event)) return;") < on_message.index("writeCookie(")
    assert "event.source === window.parent && event.origin === SELF" in BRIDGE


def test_the_bridge_never_replies_to_any_origin():
    assert 'postMessage(msg, "*")' not in BRIDGE and "postMessage(msg, SELF)" in BRIDGE


def test_the_bridge_touches_only_its_own_cookies():
    assert 'OWN_COOKIES = ["tr_session", "tr_session_started", "tr_oauth_state"]' in BRIDGE
    assert "if (!ops[i] || !own(ops[i].name)) continue;" in BRIDGE
    assert "if (own(names[n]))" in BRIDGE


def test_the_cross_origin_parent_read_is_guarded_on_purpose():
    listener = BRIDGE[BRIDGE.index("function installSignalListener"):BRIDGE.index("function signalAndClose")]
    assert re.search(r"try \{\s*target = window\.parent;\s*if \(!target \|\| target\.__trGoogleListen\) return;",
                     listener)


# ══════════════════════════════════════════════════════════════════════════
# SEC-04 / SEC-08b · Revoked sessions end; the deadline and the admin claim
# come from Firebase, not the browser
# ══════════════════════════════════════════════════════════════════════════
def _jwt(**claims) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJSUzI1NiJ9.{body}.signature"


@pytest.fixture
def bare(monkeypatch):
    state: dict = {}
    monkeypatch.setattr(session, "st", types.SimpleNamespace(
        session_state=state, context=types.SimpleNamespace(cookies={}, headers={})))
    monkeypatch.setenv("FIREBASE_WEB_API_KEY", "test-key")
    return state


def _signed_in(bare, *, admin=False, expires_in=-1):
    bare[session.USER_KEY] = session.AuthUser(
        uid="uid-ravi", email="ravi@example.com", id_token="old", refresh_token="refresh-1",
        expires_at=time.time() + expires_in, admin=admin)
    bare[session.SESSION_EXPIRES_KEY] = time.time() + session.SESSION_SECONDS


@pytest.mark.parametrize("code", sorted(session.SESSION_ENDING_REFRESH))
def test_a_refresh_google_refuses_ends_the_session(bare, monkeypatch, code):
    """A disabled, deleted or revoked account used to keep working on its last
    ID token; now the first refresh that Google refuses signs it out."""
    _signed_in(bare)

    def refused(self, token):
        raise AuthError("no", code, 400)

    monkeypatch.setattr(firebase.FirebaseAuth, "refresh", refused)
    assert session.id_token() == ""
    assert session.current_user() is None
    assert bare.get("auth_cookie_clear") is True


def test_a_network_failure_during_refresh_keeps_the_session(bare, monkeypatch):
    _signed_in(bare)

    def offline(self, token):
        raise AuthError("offline", "network", 0)

    monkeypatch.setattr(firebase.FirebaseAuth, "refresh", offline)
    assert session.id_token() == "old" and session.current_user() is not None


@pytest.mark.parametrize("before,claims,after", [
    (True, {"user_id": "uid-ravi"}, False),                # claim revoked since sign-in
    (False, {"user_id": "uid-ravi", "admin": True}, True),  # claim granted since sign-in
    (True, {"user_id": "uid-ravi", "admin": "true"}, False),  # only the boolean counts
])
def test_the_admin_claim_follows_firebase_at_each_refresh(bare, monkeypatch, before, claims, after):
    _signed_in(bare, admin=before)
    fresh = Credentials(uid="uid-ravi", email="", display_name="", id_token=_jwt(**claims),
                        refresh_token="refresh-2", expires_in=3600)
    monkeypatch.setattr(firebase.FirebaseAuth, "refresh", lambda self, token: fresh)
    session.id_token()
    assert session.is_admin() is after


def _restore_with(bare, monkeypatch, *, auth_time: float, cookie_started: str):
    token = _jwt(user_id="uid-ravi", auth_time=int(auth_time))
    monkeypatch.setattr(session, "_cookie_jar", lambda: {
        session.COOKIE: "refresh-1", session.SESSION_STARTED_COOKIE: cookie_started})
    monkeypatch.setattr(firebase.FirebaseAuth, "refresh", lambda self, t: Credentials(
        uid="uid-ravi", email="", display_name="", id_token=token, refresh_token="refresh-1", expires_in=3600))
    monkeypatch.setattr(firebase.FirebaseAuth, "lookup", lambda self, t: {
        "uid": "uid-ravi", "email": "ravi@example.com", "display_name": "Ravi",
        "email_verified": True, "admin": False})
    return REAL_RESTORE()


@pytest.mark.parametrize("forged", ["", str(time.time() + 86400), str(time.time())],
                         ids=["cookie removed", "cookie in the future", "cookie reset to now"])
def test_a_forged_session_start_cannot_extend_the_seven_days(bare, monkeypatch, forged):
    """Firebase says the person signed in eight days ago. Whatever the
    browser-written companion cookie claims, the session is over."""
    user = _restore_with(bare, monkeypatch, auth_time=time.time() - 8 * 86400, cookie_started=forged)
    assert user is None and session.current_user() is None
    assert bare.get("auth_cookie_clear") is True


def test_a_recent_sign_in_restores_and_keeps_its_own_deadline(bare, monkeypatch):
    signed_in = time.time() - 2 * 86400
    user = _restore_with(bare, monkeypatch, auth_time=signed_in, cookie_started=str(time.time()))
    assert user is not None
    assert bare[session.SESSION_EXPIRES_KEY] == pytest.approx(signed_in + session.SESSION_SECONDS, abs=2)


@pytest.mark.parametrize("token", ["", "abc", "a.b", "a.!!!.c", "a." + base64.urlsafe_b64encode(b"[1]").decode() + ".c"])
def test_token_claims_of_anything_malformed_is_empty(token):
    assert firebase.token_claims(token) == {}


# ══════════════════════════════════════════════════════════════════════════
# SEC-06 · The app never falls back to the worker's all-accounts credential
# ══════════════════════════════════════════════════════════════════════════
def test_the_app_refuses_the_service_account_when_nobody_is_signed_in(monkeypatch):
    monkeypatch.setenv("FIREBASE_PROJECT_ID", PROJECT)
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT", '{"client_email": "worker@test", "type": "service_account"}')
    monkeypatch.setattr("config.firestore.service_account_token_getter", lambda info: (lambda: "admin"))
    monkeypatch.setattr(state_mod, "_admin", None)
    state_mod.set_scope_provider(lambda: None)                 # the app, nobody signed in
    try:
        with pytest.raises(state_mod.StorageUnavailable):
            state_mod.load_monitors()
    finally:
        state_mod.set_scope_provider(None)
    # The worker (no scope provider) still uses it, exactly as before.
    assert state_mod.backend_name() == "firestore"


# ══════════════════════════════════════════════════════════════════════════
# SEC-08 · The smaller items
# ══════════════════════════════════════════════════════════════════════════
def test_sec08a_the_verified_banner_never_repeats_the_url():
    source = (ROOT / "ui" / "login.py").read_text(encoding="utf-8")
    assert 'f"{verified} is verified' not in source
    assert "Your email address is verified — welcome to TicketRadar." in source


@pytest.mark.parametrize("url,kept", [
    ("https://in.bookmyshow.com/movies/hyderabad/x/buytickets/ET00512345/20261201", True),
    ("javascript:alert(1)", False),
    ("http://in.bookmyshow.com/x", False),
    ("https://evil.example/in.bookmyshow.com", False),
    ("https://in.bookmyshow.com.evil.example/x", False),
    ("", False),
])
def test_sec08c_cards_link_only_to_the_platforms_own_pages(url, kept):
    from ui import components as C

    assert C.safe_href(url, "bookmyshow") == (url if kept else "")
    chip = C._chip("07:30 PM", C.safe_href(url, "bookmyshow"))
    assert ("href=" in chip) is kept


def test_sec08d_the_google_token_is_url_encoded_in_the_firebase_request(monkeypatch):
    seen = {}

    def post(url, params, payload):
        seen.update(payload)
        return 400, {"error": {"message": "INVALID_IDP_RESPONSE"}}

    monkeypatch.setattr(firebase, "_post", post)
    with pytest.raises(AuthError):
        firebase.FirebaseAuth("k").sign_in_with_google("tok&providerId=evil.com", "https://app/")
    assert seen["postBody"] == "id_token=tok%26providerId%3Devil.com&providerId=google.com"


def test_sec08d_a_google_token_for_another_sign_in_attempt_is_refused():
    state = google.new_state()
    assert google.nonce_matches(_jwt(nonce=google.nonce_for(state)), state)
    assert not google.nonce_matches(_jwt(nonce=google.nonce_for("another-attempt")), state)
    assert not google.nonce_matches(_jwt(sub="x"), state)                 # no nonce at all
    assert google.nonce_matches("not-a-jwt", state)                       # Firebase's check decides


def test_sec08f_the_cooldown_table_forgets_what_no_longer_matters(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(dispatch, "_clock", lambda: now[0])
    monkeypatch.setattr(dispatch, "_last", {(f"u{i}", "m"): 0.0 for i in range(dispatch._PRUNE_ABOVE + 5)})
    dispatch._cooldown("uid-new", "m1")
    assert len(dispatch._last) == 1 and ("uid-new", "m1") in dispatch._last


# ══════════════════════════════════════════════════════════════════════════
# SEC-05 · Pinned, hashed dependencies; CI without secrets
# ══════════════════════════════════════════════════════════════════════════
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
LOCKS = {name: (ROOT / "ci" / f"requirements-{name}.lock").read_text(encoding="utf-8") for name in ("worker", "dev")}


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_workflow_installs_only_from_a_hashed_lock(path):
    for line in re.findall(r"pip install[^\n]*", path.read_text(encoding="utf-8")):
        assert "--require-hashes" in line and re.search(r"-r ci/requirements-(worker|dev)\.lock", line), line


@pytest.mark.parametrize("name", ["worker", "dev"])
def test_every_locked_package_is_pinned_and_hashed(name):
    blocks = re.split(r"\n(?=[A-Za-z0-9])", LOCKS[name].strip())
    packages = [b for b in blocks if not b.startswith("#")]
    assert len(packages) > 30
    for block in packages:
        assert re.match(r"^[A-Za-z0-9_.\-\[\],]+==[^\s]+ \\", block), block.splitlines()[0]
        assert "--hash=sha256:" in block, block.splitlines()[0]


def test_the_lock_satisfies_the_apps_own_requirements():
    """The worker runs what requirements.txt allows (Streamlit Cloud installs
    from that file); the lock only fixes the exact versions and hashes."""
    from packaging.requirements import Requirement

    pinned = dict(re.findall(r"^([A-Za-z0-9_.\-]+)==([^\s]+)", LOCKS["worker"], re.MULTILINE))
    pinned = {k.lower().replace("_", "-"): v for k, v in pinned.items()}
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        req = Requirement(line)
        name = req.name.lower().replace("_", "-")
        assert name in pinned, name
        assert req.specifier.contains(pinned[name], prereleases=True), (name, pinned[name])


def test_ci_runs_with_a_read_only_token_and_no_secrets():
    text = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert re.search(r"^permissions:\n  contents: read\n", text, re.MULTILINE)
    assert "secrets." not in text
    assert "pull_request_target" not in text


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_workflow_runs_untrusted_pull_request_code_with_secrets(path):
    text = path.read_text(encoding="utf-8")
    assert "pull_request_target" not in text
    if "pull_request" in text:
        assert "secrets." not in text, f"{path.name} would hand secrets to pull-request code"


@pytest.mark.parametrize("name", ["bms-diagnose", "pvr-diagnose", "grant-admin", "tests"])
def test_read_only_workflows_do_not_keep_the_token_in_git_config(name):
    text = (ROOT / ".github" / "workflows" / f"{name}.yml").read_text(encoding="utf-8")
    assert "persist-credentials: false" in text


# ══════════════════════════════════════════════════════════════════════════
# Rollout safety for the new rules: an older document never blocks a save
# ══════════════════════════════════════════════════════════════════════════
def test_saving_one_monitor_writes_only_that_monitors_document(firestore_worker, monkeypatch):  # noqa: F811 - the imported fixture
    """A person with an older running monitor the new rules would refuse can
    still stop it, and still create, stop or extend any other: each action
    writes the one monitor document it changed, so the older one is never
    rewritten (and so never refused)."""
    from config import firestore as fs

    w = firestore_worker
    owner = "uid-friend-1"
    old = w.seed(_monitor(w.now, movie=_film(variants=_siblings(2)), dates=_dates(45)))      # 135 requests
    other = w.seed(_monitor(w.now, movie=_film("ET00512346")))
    written: list[str] = []
    real_commit = fs.FirestoreClient.commit

    def recording(self, writes):
        written.extend(f"{collection}/{doc}" for collection, doc, _ in writes if collection == "monitors")
        return real_commit(self, writes)

    monkeypatch.setattr(fs.FirestoreClient, "commit", recording)
    state_mod.set_scope_provider(lambda: Scope(PROJECT, owner, lambda: f"id.{owner}", email="w@example.com", admin=True))
    try:
        state_mod.stop_monitor(other.id, mirror=False)
        state_mod.extend_monitor(other.id, mirror=False)
        new = _monitor(w.now, movie=_film("ET00512347"), owner=owner)
        state_mod.upsert_monitor(new, mirror=False)
        assert written == [f"monitors/{other.id}", f"monitors/{other.id}", f"monitors/{new.id}"]
        state_mod.stop_monitor(old.id, mirror=False)               # and the old one can still be stopped
        assert written[-1] == f"monitors/{old.id}"
    finally:
        state_mod.set_scope_provider(None)
    assert w.docs["monitors"][old.id]["status"] == "STOPPED"
