"""Google sign-in through a popup: the callback run, the signal, the opener.

Two failures were found in production, in this order:

1. **The callback run overwrote its own state cookie.** The return from
   Google opens a brand-new Streamlit session. The gate minted a fresh
   ``state`` for it (as it does for any run with nobody in memory) and the
   bridge writes queued cookies *before* it reads the jar — so by the time
   ``handle_google_return`` compared ``?state=`` to the cookie, the cookie
   the link was made with was gone. Every sign-in ended in "didn't
   complete". Replayed here against the real ordering; it must never come
   back.

2. **The original tab could not pick the session up.** ``restore()`` runs
   once per session and reads only what the bridge last reported. So the
   popup now tells its opener — a UI signal, from our exact origin, carrying
   nothing — and the opener lets ``restore()`` run once more, against
   Google. The message is never proof of anything.

The browser side is pinned by reading the bridge's own source; the Python
side by the bare-mode session the restore tests use, and by AppTest for
the two runs the gate now draws differently.
"""

from __future__ import annotations

import re
import time
import types
from pathlib import Path

import pytest

from auth import firebase, google, session
from tests.test_auth import FakeFirebase, REAL_RESTORE
from tests.test_google_auth import APP_URL, GOOGLE_TOKEN, FakeGoogleTokens, _Rerun

