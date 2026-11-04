"""The wait for the browser ends, and it never ends in somebody being let in.

On Community Cloud the session cookie only reaches Python through a hidden
component. Its frontend is one HTTP request, and when that request failed the
gate waited for a rerun that nothing was going to cause: ``Restoring your
session…`` for as long as the tab stayed open, under Streamlit's own "having
trouble loading the component" warning.

These run the real gate, the real ``restore()`` and the suite's fake Firebase,
with the component replaced by something a test controls, and the watchdog's
clock moved by hand. ``tests/test_auth_bridge_browser.py`` does the same against
a real browser and a real failed request.
"""

from __future__ import annotations

import time
import types
from html import unescape
from pathlib import Path

import pytest

from auth import firebase, gate, session
from tests.test_auth import (
    REAL_RESTORE,
    FakeFirebase,
    bare_session,  # noqa: F401 - a fixture
    in_the_app,
    on_login_page,
    run,
)
from ui import restoring

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

RECOVERY_AT = sum(session.BRIDGE_MOUNT_SECONDS[: session.BRIDGE_RECOVERY_AFTER])
SCHEDULE_ENDS_AT = sum(session.BRIDGE_MOUNT_SECONDS)


class Frame:
    """The component as the gate sees it: whatever the browser has said so far."""

    def __init__(self):
        self.value = None
        self.error: Exception | None = None
        self.renders: list[dict] = []

    def __call__(self, **kwargs):
        self.renders.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.value

    @property
    def keys(self) -> list[str]:
        """Each distinct frame, in the order they were mounted."""
        return list(dict.fromkeys(r["key"] for r in self.renders))


class World:
    def __init__(self, fb: FakeFirebase, frame: Frame):
        self.fb = fb
        self.frame = frame
        self.now = 1_000_000.0

    def answer(self, *, uid: str = "uid-ravi", **extra) -> None:
        """The browser reports a jar holding this account's session cookie."""
        self.frame.value = {
            "seen": 2, "nonce": 0, "opener": False, "signals": 0,
            session.COOKIE: self.fb.refresh_token_for(uid),
            session.SESSION_STARTED_COOKIE: str(time.time() - 3600),
            session.OAUTH_STATE_COOKIE: "",
            **extra,
        }

    def answer_empty(self) -> None:
        self.frame.value = {"seen": 0, "nonce": 0, "opener": False, "signals": 0,
                            session.COOKIE: "", session.SESSION_STARTED_COOKIE: "",
                            session.OAUTH_STATE_COOKIE: ""}

    def calls(self, action: str) -> int:
        return sum(1 for name, _ in self.fb.calls if name == action)

    @property
    def firebase_calls(self) -> int:
        return len(self.fb.calls)


@pytest.fixture
def world(monkeypatch):
    fb = FakeFirebase()
    fb.add("ravi@example.com", "Popcorn2026", uid="uid-ravi", name="Ravi Teja")
    fb.add("ada@example.com", "Popcorn2026", uid="uid-admin", name="Ada Admin")
    fb.set_claims("ada@example.com", {"admin": True})
    fb.add("newbie@example.com", "Trailer2026", uid="uid-newbie", verified=False)
    monkeypatch.setenv("FIREBASE_WEB_API_KEY", "test-web-api-key")
    monkeypatch.setattr(firebase, "_post", fb)
    monkeypatch.setattr(session, "restore", REAL_RESTORE)
    monkeypatch.setattr(session, "BRIDGE_ENABLED", True)
    frame = Frame()
    monkeypatch.setattr(session, "_bridge_component", lambda: frame)
    w = World(fb, frame)
    monkeypatch.setattr(session, "_now", lambda: w.now)
    return w


def text(app) -> str:
    return unescape(" ".join(m.value for m in app.markdown))


def waiting(app) -> bool:
    return restoring.STATUS in text(app)


def recovering(app) -> bool:
    return restoring.RECOVERY_TITLE in text(app)


def nobody_is_signed_in(app) -> bool:
    return app.session_state[session.USER_KEY] is None if session.USER_KEY in app.session_state else True


