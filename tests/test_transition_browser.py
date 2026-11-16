"""Phase 6 in a real browser: the swaps between pages, frame by frame.

Runs the real app (``tools/fake_firebase_app.py``) in Chromium with the store
answering a cold read as slowly as Firestore does from the deployed app
(``TR_STORE_LATENCY_MS``) — without that delay none of these transitions can go
wrong, which is how they hid. A ``requestAnimationFrame`` sampler records, at
every frame, whether login text is painted, whether the app's rail, chip or
Home are, and whether the big reel and the hero are.

Before Phase 6 (same harness, main at 757d49b): the login page's text and
form fields were painted beside the rail and account chip during sign-in, the
big reel was at full strength before the hero on every reload and every return
to Home, and a sign-in cost four runs of the app.

Off by default, like the rest of the browser suite:

    TR_BROWSER_TESTS=1 python -m pytest tests/test_transition_browser.py
"""

from __future__ import annotations

import os
import time

import pytest

from tests.test_auth_bridge_browser import (
    RAVI,
    Server,
    chromium,  # noqa: F401 - a fixture
    in_app,
    on_login,
    wait_for,
)
from ui import home

pytestmark = pytest.mark.skipif(os.environ.get("TR_BROWSER_TESTS") != "1",
                                reason="browser tests: set TR_BROWSER_TESTS=1")

LATENCY_MS = "600"

SAMPLER = r"""
(() => {
  if (window.top !== window) return;
  window.__frames = [];
  const shown = (el) => { if (!el) return false; const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.top > innerHeight) return false;
    if (getComputedStyle(el).visibility !== 'visible') return false;
    for (let e = el; e && e !== document.documentElement; e = e.parentElement) {
      const s = getComputedStyle(e); if (s.display === 'none' || parseFloat(s.opacity) < 0.05) return false; }
    return true; };
  const loginText = () => {
    const main = document.querySelector('[data-testid="stMain"]'); if (!main) return false;
    const w = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
    while (w.nextNode()) { const n = w.currentNode;
      if (/Never Miss|Welcome back|RADAR ONLINE|Forgot password|Continue with Google|Create account/i.test(n.textContent) && shown(n.parentElement)) return true; }
    for (const i of main.querySelectorAll('input')) if (/@example\.com/.test(i.value) && shown(i)) return true;
    return false; };
  const tick = () => {
    const q = (s) => document.querySelector(s);
    window.__frames.push({
      t: Math.round(performance.now()),
      login: loginText(),
      app: shown(q('[data-testid="stSidebar"]')) || shown(q('[class*="st-key-acct_menu"]')) || shown(q('.tr-hero')),
      reel: shown(q('.tr-home-ambience .tr-reel.big')),
      hero: shown(q('.tr-hero')),
    });
    requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
})();
"""


@pytest.fixture(scope="module")
def slow_server():
    previous = os.environ.get("TR_STORE_LATENCY_MS")
    os.environ["TR_STORE_LATENCY_MS"] = LATENCY_MS
    try:
        srv = Server()
    finally:
        if previous is None:
            os.environ.pop("TR_STORE_LATENCY_MS", None)
        else:
            os.environ["TR_STORE_LATENCY_MS"] = previous
    yield srv
    srv.stop()


@pytest.fixture(params=[{"width": 1440, "height": 900}, {"width": 1024, "height": 768}, {"width": 390, "height": 844}],
                ids=["1440", "1024", "390"])
def tab(request, chromium):  # noqa: F811 - the imported fixture
    context = chromium.new_context(viewport=request.param)
    context.add_init_script(SAMPLER)
    page = context.new_page()
    yield page
    context.close()


def frames(tab) -> list[dict]:
    return tab.evaluate("window.__frames")


def reset(tab) -> None:
    tab.evaluate("window.__frames = []")


def settle(tab, seconds: float = 2.5) -> None:
    """Long enough for the hero's rise and the reel's fade to finish."""
    tab.wait_for_timeout(int(seconds * 1000))


def sign_in(tab, server) -> None:
    tab.goto(server.url)
    wait_for(tab, on_login, 60, "the login page")
    tab.wait_for_timeout(800)
    tab.locator("input:not([type=password])").first.fill(RAVI[0])
    tab.locator("input[type=password]").first.fill(RAVI[1])
    reset(tab)
    tab.locator("input[type=password]").first.press("Enter")
    wait_for(tab, in_app, 30, "the app after signing in")
    settle(tab)


def overlaps(seen: list[dict]) -> list[int]:
    return [f["t"] for f in seen if f["login"] and f["app"]]


def reel_ahead_of_hero(seen: list[dict]) -> list[int]:
    return [f["t"] for f in seen if f["reel"] and not f["hero"]]


def test_signing_in_never_paints_the_login_page_beside_the_app(tab, slow_server):
    mark = slow_server.mark()
    sign_in(tab, slow_server)
    seen = frames(tab)
    assert any(f["login"] for f in seen) and any(f["app"] for f in seen), "the sampler saw both pages"
    assert overlaps(seen) == []
    assert reel_ahead_of_hero(seen) == []
    # One run of the app to write the session cookie, one to hear it written.
    log = slow_server.since(mark)
    assert log.count("renders=app") == 2, log.count("renders=app")


def test_a_reload_brings_the_reel_in_behind_the_hero(tab, slow_server):
    sign_in(tab, slow_server)
    tab.wait_for_timeout(5500)                       # past the store cache: a cold read again
    reset(tab)
    tab.reload()
    wait_for(tab, in_app, 30, "the restored app")
    settle(tab)
    seen = frames(tab)
    assert reel_ahead_of_hero(seen) == []
    if tab.viewport_size["width"] >= home.DESKTOP_MIN:   # narrower, the reel sits below the first screen
        assert any(f["reel"] for f in seen), "the reel did arrive"


def test_signing_out_never_paints_the_app_beside_the_login_page(tab, slow_server):
    sign_in(tab, slow_server)
    reset(tab)
    tab.locator('[class*="st-key-acct_menu"] button').first.click()
    tab.get_by_role("button", name="Sign out").click()
    wait_for(tab, on_login, 30, "the login page after signing out")
    settle(tab, 1.5)
    seen = frames(tab)
    assert overlaps(seen) == []
    assert not tab.locator('[data-testid="stSidebar"]').first.is_visible()


def test_a_rerun_of_home_does_not_restart_the_reel(tab, slow_server):
    if tab.viewport_size["width"] < home.DESKTOP_MIN:
        pytest.skip("narrower than the desktop grid, the reel is below the first screen")
    sign_in(tab, slow_server)
    reel = '.tr-home-ambience .tr-reel.big'
    read = f"() => document.querySelector('{reel}').getAnimations()[0].currentTime"
    before = tab.evaluate(read)
    tab.mouse.click(700, 5)
    tab.keyboard.press("r")                          # Streamlit's own rerun
    time.sleep(1.5)
    after = tab.evaluate(read)
    assert after > before + 1000, (before, after)    # still turning from where it was