BRIDGE = Path("auth/bridge/index.html").read_text(encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────────
# A browser: cookies shared by every tab, and a bridge that does what the
# real one does — apply the queued writes, then read the jar
# ──────────────────────────────────────────────────────────────────────────
class Browser:
    def __init__(self):
        self.cookies: dict[str, str] = {}

    def bridge(self, sess: dict, *, opener: bool = False, signals: int = 0) -> None:
        """One render of the component in the session ``sess``."""
        for op in sess.pop(session.BRIDGE_OPS_KEY, []):
            if int(op["max_age"]) > 0:
                self.cookies[op["name"]] = op["value"]
            else:
                self.cookies.pop(op["name"], None)
        sess[session.BRIDGE_JAR_KEY] = {**self.cookies, "seen": len(self.cookies),
                                        "opener": opener, "signals": signals}


@pytest.fixture
def world(monkeypatch):
    """Google + Firebase fakes, and a way to be any tab."""
    fb = FakeFirebase()
    fb.add("ravi@example.com", "Popcorn2026", uid="uid-ravi", name="Ravi Teja")
    fb.add_google_identity(GOOGLE_TOKEN, email="priya@gmail.com", name="Priya Rao", sub="1001")
    monkeypatch.setenv("FIREBASE_WEB_API_KEY", "test-web-api-key")
    monkeypatch.setattr(firebase, "_post", fb)
    tokens = FakeGoogleTokens()
    tokens.codes["code-1"] = GOOGLE_TOKEN
    monkeypatch.setattr(google, "_post_form", tokens)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "web-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "web-client-secret")
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", APP_URL)
    monkeypatch.setattr(session, "_cookie_header_from_runtime", lambda: "")
    browser = Browser()

    from ui import login

    class World:
        firebase = fb
        google = tokens
        cookies = browser.cookies

        @staticmethod
        def tab(params: dict | None = None) -> dict:
            """Become a new Streamlit session (a tab) at this URL."""
            sess: dict = {}
            q = dict(params or {})
            query = types.SimpleNamespace(to_dict=lambda: dict(q), clear=lambda: q.clear())
            ctx = types.SimpleNamespace(cookies={}, headers={}, url=APP_URL + "~/+/")
            monkeypatch.setattr(session, "st", types.SimpleNamespace(session_state=sess, context=ctx, query_params=query))
            monkeypatch.setattr(login, "st", types.SimpleNamespace(
                session_state=sess, context=ctx, query_params=query,
                rerun=lambda: (_ for _ in ()).throw(_Rerun())))
            sess["_params"] = q
            return sess

        @staticmethod
        def render_bridge(sess: dict, **kw) -> None:
            browser.bridge(sess, **kw)

        @staticmethod
        def callback_runs(sess: dict, *, opener: bool) -> bool:
            """The gate's sequence on a return from Google: run 1 (mint?,
            bridge), run 2 (return handler). True = signed in."""
            login.prepare_google()                     # run 1: what the gate does first
            browser.bridge(sess, opener=opener)        # …then the bridge renders
            try:
                login.handle_google_return()           # run 2
            except _Rerun:
                pass
            return session.USER_KEY in sess

    return World


def _login_page_minted(world) -> tuple[dict, str]:
    """Tab A shows the login page: the gate mints the state, the bridge
    writes it, the link is drawn with it."""
    from ui import login

    a = world.tab()
    login.prepare_google()
    world.render_bridge(a)
    state = session.oauth_state()
    assert world.cookies[session.OAUTH_STATE_COOKIE] == state
    return a, state


# ──────────────────────────────────────────────────────────────────────────
# 1. The root cause, replayed
# ──────────────────────────────────────────────────────────────────────────
def test_the_callback_run_leaves_the_state_cookie_alone(world):
    """Run 1 of the return must not mint: the bridge would write the fresh
    state over the one the link was made with, before run 2 compares."""
    from ui import login

    _, state = _login_page_minted(world)
    b = world.tab({"code": "code-1", "state": state})

    login.prepare_google()
    assert session.BRIDGE_OPS_KEY not in b or b[session.BRIDGE_OPS_KEY] == []
    assert session.OAUTH_STATE_KEY not in b
    world.render_bridge(b)
    assert world.cookies[session.OAUTH_STATE_COOKIE] == state              # untouched


@pytest.mark.parametrize("params", [{"code": "x", "state": "y"}, {"error": "access_denied"}, {"state": "y"}])
def test_nothing_is_minted_on_any_return(world, params):
    from ui import login

    b = world.tab(params)
    login.prepare_google()
    assert session.OAUTH_STATE_KEY not in b and not b.get(session.BRIDGE_OPS_KEY)


def test_the_full_return_signs_in_with_the_gates_real_ordering(world):
    """Before the fix this replay ended in 'state mismatch — code not
    exchanged' with Firebase never called. Now it signs in."""
    _, state = _login_page_minted(world)
    b = world.tab({"code": "code-1", "state": state})

    assert world.callback_runs(b, opener=False) is True
    assert b[session.USER_KEY].uid == "uid-google-1001"
    assert [a for a, _ in world.firebase.calls] == ["signInWithIdp", "lookup"]
    assert "auth_error" not in b
    # The state is spent: its clear is queued, and lands on the next render.
    world.render_bridge(b)
    assert session.OAUTH_STATE_COOKIE not in world.cookies


def test_a_failed_return_reruns_so_the_next_link_and_cookie_agree(world):
    """After a refusal the run is spent: the handler reruns, and only the
    next run mints — so the bridge writes the new state before the link
    that carries it is drawn. Today a retry would have been one cookie
    behind."""
    from ui import login

    _, state = _login_page_minted(world)
    b = world.tab({"code": "code-1", "state": "not-" + state})
    login.prepare_google()
    world.render_bridge(b)
    with pytest.raises(_Rerun):
        login.handle_google_return()
    assert b["auth_error"] == google.MESSAGES["state"]
    assert b["_params"] == {}                                                # spent
    # The next run: mints, and the bridge writes it before the page draws.
    login.prepare_google()
    fresh = session.oauth_state()
    world.render_bridge(b)
    assert fresh != state and world.cookies[session.OAUTH_STATE_COOKIE] == fresh


# ──────────────────────────────────────────────────────────────────────────
# 2. The popup: signal only after success, close only after signal
# ──────────────────────────────────────────────────────────────────────────
def test_a_popup_that_signed_in_is_marked_done_not_entered(world):
    _, state = _login_page_minted(world)
    popup = world.tab({"code": "code-1", "state": state})

    assert world.callback_runs(popup, opener=True) is True
    assert popup[session.POPUP_DONE_KEY] is True
    assert session.bridge_has_opener() is True


def test_a_plain_tab_that_signed_in_enters_the_app(world):
    """No opener (the popup was blocked and the link opened a tab): that
    tab is now the session, exactly as before."""
    _, state = _login_page_minted(world)
    tab = world.tab({"code": "code-1", "state": state})
    assert world.callback_runs(tab, opener=False) is True
    assert session.POPUP_DONE_KEY not in tab


@pytest.mark.parametrize("bad", [
    {"code": "code-1", "state": "wrong"},
    {"error": "access_denied"},
    {"code": "expired-code", "state": None},
])
def test_a_popup_that_failed_is_never_marked_done(world, bad):
    _, state = _login_page_minted(world)
    if bad.get("state") is None:
        bad["state"] = state
    popup = world.tab(bad)
    assert world.callback_runs(popup, opener=True) is False
    assert session.POPUP_DONE_KEY not in popup
    assert session.USER_KEY not in popup


def test_the_done_flag_and_signal_count_survive_the_sign_in_reset():
    """sign_in_user asks for a session reset on the next run, which runs
    before the gate looks at anything. The popup's 'done' must survive it,
    and so must the opener's count of signals acted on — or a sign-out
    would let a stale signal reopen the door."""
    assert session.POPUP_DONE_KEY in session.RESET_KEEPS
    assert session.SIGNALS_SEEN_KEY in session.RESET_KEEPS


def test_the_signal_is_handed_to_the_bridge_only_for_a_done_popup(monkeypatch):
    seen = {}

    def fake_component(**kw):
        seen.update(kw)
        return None

    monkeypatch.setattr(session, "_bridge_component", lambda: fake_component)
    monkeypatch.setattr(session, "st", types.SimpleNamespace(session_state={}))
    session.run_bridge()
    assert seen["signal"] == ""
    session.run_bridge(signal=session.GOOGLE_SIGNAL)
    assert seen["signal"] == session.GOOGLE_SIGNAL
    assert seen["nonce"] != 0                     # a signal is always a fresh render
    assert set(seen) == {"ops", "names", "signal", "nonce", "key", "default"}
    assert "client_secret" not in str(seen) and "id_token" not in str(seen)


# ──────────────────────────────────────────────────────────────────────────
# 3. The opener: the signal means "look again", and restore() decides
# ──────────────────────────────────────────────────────────────────────────
def test_the_opener_restores_from_the_cookie_the_popup_wrote(world):
    """The actual restoration path, end to end: the popup signed in and its
    bridge wrote tr_session; the opener's bridge heard the signal and
    re-reported the jar; the opener's gate lets restore() run once more,
    and restore() asks Google — token exchange, then the record."""
    a, state = _login_page_minted(world)
    a["auth_restore_tried"] = True                  # tab A already tried, at open

    popup = world.tab({"code": "code-1", "state": state})
    assert world.callback_runs(popup, opener=True)
    session.flush_cookie()                          # the done run: the gate queues the session cookie…
    world.render_bridge(popup, opener=True)         # …the bridge writes it, then signals
    assert world.cookies[session.COOKIE] == world.firebase.refresh_token_for("uid-google-1001")

    # Tab A again: the bridge heard the signal and re-read the jar.
    world.tab()
    session.st.session_state.update(a)
    world.render_bridge(session.st.session_state, signals=1)
    sess = session.st.session_state
    assert session.USER_KEY not in sess
    assert sess["auth_restore_tried"] is True

    assert session.google_signal_pending() is True
    session.allow_restore_again()
    world.firebase.calls.clear()
    user = REAL_RESTORE()

    assert user is not None and user.uid == "uid-google-1001"
    assert [c for c, _ in world.firebase.calls] == ["token", "lookup"]    # Google decided, not the message
    assert sess["auth_restore_tried"] is True


def test_a_signal_is_consumed_once_and_never_after_a_sign_out(world):
    sess = world.tab()
    world.render_bridge(sess, signals=1)
    assert session.google_signal_pending() is True
    assert session.google_signal_pending() is False                        # same signal, once
    world.render_bridge(sess, signals=2)
    assert session.google_signal_pending() is True                         # a new one counts
    # Sign-out keeps the count (RESET_KEEPS), so the stale value cannot
    # re-open restore() and undo the sign-out.
    session.sign_out()
    assert sess[session.SIGNALS_SEEN_KEY] == 2
    assert session.google_signal_pending() is False


def test_no_signal_means_no_second_restore(world):
    sess = world.tab()
    sess["auth_restore_tried"] = True
    world.render_bridge(sess, signals=0)
    assert session.google_signal_pending() is False
    assert sess["auth_restore_tried"] is True


@pytest.mark.parametrize("value", [{"signals": "junk"}, {"signals": None}, {}, {"opener": "true"}])
def test_garbage_from_the_bridge_is_ignored(world, value):
    sess = world.tab()
    sess[session.BRIDGE_JAR_KEY] = {"seen": 0, **value}
    assert session.google_signal_pending() is False
    assert session.bridge_has_opener() is False


# ──────────────────────────────────────────────────────────────────────────
# 4. The gate, under AppTest: the popup's last paint, and the opener's pickup
# ──────────────────────────────────────────────────────────────────────────
def test_a_done_popup_draws_the_closing_line_and_signals_not_the_app(monkeypatch, signed_in):
    from tests.test_auth import body, in_the_app, run

    calls = []
    monkeypatch.setattr(session, "run_bridge", lambda **kw: calls.append(kw) or None)
    # The done run: the popup's previous run signed in, so the user is in
    # memory when the gate looks, and the flag is set.
    app = run(**{session.USER_KEY: signed_in, session.POPUP_DONE_KEY: True})
    assert not app.exception
    assert calls and calls[-1].get("signal") == session.GOOGLE_SIGNAL
    assert "SIGNED IN" in body(app) and "CLOSE THIS WINDOW" in body(app)
    assert not in_the_app(app)                       # no sidebar, no wizard, no data reads


def test_the_signal_is_only_sent_when_somebody_is_signed_in(monkeypatch):
    from tests.test_auth import on_login_page, run

    calls = []
    monkeypatch.setattr(session, "run_bridge", lambda **kw: calls.append(kw) or None)
    monkeypatch.setattr(session, "restore", lambda: None)
    monkeypatch.setattr(session, "BRIDGE_ENABLED", False)
    app = run(**{session.POPUP_DONE_KEY: True})      # the flag alone proves nothing
    assert on_login_page(app)
    assert all(c.get("signal", "") == "" for c in calls)


def test_the_opener_tab_enters_the_app_on_the_signal_without_a_reload(monkeypatch, signed_in):
    """Tab A: restore already tried once (nobody), then the popup's signal
    arrives. The gate lets restore run again, and this time there is a
    session to restore. No reload, no click."""
    from tests.test_auth import in_the_app, on_login_page, run

    monkeypatch.setattr(session, "BRIDGE_ENABLED", False)
    # The fixture's restore honours the once-per-session guard like the real one.
    app = run(auth_restore_tried=True, **{session.BRIDGE_JAR_KEY: {"seen": 1, "signals": 0}})
    assert on_login_page(app)

    app = run(auth_restore_tried=True, **{session.BRIDGE_JAR_KEY: {"seen": 1, "signals": 1}})
    assert not app.exception
    assert in_the_app(app)
    assert app.session_state[session.SIGNALS_SEEN_KEY] == 1


# ──────────────────────────────────────────────────────────────────────────
# 5. The bridge's own source: origin, payload, popup, close
# ──────────────────────────────────────────────────────────────────────────
def _js(name: str) -> str:
    m = re.search(rf"function {name}\((.*?)\n        }}\n", BRIDGE, re.S)
    assert m, name
    return m.group(0)


def test_the_completion_message_goes_to_our_exact_origin_only():
    js = _js("signalAndClose")
    assert 'postMessage({ type: SIGNAL_TYPE }, origin())' in js
    assert '"*"' not in js                          # never a wildcard target
    assert 'return window.location.origin;' in _js("origin")


def test_the_completion_message_carries_nothing_but_its_type():
    js = _js("signalAndClose")
    payloads = re.findall(r"postMessage\((\{.*?\}),", js)
    assert payloads == ["{ type: SIGNAL_TYPE }"]
    for word in ("token", "cookie", "jar", "code", "state"):
        assert word not in js.lower().replace("signal_type", "")


def test_the_opener_ignores_the_wrong_origin_and_the_wrong_type():
    js = _js("installSignalListener")
    assert "if (e.origin !== origin()) return;" in js
    assert "d.type !== SIGNAL_TYPE) return;" in js
    assert "heard += 1;" in js and "report(lastNames, lastNonce, true);" in js
    assert "restore" not in js and "cookie" not in js   # it reports; Python decides


def test_the_popup_closes_only_after_python_said_it_signed_in():
    assert "if (args.signal === SIGNAL_TYPE) signalAndClose();" in BRIDGE
    js = _js("signalAndClose")
    assert "if (signalled) return;" in js and "top.close()" in js
    assert BRIDGE.count(".close()") == 1                 # nowhere else


def test_the_popup_is_opened_from_the_persons_click_and_falls_back_to_the_link():
    js = _js("installClickHandler")
    assert 'closest(GOOGLE_LINK)' in js
    assert 'href.indexOf("https://accounts.google.com/") !== 0) return;' in js
    assert "if (openPopup(href)) {" in js and "e.preventDefault();" in js
    opener = _js("openPopup")
    assert "window.parent.open(href, POPUP_NAME, POPUP_FEATURES)" in opener
    assert 'var POPUP_NAME = "tr_google_auth";' in BRIDGE  # one named window, never a second
    assert "setInterval" not in BRIDGE and "setTimeout" not in BRIDGE   # no polling


def test_the_bridge_reports_opener_and_signals_alongside_the_jar():
    js = _js("report")
    assert "opener: hasOpener(), signals: heard" in js
    assert 'value[names[n]] = jar[names[n]] || "";' in js


def test_the_page_uses_the_same_signal_name_as_the_bridge():
    assert f'var SIGNAL_TYPE = "{session.GOOGLE_SIGNAL}";' in BRIDGE