def tick_past_deadline(world: World, app):
    """What the watchdog's fragment asks for when a deadline has passed."""
    world.now += session.BRIDGE_MOUNT_SECONDS[min(
        int(app.session_state[session.BRIDGE_STEP_KEY]), len(session.BRIDGE_MOUNT_SECONDS) - 1)] + 0.1
    return app.run()


def to_recovery(world: World, app):
    for _ in range(session.BRIDGE_RECOVERY_AFTER):
        app = tick_past_deadline(world, app)
    assert recovering(app)
    return app


# ──────────────────────────────────────────────────────────────────────────
# The healthy path
# ──────────────────────────────────────────────────────────────────────────
def test_a_frame_that_answers_restores_the_session_without_a_retry(world):
    app = run()
    assert waiting(app) and world.firebase_calls == 0
    world.answer()
    app = app.run()
    assert in_the_app(app) and not waiting(app)
    assert world.frame.keys == ["tr_auth_bridge"], "a healthy frame is never replaced"
    assert app.session_state[session.USER_KEY].uid == "uid-ravi"


def test_a_slow_answer_inside_the_window_changes_nothing(world):
    app = run()
    world.now += session.BRIDGE_MOUNT_SECONDS[0] - 1
    app = app.run()
    assert waiting(app) and world.frame.keys == ["tr_auth_bridge"]
    world.answer()
    app = app.run()
    assert in_the_app(app) and world.frame.keys == ["tr_auth_bridge"]


def test_once_answered_the_frame_is_left_alone_however_long_the_session_lasts(world):
    app = run()
    world.answer()
    app = app.run()
    world.now += 10 * SCHEDULE_ENDS_AT
    app = app.run()
    assert in_the_app(app) and world.frame.keys == ["tr_auth_bridge"]


# ──────────────────────────────────────────────────────────────────────────
# A frame that fails, or never says anything
# ──────────────────────────────────────────────────────────────────────────
def test_a_component_that_raises_is_a_wait_not_a_crash(world):
    world.frame.error = RuntimeError("component registry unavailable")
    app = run()
    assert not app.exception and waiting(app)
    assert nobody_is_signed_in(app) and world.firebase_calls == 0


def test_a_failing_component_still_reaches_the_recovery_screen(world):
    world.frame.error = RuntimeError("no")
    app = to_recovery(world, run())
    assert not app.exception and nobody_is_signed_in(app) and world.firebase_calls == 0


@pytest.mark.parametrize("junk", ["tr_session=forged", 7, True, ["tr_session"], ""])
def test_anything_but_the_bridges_own_shape_is_no_answer(world, junk):
    world.frame.value = junk
    app = run()
    assert waiting(app) and session.BRIDGE_JAR_KEY not in app.session_state
    assert nobody_is_signed_in(app) and world.firebase_calls == 0


def test_a_missing_response_is_never_a_session_at_any_point_in_the_schedule(world):
    app = run()
    for _ in range(len(session.BRIDGE_MOUNT_SECONDS) + 2):
        assert nobody_is_signed_in(app)
        assert not in_the_app(app) and not on_login_page(app)
        app = tick_past_deadline(world, app)
    assert world.firebase_calls == 0


# ──────────────────────────────────────────────────────────────────────────
# The timeout
# ──────────────────────────────────────────────────────────────────────────
def test_each_deadline_mounts_a_new_frame(world):
    app = run()
    assert world.frame.keys == ["tr_auth_bridge"]
    app = tick_past_deadline(world, app)
    assert world.frame.keys == ["tr_auth_bridge", "tr_auth_bridge_1"]
    app = tick_past_deadline(world, app)
    assert world.frame.keys[-1] == "tr_auth_bridge_2"


def test_no_new_frame_before_the_deadline(world):
    app = run()
    for _ in range(5):
        world.now += (session.BRIDGE_MOUNT_SECONDS[0] - 1) / 5
        app = app.run()
    assert world.frame.keys == ["tr_auth_bridge"]


