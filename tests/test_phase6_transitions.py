"""Phase 6 — the moments between pages: one clean swap, and no work twice.

Measured on the real app in Chromium (with the store answering as slowly as
Firestore does on a cold read):

* **Login and Home overlapping.** The run that opens the app replaced the
  login page's stylesheet with its own first element, but Streamlit keeps the
  rest of a page mounted until the run ends; behind the store read the login
  page's text and form fields sat unstyled beside the new rail and account
  chip. The rail itself also arrived first, beside a login page squeezed to
  make room for it.
* **The reel before Home.** Home's ambience — the big film reel — is Home's
  first element, so it reached the screen first and at full strength while the
  hero and the cards were still rising from nothing.
* **Three extra runs after every sign-in.** The cookie bridge's nonce was
  ``time.time()`` at each draw, and fell back to 0 once nothing was queued, so
  the frame wrote and reported again, then reported once more: every report is
  a full script run of the whole app.

These pin the fixes without a browser; ``tests/test_transition_browser.py``
replays the transitions in Chromium.
"""

from __future__ import annotations

import re
import types

import pytest

from auth import session
from ui import home, login, theme

# ──────────────────────────────────────────────────────────────────────────
# The bridge: a batch keeps its nonce; nothing to send changes nothing
# ──────────────────────────────────────────────────────────────────────────


class Browser:
    """The bridge's frame as auth/bridge/index.html behaves.

    It acts on a render only when the render's arguments change (Streamlit
    re-renders a component only for new arguments), writes the cookies it was
    handed, and reports the jar — and only *speaks* when the report differs
    from the last one it sent. Every report is one more script run.
    """

    def __init__(self):
        self.jar: dict[str, str] = {}
        self.value = None          # what the component hands Python
        self.last_args = None
        self.last_sent = None
        self.writes = 0

    def component(self, **kw):
        self.args = {k: kw[k] for k in ("ops", "names", "signal", "nonce")}
        return self.value

    def after_run(self) -> bool:
        """The frontend after a run: True when the bridge reported (a rerun)."""
        if self.args == self.last_args:
            return False
        self.last_args = self.args
        for op in self.args["ops"]:
            self.jar[op["name"]] = op["value"]
            self.writes += 1
        report = {"seen": len(self.jar), "nonce": self.args["nonce"], "opener": False, "signals": 0,
                  **{n: self.jar.get(n, "") for n in self.args["names"]}}
        if report == self.last_sent:
            return False
        self.last_sent = report
        self.value = report
        return True


@pytest.fixture
def bridge(monkeypatch):
    browser = Browser()
    state: dict = {}
    monkeypatch.setattr(session, "st", types.SimpleNamespace(session_state=state))
    monkeypatch.setattr(session, "_bridge_component", lambda: browser.component)
    clock = iter(range(1_000, 10_000))
    monkeypatch.setattr(session.time, "time", lambda: float(next(clock)))
    return types.SimpleNamespace(browser=browser, state=state)


def runs_until_quiet(bridge, limit: int = 10) -> int:
    """Script runs, starting with this one, until the bridge stops reporting."""
    runs = 0
    while runs < limit:
        session.run_bridge()
        runs += 1
        if not bridge.browser.after_run():
            return runs
    raise AssertionError("the bridge never went quiet")


def test_signing_in_costs_one_run_to_write_the_cookie_and_one_to_hear_it(bridge):
    # The login page's bridge has answered (an empty jar).
    assert runs_until_quiet(bridge) == 2
    # Signing in wipes the session (the widget's value with it) and queues the
    # two session cookies for the first run of the app.
    bridge.state.clear()
    bridge.browser.value = None
    session.queue_cookie(session.COOKIE, "refresh-token", 3600)
    session.queue_cookie(session.SESSION_STARTED_COOKIE, "1700000000", 3600)
    # The app's first run hands them over; the frame writes and reports; the
    # run that hears it is the last. It used to take two more.
    assert runs_until_quiet(bridge) == 2
    assert bridge.browser.writes == 2                 # each cookie written once, not twice
    assert bridge.browser.jar[session.COOKIE] == "refresh-token"
    assert session.BRIDGE_UNACKED_KEY not in bridge.state


def test_a_batch_the_frame_has_not_answered_is_resent_unchanged(bridge):
    session.queue_cookie(session.COOKIE, "refresh-token", 3600)
    session.run_bridge()
    first = dict(bridge.browser.args)
    session.run_bridge()                              # still no answer
    assert bridge.browser.args == first               # same ops, same nonce: no second write
    assert bridge.state[session.BRIDGE_UNACKED_KEY] == first["ops"]


