"""The auth bridge failing for real: a real browser, and a real failed request.

Runs ``tools/fake_firebase_app.py`` (the real ``app.py`` and gate, an in-memory
Firebase, and every server-side cookie reader blinded, as on Community Cloud)
and uses Playwright to break the one HTTP request the bridge depends on.

Off by default, because it needs a browser and takes minutes:

    TR_BROWSER_TESTS=1 python -m pytest tests/test_auth_bridge_browser.py

``TR_BROWSER_SERVER_PYTHON`` runs the server from another interpreter, to test
the Streamlit version production uses. ``TR_BROWSER_SHOTS`` is a directory to
save screenshots into.

This is a local simulation. It proves what the app does when that request fails;
it says nothing about why a request might fail on Community Cloud.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("TR_BROWSER_TESTS") != "1",
                                reason="browser tests: set TR_BROWSER_TESTS=1")

ROOT = Path(__file__).resolve().parent.parent
ROUTE = "**/component/auth.session.tr_auth_bridge/**"
#: Windows for the frames: recovery after the first three (7 s), done after 13 s.
SCHEDULE = "2,2,3,3,3"
FRAMES = len(SCHEDULE.split(","))
#: The schedule, plus a tick of lateness per window, plus a page load.
RECOVERY_BOUND = 7 + 3 * 2 + 8
VIEWPORTS = {
    "desktop": {"viewport": {"width": 1280, "height": 800}},
    "mobile": {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True,
               "device_scale_factor": 2},
}
RAVI = ("ravi@example.com", "Popcorn2026")
ADA = ("admin@example.com", "Popcorn2026")


class Server:
    def __init__(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}/"
        self.log_path = Path(tempfile.mkdtemp(prefix="tr-browser-")) / "server.log"
        self.log = open(self.log_path, "w", encoding="utf-8")
        python = os.environ.get("TR_BROWSER_SERVER_PYTHON") or sys.executable
        self.process = subprocess.Popen(
            [python, "-m", "streamlit", "run", "tools/fake_firebase_app.py",
             "--server.port", str(self.port), "--server.headless", "true",
             "--browser.gatherUsageStats", "false", "--server.fileWatcherType", "none"],
            cwd=ROOT, stdout=self.log, stderr=subprocess.STDOUT,
            env=dict(os.environ, TR_SIMULATE_CLOUD="1", TR_BRIDGE_SCHEDULE=SCHEDULE, PYTHONIOENCODING="utf-8"))
        for _ in range(120):
            try:
                urllib.request.urlopen(self.url + "_stcore/health", timeout=2)
                return
            except Exception:  # noqa: BLE001 - not up yet
                time.sleep(0.5)
        raise RuntimeError("the test server never came up")

    def text(self) -> str:
        self.log.flush()
        return self.log_path.read_text(encoding="utf-8", errors="replace")

    def mark(self) -> int:
        return len(self.text())

    def since(self, mark: int) -> str:
        return self.text()[mark:]

    def stop(self) -> None:
        self.process.terminate()
        self.log.close()


@pytest.fixture(scope="module")
def server():
    srv = Server()
    yield srv
    srv.stop()


@pytest.fixture(scope="module")
def chromium():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as play:
        browser = play.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture(params=list(VIEWPORTS))
def page(request, chromium):
    context = chromium.new_context(**VIEWPORTS[request.param])
    tab = context.new_page()
    tab.viewport_name = request.param
    yield tab
    context.close()


@pytest.fixture
def desktop(chromium):
    context = chromium.new_context(**VIEWPORTS["desktop"])
    tab = context.new_page()
    tab.viewport_name = "desktop"
    yield tab
    context.close()


def shot(tab, name: str) -> None:
    target = os.environ.get("TR_BROWSER_SHOTS")
    if target:
        tab.screenshot(path=str(Path(target) / f"{name}-{tab.viewport_name}.png"))


def in_app(tab) -> bool:
    return tab.locator(".tr-hero").count() > 0


def on_recovery(tab) -> bool:
    return tab.get_by_text("We couldn't restore your session").count() > 0


def on_beat(tab) -> bool:
    return tab.locator(".tr-boot:not(.tr-boot-recovery)").count() > 0


def on_login(tab) -> bool:
    return tab.locator("input[type=password]").count() > 0


def wait_for(tab, condition, seconds: float, what: str) -> float:
    started = time.time()
    while time.time() - started < seconds:
        if condition(tab):
            return time.time() - started
        tab.wait_for_timeout(200)
    raise AssertionError(f"never saw {what} in {seconds}s; page says: {tab.inner_text('body')[:300]!r}")


def sign_in(tab, server: Server, account=RAVI) -> None:
    tab.goto(server.url)
    wait_for(tab, on_login, 60, "the login page")
    tab.locator("input:not([type=password])").first.fill(account[0])
    tab.locator("input[type=password]").first.fill(account[1])
    tab.locator("input[type=password]").first.press("Enter")
    wait_for(tab, in_app, 30, "the app after signing in")
    tab.wait_for_function("document.cookie.includes('tr_session=')", timeout=30000)


def new_session(log: str) -> str:
    """The log from the reloaded session's first wait. The server log is shared,
    and the previous session's last rerun can land just after a mark."""
    return log[log.index("waiting for the browser bridge (run 1"):]