def test_the_wait_becomes_the_recovery_screen_within_a_bounded_time(world):
    app = run()
    assert waiting(app) and not recovering(app)
    for step in range(session.BRIDGE_RECOVERY_AFTER):
        assert not recovering(app), f"too early at step {step}"
        app = tick_past_deadline(world, app)
    assert recovering(app) and not waiting(app)
    assert RECOVERY_AT < 60, "before Streamlit's own warning would have appeared"


def test_the_schedule_ends_and_the_page_stops_asking(world, monkeypatch):
    ticks = []
    monkeypatch.setattr(gate, "_bridge_watchdog", lambda: ticks.append(1))
    app = run()
    for _ in range(len(session.BRIDGE_MOUNT_SECONDS) + 5):
        app = tick_past_deadline(world, app)
    drawn_while_pending = len(ticks)
    frames = len(world.frame.keys)
    assert frames == len(session.BRIDGE_MOUNT_SECONDS), "one frame per window, then no more"
    assert drawn_while_pending <= len(session.BRIDGE_MOUNT_SECONDS)
    for _ in range(5):
        world.now += 1000
        app = app.run()
    assert len(ticks) == drawn_while_pending, "no timer left once the schedule is finished"
    assert len(world.frame.keys) == frames
    assert recovering(app) and nobody_is_signed_in(app)


def test_the_screen_stops_promising_retries_when_there_are_none(world):
    app = to_recovery(world, run())
    assert restoring.RECOVERY_RETRYING in text(app)
    for _ in range(len(session.BRIDGE_MOUNT_SECONDS)):
        app = tick_past_deadline(world, app)
    assert recovering(app) and restoring.RECOVERY_RETRYING not in text(app)


def test_the_watchdog_only_reruns_when_a_deadline_has_passed(bare_session, monkeypatch):  # noqa: F811
    reruns = []
    monkeypatch.setattr(gate, "st", types.SimpleNamespace(rerun=lambda scope: reruns.append(scope)))
    clock = {"now": 100.0}
    monkeypatch.setattr(session, "_now", lambda: clock["now"])
    monkeypatch.setattr(session, "BRIDGE_ENABLED", True)
    session.watch_bridge()
    watchdog = gate._bridge_watchdog.__wrapped__
    watchdog()
    assert reruns == []
    clock["now"] += session.BRIDGE_MOUNT_SECONDS[0] + 0.01
    watchdog()
    assert reruns == ["app"], "a whole run of the gate, not just the fragment"


# ──────────────────────────────────────────────────────────────────────────
# Recovering
# ──────────────────────────────────────────────────────────────────────────
def test_a_transient_failure_recovers_on_the_next_frame_with_no_action(world):
    world.frame.error = RuntimeError("503 from the proxy")
    app = run()
    app = tick_past_deadline(world, app)
    world.frame.error = None
    world.answer()
    app = app.run()
    assert in_the_app(app)
    assert world.calls("token") == 1, "one exchange with Google"
    assert world.frame.keys == ["tr_auth_bridge", "tr_auth_bridge_1"]


def test_a_late_answer_on_the_recovery_screen_restores_by_itself(world):
    app = to_recovery(world, run())
    world.answer()
    app = app.run()
    assert in_the_app(app) and not recovering(app)


def test_a_very_late_answer_after_the_schedule_still_restores(world):
    app = run()
    for _ in range(len(session.BRIDGE_MOUNT_SECONDS) + 1):
        app = tick_past_deadline(world, app)
    last = world.frame.keys[-1]
    world.answer()
    app = app.run()
    assert in_the_app(app)
    assert world.frame.keys[-1] == last, "the frame that answered is the one left mounted"


def test_try_again_mounts_a_new_frame_and_starts_the_schedule_over(world):
    app = to_recovery(world, run())
    frames = len(world.frame.keys)
    app = app.button(key="tr_boot_retry").click().run()
    assert len(world.frame.keys) == frames + 1
    assert waiting(app) and not recovering(app)
    assert int(app.session_state[session.BRIDGE_STEP_KEY]) == 0


