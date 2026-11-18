"""Showtime Explorer — the time window survives every kind of run, and a
theatre is shown with the name BookMyShow gives it.

Two bugs, both reproduced in a real browser before they were fixed:

* **From = To = the server's clock.** Streamlit sends a keyed widget's value
  to the browser only on the run where it was set through Session State.
  After a run that ended early (``st.stop()``: the auth gate, storage being
  unavailable) the server kept ``sx_from``/``sx_to`` while the browser forgot
  the inputs; the next run sent no value and the browser showed the widget's
  built-in ``"now"`` — the server's time, UTC on Streamlit Cloud — in both,
  so the page refused the window. The nav radio fell back to Home the same
  way. ``ui.widget_state.hold`` now re-sends the held value on every run.
* **"PVR / Gachibowli" for two different theatres.** The By-movie list printed
  the locality keyword instead of BookMyShow's own name ("PVR: Atrium
  Gachibowli" vs "PVR: Preston, Gachibowli"). The first line is now the full
  official name ("PVR: Atrium") and the second the locality ("Gachibowli");
  see ``tests/test_explorer_full_theatre_names.py`` for that contract.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from datetime import time as dtime
from pathlib import Path
from types import SimpleNamespace

import pytest

from config.timezone import IST, now_ist
from explorer import query as Q
from explorer import source as S
from explorer.classify import UNKNOWN, classify
from monitor import state as state_mod
from monitor.models import Availability, MovieRef, Showtime, Venue
from platforms.bookmyshow import split_venue_name
from tests import test_showtime_explorer as base
from tests.test_showtime_explorer import TOMORROW, body, ok, pick_movie, run
from ui import explorer as X

#: The Explorer page in AppTest with a fake BookMyShow (tests/test_showtime_explorer.py).
page_env = base.page_env

WHOLE_DAY = (dtime(0, 0), dtime(23, 59))


# ── helpers ─────────────────────────────────────────────────────────────
def window(app) -> tuple[dtime, dtime]:
    return app.time_input(key="sx_from").value, app.time_input(key="sx_to").value


def sent_to_browser(app, key: str) -> str:
    """The value this run tells the browser to show ("" = none: the browser
    would fall back to the widget's built-in default)."""
    proto = app.time_input(key=key).proto
    return proto.value if proto.set_value else ""


def fail_storage_once(monkeypatch):
    """The next store read raises StorageUnavailable, so app.py ends that run
    with st.stop() — the run the bug needed."""
    real = state_mod.load_many
    calls = {"n": 0}

    def once(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise state_mod.StorageUnavailable("storage is down for one read")
        return real(*a, **k)

    monkeypatch.setattr(state_mod, "load_many", once)
    return calls


def stopped_run(app, monkeypatch):
    calls = fail_storage_once(monkeypatch)
    app = app.run()
    assert calls["n"] == 1
    assert any("storage isn't available" in e.value for e in app.error)
    assert not app.time_input                      # the Explorer was not drawn
    return app


# ── the time window: defaults, presets, dates, validation ───────────────
def test_a_fresh_explorer_is_the_whole_day_with_no_error(page_env):
    app = ok(run())
    assert window(app) == WHOLE_DAY
    assert "end time must be later" not in body(app)


def test_the_window_is_sent_to_the_browser_on_every_run(page_env):
    """The contract the fix rests on: never a run where the browser is left
    to its built-in default ("now", on the server's clock)."""
    app = ok(run())
    for _ in range(3):
        assert (sent_to_browser(app, "sx_from"), sent_to_browser(app, "sx_to")) == ("00:00", "23:59")
        app = ok(app.run())


@pytest.mark.parametrize("label, lo, hi", Q.PRESETS)
def test_each_preset_sets_exactly_its_window(page_env, label, lo, hi):
    app = ok(run())
    app = ok(app.button(key="sxpre_morning" if label != "Morning" else "sxpre_night").click().run())
    app = ok(app.button(key=f"sxpre_{label.lower().replace(' ', '_')}").click().run())
    assert window(app) == (lo, hi)
    assert (sent_to_browser(app, "sx_from"), sent_to_browser(app, "sx_to")) == (lo.strftime("%H:%M"),
                                                                                hi.strftime("%H:%M"))


def test_all_day_restores_the_whole_day(page_env):
    app = ok(run())
    app = ok(app.button(key="sxpre_evening").click().run())
    assert window(app) == (dtime(17, 0), dtime(20, 59))
    app = ok(app.button(key="sxpre_all_day").click().run())
    assert window(app) == WHOLE_DAY


def test_a_chosen_window_survives_further_runs_and_a_date_change(page_env):
    app = ok(run())
    app.time_input(key="sx_from").set_value(dtime(7, 0)).run()
    app = ok(app.time_input(key="sx_to").set_value(dtime(11, 0)).run())
    app = ok(app.run())
    assert window(app) == (dtime(7, 0), dtime(11, 0))
    app.session_state["sx_date"] = TOMORROW              # what the date tile's callback sets
    app = ok(app.run())
    assert window(app) == (dtime(7, 0), dtime(11, 0))
    assert sent_to_browser(app, "sx_from") == "07:00"


def test_equal_start_and_end_are_refused_on_the_page(page_env):
    app = pick_movie(ok(run(sx_date=TOMORROW)))
    app.time_input(key="sx_from").set_value(dtime(12, 56)).run()
    app = ok(app.time_input(key="sx_to").set_value(dtime(12, 56)).run())
    assert "end time must be later" in body(app) and 'class="tr-sx-row"' not in body(app)
    with pytest.raises(Q.WindowError):
        Q.validate_window(dtime(12, 56), dtime(12, 56))


def test_a_show_after_midnight_belongs_to_the_next_date_in_ist():
    """No overnight window by design: BookMyShow dates a show by its own
    date code, so 00:30 on the 19th is found under the 19th."""
    show = Q.ExplorerShow(movie_id="m", movie_title="t", language="Telugu", event_code="ET1",
                          venue_code="V", venue_name="V", area="", locality="", operator="",
                          theatre_type="", date_code="20261119", time_label="12:30 AM",
                          minutes=Q.parse_minutes("", "12:30 AM"))
    assert show.minutes == 30
    assert show.starts_at == datetime(2026, 11, 19, 0, 30, tzinfo=IST)
    assert Q.apply([show], Q.Filters("20261119", start=dtime(0, 0), end=dtime(0, 59))).shows == [show]
    assert Q.apply([show], Q.Filters("20261119", start=dtime(21, 0), end=dtime(23, 59))).shows == []


# ── the time window: the runs that used to break it ─────────────────────
def test_after_a_stopped_run_the_explorer_sends_its_window_again(page_env, monkeypatch):
    """The reproduced bug. A run that ends in st.stop() keeps sx_from/sx_to
    on the server (Streamlit skips clean-up) while the browser forgets the
    inputs; the next Explorer run must tell the browser the window."""
    app = ok(run())
    app = stopped_run(app, monkeypatch)
    assert "sx_from" in app.session_state                # kept by the server — the trigger
    app = ok(app.run())                                  # storage back: the Explorer again
    assert window(app) == WHOLE_DAY
    assert (sent_to_browser(app, "sx_from"), sent_to_browser(app, "sx_to")) == ("00:00", "23:59")
    assert "end time must be later" not in body(app)


def test_a_chosen_window_survives_a_storage_failure(page_env, monkeypatch):
    app = ok(run())
    app.time_input(key="sx_from").set_value(dtime(18, 0)).run()
    app = ok(app.time_input(key="sx_to").set_value(dtime(22, 30)).run())
    app = stopped_run(app, monkeypatch)
    app = ok(app.run())
    assert window(app) == (dtime(18, 0), dtime(22, 30))  # never replaced by the server's clock
    assert (sent_to_browser(app, "sx_from"), sent_to_browser(app, "sx_to")) == ("18:00", "22:30")


def test_navigating_away_and_back_keeps_the_chosen_window(page_env):
    app = ok(run())
    app = ok(app.button(key="sxpre_afternoon").click().run())
    app = ok(app.radio(key="page").set_value("Home").run())
    assert "sx_from" not in app.session_state            # Streamlit dropped the widget key…
    app = ok(app.radio(key="page").set_value("Showtime Explorer").run())
    assert window(app) == (dtime(12, 0), dtime(16, 59))  # …the page still remembers the window
    assert sent_to_browser(app, "sx_from") == "12:00"


def test_a_broken_held_value_becomes_the_whole_day_not_the_clock(page_env):
    app = ok(run())
    app.session_state["sx_from_keep"] = "12:56"           # not a time: never trusted
    app.session_state["sx_to_keep"] = None
    app = ok(app.radio(key="page").set_value("Home").run())
    app = ok(app.radio(key="page").set_value("Showtime Explorer").run())
    assert window(app) == WHOLE_DAY


def test_the_nav_radio_is_sent_on_every_run_and_survives_a_stopped_run(page_env, monkeypatch):
    """The same bug in the rail: after a stopped run the browser showed Home
    while the Explorer was open, and the next click navigated there."""
    app = ok(run())
    for _ in range(2):                                    # a plain rerun too: the old code sent nothing
        radio = app.radio(key="page")
        assert radio.value == "Showtime Explorer"
        assert radio.proto.set_value and radio.proto.raw_value == "Showtime Explorer"
        app = ok(app.run())
    app = stopped_run(app, monkeypatch)
    assert app.session_state["page"] == "Showtime Explorer"
    app = ok(app.run())
    radio = app.radio(key="page")
    assert radio.value == "Showtime Explorer"
    assert radio.proto.set_value and radio.proto.raw_value == "Showtime Explorer"
    assert app.time_input                                 # still on the Explorer


def test_an_unknown_page_falls_back_to_home(page_env):
    app = run(page="No such page")
    ok(app)
    assert app.radio(key="page").value == "Home"


def test_the_window_does_not_depend_on_the_server_timezone(page_env, monkeypatch):
    """Streamlit's "now" is the server's local time (UTC on Streamlit Cloud).
    With the server in UTC the window is still the IST whole day, always sent,
    and the date strip starts on today's date in IST."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    try:
        app = ok(run())
        app = ok(app.run())
        assert window(app) == WHOLE_DAY
        assert (sent_to_browser(app, "sx_from"), sent_to_browser(app, "sx_to")) == ("00:00", "23:59")
        assert now_ist().utcoffset() == timedelta(hours=5, minutes=30)
        late_utc = datetime(2026, 11, 17, 20, 0, tzinfo=timezone.utc)     # 01:30 on the 18th in IST
        assert X.date_codes(late_utc.astimezone(IST))[0] == "20261118"
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


# ── theatre names ───────────────────────────────────────────────────────
SCENE = MovieRef(platform="bookmyshow", event_code="ET00525065", title="Scene", region_code="HYD",
                 region_slug="hyderabad", city="Hyderabad", language="Telugu",
                 source_url="https://in.bookmyshow.com/movies/hyderabad/scene/buytickets/ET00525065/20261118")

#: As the catalogue holds them (data/catalogue.json): BookMyShow's venue name
#: split at the colon by platforms.bookmyshow.split_venue_name.
BRANCHES = {
    "PVTS": ("PVR", "Atrium Gachibowli, Hyderabad"),
    "PVTP": ("PVR", "Preston, Gachibowli Hyderabad"),
    "PNNG": ("PVR", "Next Galleria Mall, Panjagutta"),
    "PVYH": ("PVR", "Central Mall, Panjagutta"),
    "PVUM": ("PVR", "Musarambagh, Hyderabad"),
    "ILKS": ("PVR Lakeshore PXL 4K Laser ATMOS DTS-X Y Junction", ""),
}


def live_shows(codes=tuple(BRANCHES), *, venues=None) -> list[Q.ExplorerShow]:
    """Explorer shows built the way a live read builds them (shows_from_day)."""
    venues = [Venue(c, *BRANCHES[c]) for c in codes] if venues is None else venues
    times = [f"{10 + i // 6:02d}{(i % 6) * 10:02d}" for i in range(len(codes))]
    day = SimpleNamespace(venues=venues, showtimes=[
        Showtime(c, BRANCHES.get(c, ("", ""))[0], f"S{c}", "20261118", "", t, "2D", Availability.AVAILABLE)
        for c, t in zip(codes, times)])
    return S.shows_from_day(day, SCENE, "bookmyshow:ET00525065")


def test_branches_of_one_chain_keep_their_own_names():
    shows = live_shows()
    lines = {s.venue_code: f"{s.theatre_title} / {X._where(s)}" for s in shows}
    assert lines == {
        "PVTS": "PVR: Atrium / Gachibowli",
        "PVTP": "PVR: Preston / Gachibowli",
        "PNNG": "PVR: Next Galleria Mall / Panjagutta",
        "PVYH": "PVR: Central Mall / Panjagutta",
        "PVUM": "PVR: Musarambagh / Musarambagh",
        "ILKS": "PVR Lakeshore PXL 4K Laser ATMOS DTS-X Y Junction / Hyderabad",
    }


def test_the_by_movie_list_and_group_headings_show_the_full_name(page_env):
    shows = live_shows()
    html = X.time_rows_markup(shows, "movie")
    for text in ("PVR: Atrium", "PVR: Preston", "PVR: Next Galleria Mall", "PVR: Central Mall",
                 "PVR: Musarambagh", "PVR Lakeshore PXL 4K Laser ATMOS DTS-X Y Junction"):
        assert f'<div class="n">{text}</div>' in html
    assert ", Hyderabad<" not in html and " Hyderabad<" not in html.replace(">Hyderabad<", "")
    groups = X.group_markup(Q.group_by_theatre(shows), "movie")
    assert '<div class="n">PVR: Atrium</div><div class="s">Gachibowli · ' in groups
    assert '<div class="n">PVR: Preston</div><div class="s">Gachibowli · ' in groups


def test_the_page_shows_the_full_theatre_name(page_env):
    app = pick_movie(ok(run(sx_date=TOMORROW)))
    html = body(app)
    assert '<div class="n">PVR: Nexus Mall</div><div class="s">Kukatpally</div>' in html
    assert "Nexus Mall Kukatpally, Hyderabad" not in html  # the city tail is dropped


def test_no_two_theatres_in_the_catalogue_look_the_same():
    """On the real synced catalogue: no two different venue codes render the
    same first line (Atrium/Preston and Next Galleria/Central once did)."""
    data = json.loads(Path("data/catalogue.json").read_text(encoding="utf-8"))
    venues = {v["code"]: Venue(v["code"], v["name"], v["area"]) for m in data["movies"] for v in m["venues"]}
    if not venues:
        pytest.skip("the catalogue has no venues")
    shows = live_shows(tuple(venues), venues=list(venues.values()))
    seen: dict[str, str] = {}
    clashes = [(s.theatre_title, seen[s.theatre_title], s.venue_code) for s in shows
               if seen.setdefault(s.theatre_title, s.venue_code) != s.venue_code]
    assert clashes == []


def test_the_second_line_is_the_locality_only():
    def where(name, area):
        facts = classify(name, area)
        return X._where(Q.ExplorerShow(movie_id="m", movie_title="t", language="", event_code="E",
                                       venue_code="V", venue_name=name, area=area, locality=facts.locality,
                                       operator=facts.operator, theatre_type=facts.theatre_type,
                                       date_code="20261118", time_label="", minutes=600))
    assert where("PVR Superplex Inorbit", "LUXE, PXL, 4DX: Cyberabad") == "Cyberabad"
    assert where("Cinepolis", "Lulu Mall, Hyderabad") == "Hyderabad"
    assert where("Asian", "Secunderabad") == "Secunderabad"            # a whole word only
    assert where("INOX GVK One, Banjara Hills", "Hyderabad") == "Banjara Hills"   # city only → locality
    assert where("Some Hall", "") == "Hyderabad"                        # nothing given → the city
    assert where("Some Hall", "HYDERABAD.") == "Hyderabad"


def test_a_name_without_a_colon_is_kept_whole():
    assert split_venue_name("PVR Lakeshore PXL 4K Laser ATMOS DTS-X Y Junction") == (
        "PVR Lakeshore PXL 4K Laser ATMOS DTS-X Y Junction", "")
    assert split_venue_name("PVR: Atrium Gachibowli, Hyderabad") == ("PVR", "Atrium Gachibowli, Hyderabad")


def test_a_theatre_missing_from_the_venue_list_falls_back_safely():
    (only,) = live_shows(("PVTS",), venues=[])
    assert only.venue_name == "PVR"                       # the showtime's own name
    assert only.theatre_title == "PVR"                    # all BookMyShow gave; nothing added
    assert X._where(only) == "Hyderabad"                  # no area given → nothing invented
    nameless = SimpleNamespace(venues=[], showtimes=[Showtime("ZZZZ", "", "S1", "20261118", "", "1000", "2D",
                                                              Availability.AVAILABLE)])
    (show,) = S.shows_from_day(nameless, SCENE, "bookmyshow:ET00525065")
    assert show.venue_name == "ZZZZ"                      # the code, never a guess


def test_branches_keep_their_own_codes_groups_and_booking_links():
    shows = live_shows()
    assert [g.key for g in Q.group_by_theatre(shows)] == [s.venue_code for s in Q.sort_by_time(shows)]
    for s in shows:
        assert s.booking_url == ("https://in.bookmyshow.com/buytickets/scene-hyderabad/"
                                 f"cinema-hyde-{s.venue_code}-MT/20261118")
    assert len({s.booking_url for s in shows}) == len(shows)


def test_the_area_filter_still_works_on_localities():
    shows = live_shows()
    localities = {s.venue_code: s.locality for s in shows if s.locality != UNKNOWN}
    assert {k: localities.get(k) for k in ("PVTS", "PVTP", "PNNG", "PVYH")} == {
        "PVTS": "Gachibowli", "PVTP": "Gachibowli", "PNNG": "Panjagutta", "PVYH": "Panjagutta"}
    assert "Gachibowli" in Q.options(shows)["localities"]
    picked = Q.apply(shows, Q.Filters("20261118", localities=("Gachibowli",))).shows
    assert sorted(s.venue_code for s in picked) == ["PVTP", "PVTS"]