def count_exchanges(log: str) -> int:
    """Calls made to Google for a refresh: answered, or refused. An expired
    session is turned away by our own deadline before any call is made."""
    return len(re.findall(r"refresh_exchange=HTTP|FAILED reason=(?!session_deadline_passed)", log))


def visible_streamlit_warning(tab) -> bool:
    warning = tab.get_by_text("having trouble loading")
    return warning.count() > 0 and warning.first.is_visible()


def break_bridge(tab, how="abort") -> None:
    if how == "abort":
        tab.route(ROUTE, lambda route: route.abort())
    else:
        tab.route(ROUTE, lambda route: route.fulfill(status=503, body="Service Unavailable"))


# ──────────────────────────────────────────────────────────────────────────
# Healthy
# ──────────────────────────────────────────────────────────────────────────
def test_a_healthy_reload_restores_with_one_exchange_and_no_retry(page, server):
    sign_in(page, server)
    mark = server.mark()
    frames = []
    page.on("request", lambda r: frames.append(r.url) if "/component/" in r.url and r.resource_type == "document" else None)
    page.reload()
    wait_for(page, in_app, 20, "the restored app")
    log = server.since(mark)
    assert count_exchanges(log) == 1
    assert "mounting a new frame" not in log and "answered mount=0" in log
    assert len(frames) == 1 and not on_recovery(page)


def test_a_visitor_with_no_session_gets_the_login_page_and_no_firebase_call(page, server):
    mark = server.mark()
    page.goto(server.url)
    wait_for(page, on_login, 30, "the login page")
    log = server.since(mark)
    assert count_exchanges(log) == 0 and not in_app(page) and not on_recovery(page)


# ──────────────────────────────────────────────────────────────────────────
# The incident: the asset does not load
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("how", ["abort", "503"])
def test_a_dead_asset_ends_in_recovery_and_nobody_gets_in(page, server, how):
    sign_in(page, server)
    break_bridge(page, how)
    navigations = []
    page.on("framenavigated", lambda f: navigations.append(f.url) if f == page.main_frame else None)
    mark = server.mark()
    page.reload()
    wait_for(page, on_beat, 20, "the restoring beat")
    took = wait_for(page, on_recovery, RECOVERY_BOUND, "the recovery screen")
    shot(page, f"recovery-{how}")
    assert took < RECOVERY_BOUND
    assert not in_app(page) and not on_login(page)
    assert page.get_by_role("button", name="Try again").is_visible()
    assert page.get_by_role("button", name="Go to sign-in").is_visible()
    assert "reload this page" in page.inner_text("body")
    log = new_session(server.since(mark))
    assert count_exchanges(log) == 0, "no answer means no call to Firebase"
    assert "renders=app" not in log
    assert len(navigations) <= 1, "the app must never reload the page by itself"


def test_the_recovery_screen_fits_the_viewport(page, server):
    sign_in(page, server)
    break_bridge(page)
    page.reload()
    wait_for(page, on_recovery, RECOVERY_BOUND, "the recovery screen")
    size = page.evaluate("({scroll: document.documentElement.scrollWidth, inner: window.innerWidth})")
    assert size["scroll"] <= size["inner"], "no sideways scrolling"
    box = page.get_by_role("button", name="Try again").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= size["inner"]
    if page.viewport_name == "mobile":
        assert box["height"] >= 40, "a thumb can hit it"