def test_try_again_then_an_answer_restores_exactly_once(world):
    app = to_recovery(world, run())
    app = app.button(key="tr_boot_retry").click().run()
    world.answer()
    app = app.run()
    assert in_the_app(app)
    assert world.calls("token") == 1 and world.calls("lookup") == 1


def test_repeated_failure_comes_back_to_recovery_and_stays_closed(world):
    app = run()
    for _ in range(4):
        app = to_recovery(world, app)
        assert nobody_is_signed_in(app) and world.firebase_calls == 0
        app = app.button(key="tr_boot_retry").click().run()
    assert waiting(app) and nobody_is_signed_in(app) and world.firebase_calls == 0


def test_nothing_on_the_recovery_path_reloads_the_page():
    """A loop of reloads is the failure this must not trade the old one for."""
    for name in ("auth/gate.py", "auth/session.py", "ui/restoring.py", "auth/bridge/index.html"):
        src = Path(name).read_text(encoding="utf-8")
        for banned in ("location.reload", "location.href", "location.replace", "history.go", "http-equiv"):
            assert banned not in src, f"{name} must not use {banned}"


def test_go_to_sign_in_shows_the_login_page_and_signs_nobody_in(world):
    app = to_recovery(world, run())
    app = app.button(key="tr_boot_sign_in").click().run()
    assert on_login_page(app) and not in_the_app(app) and not recovering(app)
    assert nobody_is_signed_in(app) and world.firebase_calls == 0


def test_going_to_sign_in_stops_the_timer_and_the_remounting(world, monkeypatch):
    app = to_recovery(world, run())
    app = app.button(key="tr_boot_sign_in").click().run()
    frames = len(world.frame.keys)
    ticks = []
    monkeypatch.setattr(gate, "_bridge_watchdog", lambda: ticks.append(1))
    for _ in range(3):
        world.now += 1000
        app = app.run()
    assert len(world.frame.keys) == frames and ticks == []
    assert on_login_page(app)


def test_a_late_answer_on_the_login_page_still_restores(world):
    app = to_recovery(world, run())
    app = app.button(key="tr_boot_sign_in").click().run()
    world.answer()
    app = app.run()
    assert in_the_app(app) and world.calls("token") == 1


# ──────────────────────────────────────────────────────────────────────────
# What the recovery screen is
# ──────────────────────────────────────────────────────────────────────────
def test_the_recovery_screen_offers_a_retry_and_says_how_to_reload(world):
    app = to_recovery(world, run())
    labels = [b.label for b in app.button]
    assert restoring.RECOVERY_RETRY in labels and restoring.RECOVERY_SIGN_IN in labels
    page = text(app)
    assert "reload this page" in page
    assert "haven't been signed in" in page


def test_the_recovery_screen_has_nothing_of_the_app_or_of_anybody_in_it(world):
    app = to_recovery(world, run())
    page = text(app)
    assert not in_the_app(app) and "Movie Ticket Monitor" not in page
    assert "SIGN IN" not in page and "Welcome back" not in page
    assert 'class="tr-logo"' in page
    assert "ravi@example.com" not in page


def test_streamlits_own_warning_is_hidden_only_in_the_bridges_slot():
    theme = Path("ui/theme.py").read_text(encoding="utf-8")
    assert '[class*="st-key-tr_chrome"] [data-testid="stAlert"] { display:none !important; }' in theme
    # The app's own alerts keep their styling everywhere else.
    assert '[data-testid="stAlert"] { border-radius:12px;' in theme


# ──────────────────────────────────────────────────────────────────────────
# Sessions: the answer decides nothing by itself
# ──────────────────────────────────────────────────────────────────────────
def test_a_valid_session_is_restored_from_googles_answer(world):
    app = run()
    world.answer()
    app = app.run()
    user = app.session_state[session.USER_KEY]
    assert user.uid == "uid-ravi" and user.email == "ravi@example.com"
    assert world.calls("token") == 1 and world.calls("lookup") == 1