def test_new_work_is_always_a_new_render(bridge):
    session.queue_cookie(session.COOKIE, "a", 3600)
    session.run_bridge()
    bridge.browser.after_run()
    nonce, ops = bridge.browser.args["nonce"], bridge.browser.args["ops"]
    session.run_bridge()                              # the run that hears it: the same render as before
    assert bridge.browser.args == {**bridge.browser.args, "nonce": nonce, "ops": ops}
    assert bridge.browser.after_run() is False
    session.run_bridge()                              # acknowledged; nothing new to send
    assert bridge.browser.args["nonce"] == nonce and bridge.browser.args["ops"] == []
    assert bridge.browser.after_run() is False
    session.queue_cookie(session.COOKIE, "a", 3600)    # the same write again is still work
    session.run_bridge()
    assert bridge.browser.args["nonce"] != nonce
    assert bridge.browser.after_run() is True


def test_nothing_queued_keeps_the_last_nonce_rather_than_falling_back_to_zero(bridge):
    session.queue_cookie(session.COOKIE, "a", 3600)
    session.run_bridge()
    bridge.browser.after_run()
    session.run_bridge()                               # hears the report
    session.run_bridge()                               # nothing left to send
    assert bridge.browser.args["ops"] == [] and bridge.browser.args["nonce"] != 0
    assert bridge.browser.after_run() is False         # no report, so no rerun


def test_a_fresh_session_with_nothing_to_send_draws_nonce_zero(bridge):
    session.run_bridge()
    assert bridge.browser.args["nonce"] == 0 and bridge.browser.args["ops"] == []


def test_a_signal_is_still_a_fresh_render_every_time(bridge):
    session.run_bridge(signal=session.GOOGLE_SIGNAL)
    one = bridge.browser.args["nonce"]
    session.run_bridge(signal=session.GOOGLE_SIGNAL)
    assert bridge.browser.args["nonce"] not in (0, one)


def test_a_session_reset_forgets_the_nonce():
    assert session.BRIDGE_NONCE_KEY not in session.RESET_KEEPS