def test_retries_are_a_fixed_number_and_then_the_page_goes_quiet(desktop, server):
    sign_in(desktop, server)
    frames = []
    desktop.on("request", lambda r: frames.append(r.url) if "/component/" in r.url and r.resource_type == "document" else None)
    break_bridge(desktop)
    sent = []
    desktop.on("websocket", lambda ws: ws.on("framesent", lambda payload: sent.append(1)))
    navigations = []
    desktop.on("framenavigated", lambda f: navigations.append(f.url) if f == desktop.main_frame else None)
    mark = server.mark()
    desktop.reload()
    wait_for(desktop, on_recovery, RECOVERY_BOUND, "the recovery screen")
    wait_for(desktop, lambda t: "Still trying" not in t.inner_text("body"), 30, "the schedule finishing")
    desktop.wait_for_timeout(3000)      # a tick already on its way
    assert len(frames) == FRAMES, "one frame per window, then no more"
    before = len(sent)
    desktop.wait_for_timeout(8000)
    assert len(sent) == before, "no timer left running"
    assert len(frames) == FRAMES and len(navigations) <= 1
    log = server.since(mark)
    assert "schedule finished" in log and count_exchanges(log) == 0


def test_streamlits_raw_warning_never_shows_and_try_again_still_works_after_it_fires(desktop, server):
    """Its 60 s timer fires under the recovery screen. This is the screenshot:
    the warning above a stuck beat. Neither should be left."""
    sign_in(desktop, server)
    break_bridge(desktop)
    desktop.reload()
    wait_for(desktop, on_recovery, RECOVERY_BOUND, "the recovery screen")
    desktop.wait_for_timeout(75000)
    shot(desktop, "after-streamlit-warning-timer")
    assert not visible_streamlit_warning(desktop)
    assert on_recovery(desktop) and not in_app(desktop)
    desktop.unroute(ROUTE)
    desktop.get_by_role("button", name="Try again").click()
    wait_for(desktop, in_app, 20, "the restored app")


# ──────────────────────────────────────────────────────────────────────────
# Recovering, with and without help
# ──────────────────────────────────────────────────────────────────────────
def test_a_transient_failure_recovers_by_itself_with_one_exchange(page, server):
    sign_in(page, server)
    failed = []

    def once(route):
        if route.request.resource_type == "document" and not failed:
            failed.append(1)
            route.fulfill(status=503, body="Service Unavailable")
        else:
            route.continue_()

    page.route(ROUTE, once)
    mark = server.mark()
    page.reload()
    wait_for(page, in_app, RECOVERY_BOUND, "the app, with nobody touching anything")
    log = server.since(mark)
    assert failed and count_exchanges(log) == 1
    assert "answered mount=1" in log
    assert not on_recovery(page)


def test_a_slow_asset_still_restores_after_the_recovery_screen(page, server):
    """The shape of what was seen: nothing for a long time, then it loads."""
    sign_in(page, server)
    held = []
    page.route(ROUTE, lambda route: held.append(route))
    mark = server.mark()
    page.reload()
    wait_for(page, on_recovery, RECOVERY_BOUND, "the recovery screen")
    page.unroute(ROUTE)
    for route in held:
        try:
            route.continue_()
        except Exception:  # noqa: BLE001 - that frame has been replaced
            pass
    wait_for(page, in_app, 20, "the app, restored by itself")
    assert count_exchanges(server.since(mark)) == 1


def test_try_again_recovers_once_the_asset_is_back(page, server):
    sign_in(page, server)
    break_bridge(page)
    mark = server.mark()
    page.reload()
    wait_for(page, on_recovery, RECOVERY_BOUND, "the recovery screen")
    wait_for(page, lambda t: "Still trying" not in t.inner_text("body"), 30, "the schedule finishing")
    page.unroute(ROUTE)
    assert not in_app(page), "nothing happens until somebody asks"
    page.get_by_role("button", name="Try again").click()
    wait_for(page, in_app, 20, "the restored app")
    log = server.since(mark)
    assert count_exchanges(log) == 1 and "retry requested" in log


def test_try_again_while_still_broken_goes_back_to_waiting_and_stays_closed(desktop, server):
    sign_in(desktop, server)
    break_bridge(desktop)
    mark = server.mark()
    desktop.reload()
    wait_for(desktop, on_recovery, RECOVERY_BOUND, "the recovery screen")
    desktop.get_by_role("button", name="Try again").click()
    wait_for(desktop, on_beat, 10, "the beat again")
    wait_for(desktop, on_recovery, RECOVERY_BOUND, "the recovery screen again")
    log = new_session(server.since(mark))
    assert not in_app(desktop) and count_exchanges(log) == 0 and "renders=app" not in log