def test_an_unauthenticated_browser_goes_straight_to_login(world):
    world.answer_empty()
    app = run()
    assert on_login_page(app) and not in_the_app(app)
    assert nobody_is_signed_in(app) and world.firebase_calls == 0


def test_an_invalid_token_is_login_and_the_cookie_goes(world):
    world.frame.value = {"seen": 1, session.COOKIE: "refresh.not-a-real-token",
                         session.SESSION_STARTED_COOKIE: str(time.time() - 3600)}
    app = run()
    assert on_login_page(app) and nobody_is_signed_in(app)
    assert world.calls("token") == 1
    app = app.run()
    cleared = [op for r in world.frame.renders for op in r["ops"]
               if op["name"] == session.COOKIE and op["max_age"] == 0]
    assert cleared, "a refused cookie is removed from the browser"


def test_a_session_past_the_seven_day_deadline_is_login_without_asking_google(world):
    world.answer()
    world.frame.value[session.SESSION_STARTED_COOKIE] = str(time.time() - session.SESSION_SECONDS - 60)
    app = run()
    assert on_login_page(app) and nobody_is_signed_in(app)
    assert world.firebase_calls == 0


def test_an_unverified_account_is_not_restored(world):
    app = run()
    world.answer(uid="uid-newbie")
    app = app.run()
    assert on_login_page(app) and nobody_is_signed_in(app)


def test_a_cookie_is_worthless_when_firebase_cannot_vouch_for_it(world, monkeypatch):
    monkeypatch.delenv("FIREBASE_WEB_API_KEY", raising=False)
    app = run()
    world.answer()
    app = app.run()
    assert not in_the_app(app) and nobody_is_signed_in(app)


def test_a_refusal_from_google_after_recovery_is_still_a_refusal(world):
    app = to_recovery(world, run())
    world.frame.value = {"seen": 1, session.COOKIE: "refresh.forged"}
    app = app.run()
    assert on_login_page(app) and not in_the_app(app) and nobody_is_signed_in(app)


# ──────────────────────────────────────────────────────────────────────────
# Admin: Firebase's record, only
# ──────────────────────────────────────────────────────────────────────────
def test_the_admin_claim_comes_from_firebases_record(world):
    app = run()
    world.answer(uid="uid-admin")
    app = app.run()
    assert in_the_app(app)
    assert app.session_state[session.USER_KEY].admin is True


def test_a_normal_member_is_not_an_admin(world):
    app = run()
    world.answer()
    app = app.run()
    assert in_the_app(app)
    assert app.session_state[session.USER_KEY].admin is False


def test_a_browser_cannot_claim_admin_or_another_uid(world):
    app = run()
    world.answer(admin="true", uid_override="uid-admin", tr_admin="1", role="admin", is_admin=True)
    app = app.run()
    user = app.session_state[session.USER_KEY]
    assert user.uid == "uid-ravi" and user.admin is False


def test_recovering_does_not_change_who_is_an_admin(world):
    app = to_recovery(world, run())
    world.answer()
    app = app.run()
    assert app.session_state[session.USER_KEY].admin is False


# ──────────────────────────────────────────────────────────────────────────
# No second session
# ──────────────────────────────────────────────────────────────────────────
def test_many_frames_and_answers_are_one_exchange_with_google(world):
    app = run()
    app = tick_past_deadline(world, app)
    app = tick_past_deadline(world, app)
    world.answer()
    for _ in range(4):
        app = app.run()
    assert in_the_app(app)
    assert world.calls("token") == 1 and world.calls("lookup") == 1


def test_a_refused_restore_is_not_retried_by_the_watchdog(world):
    world.frame.value = {"seen": 1, session.COOKIE: "refresh.forged"}
    app = run()
    token_calls = world.calls("token")
    assert token_calls == 1
    for _ in range(4):
        world.now += 1000
        app = app.run()
    assert world.calls("token") == token_calls