# ──────────────────────────────────────────────────────────────────────────
# The login page leaves with its stylesheet
# ──────────────────────────────────────────────────────────────────────────
def _rule(css: str, selector: str) -> str:
    """The declarations of every rule whose selector list is exactly ``selector``."""
    found = re.findall(r"(?:^|[}\n])\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert found, selector
    return " ".join(found)


def test_the_theme_hides_the_login_shell_and_the_login_stylesheet_shows_it():
    assert "display:none !important" in _rule(theme.CSS, '[class*="st-key-trauth_shell"]')
    assert "display: flex !important" in _rule(login.CSS, '[class*="st-key-trauth_shell"]')


def test_the_login_stylesheet_takes_the_rail_away():
    hidden = _rule(login.CSS, '[data-testid="stSidebar"], button[data-testid="stExpandSidebarButton"]')
    assert "display: none !important" in hidden


def test_stale_login_pieces_outside_the_shell_are_not_drawn():
    markup = '[data-stale="true"]:has([class*="tr-auth-"]):not([class*="st-key-trauth_shell"] *)'
    widgets = ('[data-stale="true"][class*="st-key-auth_"]:not([class*="st-key-auth_signout"])'
               ':not([class*="st-key-trauth_shell"] *)')
    # Two rules, not one list: a browser without :has() drops a whole list,
    # and the widget rule needs no :has().
    assert "display:none !important" in _rule(theme.CSS, markup)
    assert "display:none !important" in _rule(theme.CSS, widgets)
    assert ":has(" not in widgets


def test_only_the_login_form_uses_auth_widget_keys_besides_sign_out():
    """The stale rule hides stale widgets keyed ``auth_*``. Every such key is
    the login form's, except the app's Sign out, which the rule excludes."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    keys = set()
    for path in [root / "app.py", *sorted((root / "ui").glob("*.py")), *sorted((root / "auth").glob("*.py"))]:
        if path.name == "login.py":
            continue
        keys |= set(re.findall(r'key="(auth_[a-z_]+)"', path.read_text(encoding="utf-8")))
    assert keys == {"auth_signout"}


def test_the_login_page_draws_the_theme_before_its_own_stylesheet(run_login):
    """Equal selectors, both !important: the later one wins, so the login
    stylesheet must come after the theme on the page."""
    styles = [m.value for m in run_login.markdown if "<style" in m.value]
    theme_at = next(i for i, s in enumerate(styles) if 'st-key-trauth_shell"] { display:none' in s)
    login_at = next(i for i, s in enumerate(styles) if 'st-key-trauth_shell"] { position: relative; display: flex' in s)
    assert theme_at < login_at


@pytest.fixture
def run_login(visitor):  # noqa: F811 - the imported fixture
    return run()


# ──────────────────────────────────────────────────────────────────────────
# The reel waits for the hero
# ──────────────────────────────────────────────────────────────────────────
def test_the_reel_is_invisible_until_the_hero_is_on_the_page():
    assert "opacity:0" in _rule(home.CSS, ".tr-home-ambience")
    # …only where :has() exists; elsewhere the reel shows as it always did.
    guarded = home.CSS.split("@supports selector(:has(*)) {", 1)[1].split("\n}", 1)[0]
    assert ".tr-home-ambience { opacity:0; }" in guarded
    shown = _rule(home.CSS, '[class*="st-key-trhome"]:has(.tr-hero) .tr-home-ambience')
    assert "opacity:1" in shown and "tr-home-ambience-in" in shown
    assert "@keyframes tr-home-ambience-in" in home.CSS


def test_the_reel_fades_in_behind_the_hero_not_ahead_of_it():
    shown = _rule(home.CSS, '[class*="st-key-trhome"]:has(.tr-hero) .tr-home-ambience')
    delay = float(re.search(r"tr-home-ambience-in [\d.]+s \S+ ([\d.]+)s", shown).group(1))
    assert delay > 0                                     # starts after the hero has begun to rise


def test_reduced_motion_shows_the_reel_without_the_fade():
    reduced = home.CSS.split("@media (prefers-reduced-motion: reduce)")[-1]
    assert '[class*="st-key-trhome"]:has(.tr-hero) .tr-home-ambience {{ animation:none; }}'.replace("{{", "{").replace("}}", "}") in reduced


def test_the_reel_and_its_animation_are_unchanged():
    """The visual identity stays: the same reels, the same turn, the same
    formats in the track — only when the layer appears has moved."""
    markup = home.reel_svg("big", formats=True)
    assert 'class="tr-reel big"' in markup
    assert "tr-home-reel 84s linear infinite" in home.CSS
    assert "tr-home-reel-back 120s linear infinite" in home.CSS


# ──────────────────────────────────────────────────────────────────────────
# One swap: the app waits in its own slot behind the page it replaces
# ──────────────────────────────────────────────────────────────────────────
def test_the_gate_reserves_three_slots_in_a_fixed_order():
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent.joinpath("auth", "gate.py").read_text(encoding="utf-8")
    order = [src.index(f'st.container(key="{key}")') for key in ("tr_chrome", "tr_page", "tr_app")]
    assert order == sorted(order)
    assert src.count('st.container(key="tr_app")') == 1


def test_the_app_renders_in_its_own_slot_and_the_login_page_in_the_page_slot(monkeypatch):
    """The login page, the restoring beat and its recovery screen draw into
    ``tr_page``; ``app_container()`` is ``tr_app`` — never the same slot, so the
    app never takes over the login page's elements."""
    from auth import gate

    made = []

    class Slot:
        def __init__(self, key):
            self.key = key

        def __enter__(self):
            made.append(("enter", self.key))
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(gate.st, "container", lambda key=None, **k: Slot(key))
    gate._page.page = gate.st.container(key="tr_page")
    gate._page.container = gate.st.container(key="tr_app")
    assert gate.app_container().key == "tr_app"
    assert gate._page.page.key == "tr_page"


def test_the_app_is_held_only_while_the_page_slot_holds_something_stale():
    hold = _rule(theme.CSS, '.stApp:has([class*="st-key-tr_page"] [data-stale="true"]) [class*="st-key-tr_app"]')
    assert "display:none !important" in hold
    # Nothing else in the theme hides the app's slot.
    rules = re.sub(r"/\*.*?\*/", "", theme.CSS, flags=re.S)
    hides = re.findall(r'([^{}]*st-key-tr_app[^{}]*)\{([^}]*)\}', rules)
    assert [sel.strip() for sel, body in hides if "display:none" in body.replace(" ", "")] == [
        '.stApp:has([class*="st-key-tr_page"] [data-stale="true"]) [class*="st-key-tr_app"]']


def test_signing_out_hides_only_the_stale_app():
    assert "display: none !important" in _rule(login.CSS, '[class*="st-key-tr_app"]:has([data-stale="true"])')


def test_the_entrance_draws_nothing_at_the_top_of_the_run(monkeypatch):
    """The fade used to be drawn at the top of the first run after signing in:
    with a slow store it faded the login page out and back in, and its element
    moved the cookie bridge to a new position (a new frame on every sign-in)."""
    import types

    state = {"auth_just_signed_in": True}
    drawn = []
    monkeypatch.setattr(login, "st", types.SimpleNamespace(session_state=state, markdown=lambda *a, **k: drawn.append(a)))
    login.entrance()
    assert drawn == [] and "auth_just_signed_in" not in state


def test_the_entrance_is_the_app_slot_only_and_never_a_transform():
    assert "st-key-tr_app" in theme.APP_ENTRANCE and theme.APP_ENTRANCE in theme.CSS
    assert "stSidebar" not in theme.APP_ENTRANCE and "transform" not in theme.APP_ENTRANCE
    assert "stMainBlockContainer" not in login.ENTRANCE_CSS


def test_homes_rise_starts_half_lit_not_from_nothing():
    rise = re.search(r"@keyframes tr-home-rise \{ from \{([^}]*)\}", home.CSS).group(1)
    assert "opacity:.5" in rise and "translateY(10px)" in rise
    assert "tr-home-rise .8s var(--tr-ease) backwards" in home.CSS      # same rise, same timing


from tests.test_auth import run, visitor  # noqa: E402,F401 - the fixture and the runner