def test_a_lost_handshake_recovers_on_a_new_frame(page, server):
    """The asset is fine but ``componentReady`` goes unheard, once."""
    sign_in(page, server)
    page.add_init_script("""
      if (window === window.top) {
        var dropped = false;
        window.addEventListener('message', function (e) {
          if (!dropped && e.data && e.data.type === 'streamlit:componentReady') {
            dropped = true;
            e.stopImmediatePropagation();
          }
        }, true);
      }
    """)
    mark = server.mark()
    page.reload()
    wait_for(page, in_app, RECOVERY_BOUND, "the app after a lost handshake")
    log = server.since(mark)
    assert "answered mount=1" in log and count_exchanges(log) == 1


def test_go_to_sign_in_needs_a_real_password_and_a_dead_bridge_stays_dead(page, server):
    sign_in(page, server)
    break_bridge(page)
    page.reload()
    wait_for(page, on_recovery, RECOVERY_BOUND, "the recovery screen")
    page.get_by_role("button", name="Go to sign-in").click()
    wait_for(page, on_login, 15, "the login page")
    assert not in_app(page)

    page.locator("input:not([type=password])").first.fill(RAVI[0])
    page.locator("input[type=password]").first.fill("wrong-password")
    page.locator("input[type=password]").first.press("Enter")
    page.wait_for_timeout(2500)
    assert not in_app(page), "Firebase still decides"

    page.locator("input[type=password]").first.fill(RAVI[1])
    page.locator("input[type=password]").first.press("Enter")
    wait_for(page, in_app, 20, "the app, after a real sign-in")


# ──────────────────────────────────────────────────────────────────────────
# What the cookie is worth
# ──────────────────────────────────────────────────────────────────────────
def cookie(server: Server, name: str, value: str) -> dict:
    return {"name": name, "value": quote(value, safe=""), "url": server.url}


def test_an_invalid_cookie_is_the_login_page_and_goes_on_the_next_run(page, server):
    """``restore`` queues the removal after this run's frame has rendered, so it
    reaches the browser with the next run: the first thing the person does."""
    page.context.add_cookies([cookie(server, "tr_session", "refresh.forged"),
                              cookie(server, "tr_session_started", str(time.time() - 3600))])
    mark = server.mark()
    page.goto(server.url)
    wait_for(page, on_login, 30, "the login page")
    assert not in_app(page) and count_exchanges(server.since(mark)) == 1
    page.locator("input:not([type=password])").first.fill(RAVI[0])
    page.locator("input[type=password]").first.fill("wrong-password")
    page.locator("input[type=password]").first.press("Enter")
    page.wait_for_function("!document.cookie.includes('tr_session=')", timeout=15000)


def test_an_expired_session_is_the_login_page_without_asking_google(page, server):
    page.context.add_cookies([cookie(server, "tr_session", "refresh.uid-ravi/aB+cD=eF"),
                              cookie(server, "tr_session_started", str(time.time() - 8 * 86400))])
    mark = server.mark()
    page.goto(server.url)
    wait_for(page, on_login, 30, "the login page")
    log = server.since(mark)
    assert not in_app(page) and count_exchanges(log) == 0 and "session_deadline_passed" in log


def test_admin_comes_from_firebase_and_a_cookie_cannot_claim_it(page, server):
    sign_in(page, server)
    page.context.add_cookies([cookie(server, "tr_admin", "true"), cookie(server, "admin", "true"),
                              cookie(server, "uid", "uid-admin")])
    mark = server.mark()
    page.reload()
    wait_for(page, in_app, 20, "the restored app")
    log = server.since(mark)
    assert "admin=false" in log and "admin=true" not in log


def test_an_administrator_is_recognised_through_a_restore_after_a_failure(page, server):
    sign_in(page, server, ADA)
    failed = []

    def once(route):
        if route.request.resource_type == "document" and not failed:
            failed.append(1)
            route.abort()
        else:
            route.continue_()

    page.route(ROUTE, once)
    mark = server.mark()
    page.reload()
    wait_for(page, in_app, RECOVERY_BOUND, "the restored app")
    assert "uid_present=True admin=true SUCCESS" in server.since(mark)


def test_no_token_reaches_the_console_or_the_log(page, server):
    messages = []
    page.on("console", lambda m: messages.append(m.text))
    page.on("pageerror", lambda e: messages.append(str(e)))
    sign_in(page, server)
    break_bridge(page)
    mark = server.mark()
    page.reload()
    wait_for(page, on_recovery, RECOVERY_BOUND, "the recovery screen")
    page.unroute(ROUTE)
    wait_for(page, in_app, 20, "the restored app")
    everything = " ".join(messages) + server.since(mark)
    for secret in ("refresh.uid-", "id.uid-", "ravi@example.com"):
        assert secret not in everything, secret