def test_try_again_does_not_earn_another_exchange(bare_session, monkeypatch):  # noqa: F811
    monkeypatch.setattr(session, "_now", lambda: 1.0)
    bare_session["auth_restore_tried"] = True
    session.retry_bridge()
    assert bare_session["auth_restore_tried"] is True


def test_signing_out_does_not_come_back_through_a_late_answer(world):
    app = run()
    world.answer()
    app = app.run()
    assert in_the_app(app)
    token_calls = world.calls("token")
    # What ``session.sign_out`` leaves behind, with the browser still reporting
    # the cookie it held a moment ago.
    del app.session_state[session.USER_KEY]
    app.session_state[session.RESET_FLAG] = True
    app.session_state["auth_restore_tried"] = True
    app.session_state["auth_cookie_clear"] = True
    app = app.run()
    app = app.run()
    assert not in_the_app(app) and world.calls("token") == token_calls


def test_a_session_already_in_memory_never_starts_the_watchdog(world):
    app = run()
    world.answer()
    app = app.run()
    assert in_the_app(app)
    world.frame.value = None      # the frame dies afterwards
    world.now += 10 * SCHEDULE_ENDS_AT
    frames = len(world.frame.keys)
    app = app.run()
    assert in_the_app(app) and len(world.frame.keys) == frames


# ──────────────────────────────────────────────────────────────────────────
# What a retry must not lose, or leak
# ──────────────────────────────────────────────────────────────────────────
def test_a_queued_cookie_write_goes_to_the_replacement_frame_too(world):
    """A frame that never loaded never wrote anything. Lose this and a Google
    sign-in started after a retry fails on its ``state`` cookie."""
    app = run()
    app.session_state[session.BRIDGE_OPS_KEY] = [{"name": session.OAUTH_STATE_COOKIE, "value": "state-1", "max_age": 600}]
    app = app.run()
    assert any(op["value"] == "state-1" for op in world.frame.renders[-1]["ops"])
    app = tick_past_deadline(world, app)
    assert world.frame.renders[-1]["key"] == "tr_auth_bridge_1"
    assert any(op["value"] == "state-1" for op in world.frame.renders[-1]["ops"])


def test_writes_stop_being_resent_once_a_frame_has_answered(world):
    app = run()
    app.session_state[session.BRIDGE_OPS_KEY] = [{"name": session.OAUTH_STATE_COOKIE, "value": "state-1", "max_age": 600}]
    app = app.run()
    world.answer_empty()
    app = app.run()
    assert session.BRIDGE_UNACKED_KEY not in app.session_state
    app = app.run()
    assert all(op["value"] != "state-1" for op in world.frame.renders[-1]["ops"])


def test_unacknowledged_writes_do_not_survive_a_session_reset():
    """They can hold a refresh token, and the next person is not its owner."""
    assert session.BRIDGE_UNACKED_KEY not in session.RESET_KEEPS


def test_retrying_never_prints_a_token(world, capsys):
    app = to_recovery(world, run())
    world.answer()
    app = app.run()
    assert in_the_app(app)
    out = capsys.readouterr()
    logged = out.out + out.err
    assert "[auth] bridge:" in logged, "the watchdog leaves a trail"
    assert world.fb.refresh_token_for("uid-ravi") not in logged
    assert "id.uid-ravi" not in logged
    assert "ravi@example.com" not in logged


def test_the_diagnostics_are_numbers_and_booleans(bare_session):  # noqa: F811
    bare_session[session.BRIDGE_MOUNT_KEY] = 2
    bare_session[session.BRIDGE_JAR_KEY] = {session.COOKIE: "refresh.secret"}
    report = session.cookie_report()
    assert report["bridge_mount"] == 2 and report["bridge_answered"] is True
    assert "refresh.secret" not in str(report)


def test_the_frame_needs_no_change_to_recover():
    """Recovery is on the server's clock, so the bridge keeps its no-timers rule
    and a frame that never ran is covered as well as one that went quiet."""
    html = Path("auth/bridge/index.html").read_text(encoding="utf-8")
    assert "setInterval" not in html and "setTimeout" not in html
