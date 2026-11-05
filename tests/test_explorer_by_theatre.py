"""Showtime Explorer — By theatre, traced end to end, and its security edges.

The scenario is the one reported from production: AMB Cinemas, Gachibowli,
5 November, Hindi. BookMyShow lists Ramayana's Hindi 2D and Hindi 3D as two
events (the 3D is a premium sibling of the 2D row); the reported shows in
09:00–12:00 were 11:15, 11:20 and 11:30 AM.

The payloads are ``tests.conftest.build_payload`` — the widget shape of the real
showtimes-by-event response — parsed by the production provider. Venue codes,
event codes and screen names are the real catalogue's (``data/catalogue.json``,
Nov 2026); the times are the reported ones plus a few around them. They are a
fixture, not a captured response.
"""

from __future__ import annotations

from datetime import datetime, time as dtime

import pytest

from config import store
from config.timezone import IST
from explorer import catalog, query as Q, source as S
from monitor import catalogue
from platforms.bookmyshow import BookMyShowProvider
from tests.conftest import APP_SCRIPT, build_payload
from tests.test_showtime_explorer import FakeBMS, _entry, make_reader, payload, show

NOV5, NOV6 = "20261105", "20261106"
AMB = ("AMBH", "AMB Cinemas", "Gachibowli")
LULU = ("CPMH", "Cinepolis", "Lulu Mall, Hyderabad")
MANTRA = ("CMMA", "Cinepolis", "Mantra Mall, Attapur")
PVR = ("PVFS", "PVR", "Nexus Mall Kukatpally, Hyderabad")

HINDI_2D, HINDI_3D = "ET00451914", "ET00493076"
TELUGU_2D, KANNADA_2D, UDTA, NEWFILM = "ET00518312", "ET00518311", "ET00519000", "ET00519999"


def amb(time, tcode, fmt, date=NOV5, status="3"):
    return show(*AMB, time, tcode, date=date, fmt=fmt, status=status)


#: Ramayana Hindi, base (2D) event: screen names only, as BookMyShow sends them.
HINDI_2D_SHOWS = [
    amb("11:15 AM", "1115", "HDR BY BARCO"),
    amb("07:00 PM", "1900", "BARCO FLAGSHIP LASER DOLBY ATMOS"),
    amb("09:00 AM", "0900", "HDR BY BARCO", date=NOV6),             # another day
    show(*PVR, "09:30 AM", "0930", date=NOV5, fmt="4K"),            # another theatre
    show(*LULU, "10:00 AM", "1000", date=NOV5, fmt="2D"),
]
#: Ramayana Hindi 3D — a separate BookMyShow event, the 2D row's sibling.
HINDI_3D_SHOWS = [
    amb("11:20 AM", "1120", "M B LUXE"),
    amb("11:30 AM", "1130", "VIP SCREEN"),
    amb("12:01 PM", "1201", "M B LUXE"),                             # just past 12:00
    amb("08:59 AM", "0859", "VIP SCREEN"),                           # just before 09:00
    show(*MANTRA, "11:45 AM", "1145", date=NOV5, fmt="3D"),
]
TELUGU_SHOWS = [amb("09:00 AM", "0900", "M B LUXE"), amb("12:00 PM", "1200", "HDR BY BARCO"),
                amb("03:30 PM", "1530", "HDR BY BARCO")]
KANNADA_SHOWS = [amb("10:00 AM", "1000", "M B LUXE")]
UDTA_SHOWS = [amb("06:45 PM", "1845", "VIP SCREEN")]
NEW_SHOWS = [amb("10:30 AM", "1030", "HDR BY BARCO")]   # not in the last sync's listing for AMB


def entries():
    def e(code, title, language, venues, listings, variants=(), dimension="2D"):
        row = _entry(code, title, language, venues, listings, variants=variants)
        row["dimension"] = dimension
        return row

    at_amb = {NOV5: {"AMBH": []}}
    return [
        e(HINDI_2D, "Ramayana", "Hindi", [AMB, PVR, LULU, MANTRA],
          {NOV5: {"AMBH": [], "PVFS": [], "CPMH": [], "CMMA": []}},
          variants=((HINDI_3D, "3D"),)),
        e(TELUGU_2D, "Ramayana", "Telugu", [AMB], at_amb),
        e(KANNADA_2D, "Ramayana", "Kannada", [AMB], at_amb),
        e(UDTA, "Udta Teer", "Hindi", [AMB], at_amb),
        # played AMB before; the last sync's listing for 5 Nov did not have it there
        e(NEWFILM, "Summer Holidays", "Telugu", [AMB], {NOV5: {"PVFS": []}}),
    ]


ANSWERS = {HINDI_2D: payload(HINDI_2D_SHOWS, date=NOV5), HINDI_3D: payload(HINDI_3D_SHOWS, date=NOV5),
           TELUGU_2D: payload(TELUGU_SHOWS, date=NOV5), KANNADA_2D: payload(KANNADA_SHOWS, date=NOV5),
           UDTA: payload(UDTA_SHOWS, date=NOV5), NEWFILM: payload(NEW_SHOWS, date=NOV5)}


def read_theatre(venue="AMBH", date=NOV5, languages=(), answers=None, rd=None):
    films = [w for w in catalog.films_at(entries(), venue, date)
             if not languages or w.movie.language in languages]
    rd = rd or make_reader(FakeBMS(dict(answers or ANSWERS)))
    return S.read_many(films, date, rd=rd), rd


def at_theatre(days, venue="AMBH", date=NOV5, start=dtime(0), end=dtime(23, 59), **filters):
    shows = [s for d in days for s in d.shows]
    return Q.apply(shows, Q.Filters(date, start, end, venue_code=venue, **filters))


def clocks(out):
    return [" ".join(s.display_time) for s in out.shows]


# ── the reported case ────────────────────────────────────────────────────
def test_1_hindi_9_to_10_is_genuinely_empty():
    days, _ = read_theatre(languages=("Hindi",))
    out = at_theatre(days, start=dtime(9), end=dtime(10))
    assert out.shows == []
    assert out.outside_window > 0                      # the shows exist, just not in this hour


def test_2_hindi_9_to_12_is_exactly_the_three_bookmyshow_lists():
    days, _ = read_theatre(languages=("Hindi",))
    out = at_theatre(days, start=dtime(9), end=dtime(12))
    assert clocks(out) == ["11:15 AM", "11:20 AM", "11:30 AM"]
    assert {s.venue_code for s in out.shows} == {"AMBH"} and {s.movie_title for s in out.shows} == {"Ramayana"}


def test_3_all_day_at_the_theatre():
    days, _ = read_theatre()
    out = at_theatre(days)
    assert clocks(out) == ["8:59 AM", "9:00 AM", "10:00 AM", "10:30 AM", "11:15 AM", "11:20 AM", "11:30 AM",
                           "12:00 PM", "12:01 PM", "3:30 PM", "6:45 PM", "7:00 PM"]
    assert all(s.venue_code == "AMBH" and s.date_code == NOV5 for s in out.shows)


def test_4_hindi_2d_and_3d_are_told_apart_and_filterable():
    days, _ = read_theatre(languages=("Hindi",))
    ram = [s for s in at_theatre(days).shows if s.movie_title == "Ramayana"]
    fmt = {" ".join(s.display_time): s.formats for s in ram}
    assert fmt["11:15 AM"] == ("2D", "HDR By Barco")       # the base event: its listing's dimension, then the screen
    assert fmt["11:20 AM"] == ("3D", "M B LUXE")           # the sibling event's own format
    # Hindi 2D: Ramayana's base event and Udta Teer (also a 2D row)
    assert clocks(at_theatre(days, formats=("2D",), languages=("Hindi",))) == ["11:15 AM", "6:45 PM", "7:00 PM"]
    assert clocks(at_theatre(days, formats=("3D",))) == ["8:59 AM", "11:20 AM", "11:30 AM", "12:01 PM"]
    assert "2D" in Q.options(ram)["formats"] and "3D" in Q.options(ram)["formats"]


def test_a_row_with_no_known_dimension_is_never_called_2d():
    rows = entries()
    rows[0].pop("dimension")
    films = [w for w in catalog.films_at(rows, "AMBH", NOV5) if w.movie.event_code == HINDI_2D]
    day = S.read_one(films[0], NOV5, rd=make_reader(FakeBMS(dict(ANSWERS))))
    base = [s for s in day.shows if s.venue_code == "AMBH" and s.time_label == "11:15 AM"][0]
    assert base.formats == ("HDR By Barco",)


def test_5_telugu_and_kannada():
    days, _ = read_theatre(languages=("Telugu",))
    assert clocks(at_theatre(days)) == ["9:00 AM", "10:30 AM", "12:00 PM", "3:30 PM"]
    days, _ = read_theatre(languages=("Kannada",))
    assert clocks(at_theatre(days)) == ["10:00 AM"]


def test_6_and_7_many_films_and_many_shows_group_by_film():
    days, _ = read_theatre()
    groups = Q.group_by_movie(at_theatre(days).shows)
    by = {(g.title, g.subtitle): [" ".join(s.display_time) for s in g.shows] for g in groups}
    assert by[("Ramayana", "Hindi")] == ["8:59 AM", "11:15 AM", "11:20 AM", "11:30 AM", "12:01 PM", "7:00 PM"]
    assert by[("Ramayana", "Telugu")] == ["9:00 AM", "12:00 PM", "3:30 PM"]
    assert ("Udta Teer", "Hindi") in by and ("Summer Holidays", "Telugu") in by


def test_8_theatres_with_the_same_name_stay_apart():
    th = {t.code: t for t in catalog.theatres(entries())}
    assert th["CPMH"].label != th["CMMA"].label                  # both "Cinepolis", different areas
    days, _ = read_theatre(venue="CPMH")
    assert clocks(at_theatre(days, venue="CPMH")) == ["10:00 AM"]
    days, _ = read_theatre(venue="CMMA")
    assert clocks(at_theatre(days, venue="CMMA")) == ["11:45 AM"]


def test_9_both_ends_of_the_window_are_inclusive():
    days, _ = read_theatre()
    out = at_theatre(days, start=dtime(9), end=dtime(12))
    got = clocks(out)
    assert "9:00 AM" in got and "12:00 PM" in got              # exactly on From and on To
    assert "8:59 AM" not in got and "12:01 PM" not in got       # a minute outside either end


def test_a_film_the_theatre_added_since_the_sync_is_still_found():
    films = catalog.films_at(entries(), "AMBH", NOV5)
    assert [w.movie.event_code for w in films if not w.listed] == [NEWFILM]
    days, _ = read_theatre()
    assert "10:30 AM" in clocks(at_theatre(days))


def test_the_sync_records_each_rows_dimension_and_keeps_it(provider_factory, monkeypatch):
    from tests.conftest import QUICKBOOK_HYD, ALLU_LIVE, hyd_detail

    provider = provider_factory([QUICKBOOK_HYD] + hyd_detail(build_payload(ALLU_LIVE)))
    monkeypatch.setattr(catalogue, "get_provider", lambda slug: provider)
    catalogue.sync_region("hyderabad", mirror=False, detail=True)
    rows = catalogue.list_entries("hyderabad")
    assert rows and all(r.get("dimension") == "2D" for r in rows)      # the listing says 2D for each
    assert all(r.get("resolved_at") for r in rows)                       # …and detail did not drop it


def test_the_provider_reads_the_dimension_from_the_city_listing():
    from tests.conftest import QUICKBOOK_HYD

    class One:
        def get(self, url, headers=None, params=None, timeout=20):
            class R:
                status_code = 200

                def json(self):
                    return QUICKBOOK_HYD
            return R()

    p = BookMyShowProvider(session=One(), sleeper=lambda s: None)
    movies = p.list_movies("hyderabad")
    assert movies and all(p.base_dimensions.get(m.event_code) == "2D" for m in movies)


# ── empty is not failed, and failures never leak internals ───────────────
def test_10_one_failed_film_makes_the_result_incomplete_not_empty():
    answers = dict(ANSWERS)
    answers[HINDI_3D] = 503       # a premium sibling fails: partial
    answers[UDTA] = 403           # a whole film fails
    days, _ = read_theatre(languages=("Hindi",), answers=answers)
    failed = [d for d in days if d.origin == S.FAILED]
    # a refusal pauses live reads, so other films read after it may fail the same way
    assert failed and all("refused" in d.error for d in failed)
    assert any(d.missing for d in days) or len(failed) == len(days)


def test_internal_error_text_never_reaches_the_page():
    class Proxy:
        def get(self, *a, **k):
            import requests

            raise requests.ConnectionError("HTTPSConnectionPool(host='10.12.0.7', port=3128): proxy auth abc123")

    w = catalog.films_at(entries(), "AMBH", NOV5)[0]
    day = S.read_one(w, NOV5, rd=make_reader(Proxy()))
    assert day.origin == S.FAILED and day.error == "BookMyShow couldn't be reached"
    assert "10.12" not in day.error and "abc123" not in day.error


# ── the page ─────────────────────────────────────────────────────────────
AppTest = pytest.importorskip("streamlit.testing.v1").AppTest


@pytest.fixture
def amb_page(monkeypatch):
    """The page on 5 Nov at 08:00 IST, with the AMB catalogue and BookMyShow above."""
    from ui import explorer as ux

    store.save_catalogue({"movies": entries(), "updated_at": "2026-11-05T01:55:00+05:30",
                          "sync": {"hyderabad": {"status": "OK", "message": "", "movie_count": 5,
                                                 "at": "2026-11-05T01:55:00+05:30"}}}, mirror=False)
    monkeypatch.setattr(ux, "now_ist", lambda: datetime(2026, 11, 5, 8, 0, tzinfo=IST))
    bms = FakeBMS(dict(ANSWERS))
    rd = make_reader(bms)
    S.install(rd)
    return bms, rd


def run(**session):
    app = AppTest.from_file(APP_SCRIPT, default_timeout=60)
    app.session_state["page"] = "Showtime Explorer"
    for k, v in session.items():
        app.session_state[k] = v
    return app.run()


def ok(app):
    assert not app.exception, [str(e) for e in app.exception]
    return app


def body(app) -> str:
    from html import unescape

    return unescape(" ".join(m.value for m in app.markdown if "<style>" not in m.value[:400]))


def at_amb(app, language="Hindi"):
    app = ok(app.button(key="sxmode_theatre").click().run())
    app = ok(app.selectbox(key="sx_theatre").select("AMB Cinemas · Gachibowli").run())
    if language:
        app = ok(app.multiselect(key="sx_lang").select(language).run())
    return app


def window(app, start, end):
    app.time_input(key="sx_from").set_value(start).run()
    return ok(app.time_input(key="sx_to").set_value(end).run())


def test_page_the_reported_case(amb_page):
    app = at_amb(ok(run()))
    app = window(app, dtime(9), dtime(10))
    html = body(app)
    assert "No shows match" in html and "listed for Today 5 Nov" in html     # zero, and why
    app = window(app, dtime(9), dtime(12))
    html = body(app)
    assert ">3</b> showtimes" in html
    assert html.index("11:15") < html.index("11:20") < html.index("11:30")
    assert "8:59" not in html and "12:01" not in html


def test_page_a_theatre_with_nothing_that_day_says_so_not_widen_the_window(amb_page):
    bms, _ = amb_page
    # every film AMB plays answers with shows only at other theatres
    for code in list(bms.answers):
        bms.answers[code] = payload([show(*PVR, "11:00 AM", "1100", date=NOV5)], date=NOV5)
    app = at_amb(ok(run()), language=None)
    html = body(app)
    assert "Nothing listed for this date" in html and "AMB Cinemas lists no shows" in html
    assert "Widen the window" not in html


def test_page_incomplete_and_failed_are_not_empty(amb_page):
    bms, _ = amb_page
    bms.answers[UDTA] = 403
    app = at_amb(ok(run()))
    app = window(app, dtime(9), dtime(10))
    html = body(app)
    assert "Results may be incomplete" in html and "1 of 2 films" in html and "No shows match" not in html
    for code in list(bms.answers):
        bms.answers[code] = 403
    app = ok(app.button(key="sx_refresh").click().run())
    S.reader().clear()
    app = ok(app.button(key="sx_refresh").click().run())
    html = body(app)
    assert "BookMyShow couldn't be read" in html or "Results may be incomplete" in html
    assert "Live from BookMyShow" not in html


def test_page_11_changing_theatre_date_or_language_reads_again_but_the_window_does_not(amb_page):
    bms, rd = amb_page
    app = at_amb(ok(run()))
    calls = len(bms.calls)
    app = window(app, dtime(9), dtime(12))
    assert len(bms.calls) == calls                                   # the window is a filter, never a request
    app = ok(app.multiselect(key="sx_lang").unselect("Hindi").select("Telugu").run())
    html = body(app)
    assert "9:00" in html and "12:00" in html and "11:15" not in html  # Telugu's shows, not Hindi's
    app = ok(app.button(key="sxdate_20261106").click().run())
    assert (TELUGU_2D, NOV6) in bms.calls                            # a new date: read for that date
    app = ok(app.button(key="sxdate_20261105").click().run())
    app = ok(app.selectbox(key="sx_theatre").select("Cinepolis · Lulu Mall, Hyderabad").run())
    app = ok(app.multiselect(key="sx_lang").select("Hindi").run()) if not app.session_state["sx_lang"] else app
    html = body(app)
    assert "10:00" in html and "11:15" not in html                   # Lulu's Hindi show, none of AMB's
    calls = len(bms.calls)
    S.reader().clear()                                               # past the refresh cooldown
    ok(app.button(key="sx_refresh").click().run())
    assert len(bms.calls) > calls                                    # Refresh asks BookMyShow again


def test_page_12_switching_modes_never_shows_the_other_modes_results(amb_page):
    app = ok(run())
    box = app.selectbox(key="sx_movie")
    app = ok(box.select(next(o for o in box.options if o.startswith("Ramayana"))).run())
    assert "Nexus Mall" in body(app) or "Kukatpally" in body(app)    # By movie: other theatres
    app = at_amb(app, language=None)
    html = body(app)
    assert "Kukatpally" not in html and "Lulu" not in html.split("Search theatres")[-1].split("showtimes")[-1]
    app = ok(app.button(key="sxmode_movie").click().run())
    assert "Kukatpally" in body(app)                                  # back: the movie's own results


def test_page_escapes_whatever_a_theatre_or_film_is_called(amb_page):
    """Theatre names come from BookMyShow's response; By movie draws them on every row."""
    bms, _ = amb_page
    evil = '<img src=x onerror="alert(1)">'
    bms.answers[HINDI_2D] = payload([show("AMBH", evil, "Gachibowli", "11:15 AM", "1115", date=NOV5)], date=NOV5)
    app = ok(run())
    box = app.selectbox(key="sx_movie")
    app = ok(box.select(next(o for o in box.options if o.startswith("Ramayana"))).run())
    raw = " ".join(m.value for m in app.markdown)
    assert "11:15" in raw                                  # the row is drawn…
    assert evil not in raw and "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in raw   # …escaped


def test_page_needs_a_signed_in_account(monkeypatch, amb_page):
    from auth import session

    monkeypatch.setattr(session, "restore", lambda: None)
    app = ok(run())
    html = body(app)
    assert "Welcome back." in html and "Showtime Explorer" not in html and "AMB Cinemas" not in html


def test_posters_only_from_bookmyshows_cdn():
    assert S.safe_poster("https://in.bmscdn.com/events/moviecard/x.jpg")
    for bad in ("http://in.bmscdn.com/x.jpg", "https://evil.example/x.jpg", "javascript:alert(1)",
                "https://in.bmscdn.com.evil.example/x.jpg", ""):
        assert S.safe_poster(bad) == ""


def test_the_shared_cache_holds_only_public_listings():
    """The process-wide cache is keyed by (event, date) and holds what BookMyShow
    published — nothing about who asked."""
    rd = make_reader(FakeBMS(dict(ANSWERS)))
    S.read_one(catalog.films_at(entries(), "AMBH", NOV5)[0], NOV5, rd=rd)
    assert list(rd._cache) == [(HINDI_2D, NOV5)]
    blob = repr(rd._cache)
    assert "uid" not in blob and "@" not in blob and "token" not in blob.lower()


# ── the atmosphere: Home's radar, reused; nothing new drawn ──────────────
def test_the_explorer_reuses_homes_radar_and_draws_no_reel_or_strip(amb_page):
    from ui import explorer as ux
    from ui import radar

    app = ok(run())
    raw = " ".join(m.value for m in app.markdown)
    assert 'class="tr-sx-ambience"' in raw and 'class="tr-sx-hero-radar"' in raw
    assert raw.count('class="tr-radar ') >= 2                    # the same ui.radar mark, twice
    import re

    # the page itself (the sidebar's existing art is not the Explorer's), stylesheets aside
    page_markup = re.sub(r"<style>.*?</style>", "", " ".join(m.value for m in app.main.markdown), flags=re.S)
    assert "tr-sx-ambience" in page_markup
    assert "tr-reel" not in page_markup and "tr-strip" not in page_markup
    assert radar.CSS in ux.AMBIENCE_CSS                            # radar's own rules and keyframes, not a copy


def test_the_atmosphere_is_cheap_quiet_and_out_of_the_way():
    from ui import explorer as ux

    css = ux.AMBIENCE_CSS
    own = css.replace(__import__("ui.radar", fromlist=["CSS"]).CSS, "")
    assert "blur(" not in own                                      # the Explorer's own rules: no blur filters
    assert "pointer-events:none" in css                            # never takes a click, a scroll or a focus
    assert "@media (prefers-reduced-motion: reduce)" in css
    reduced = css[css.index("@media (prefers-reduced-motion: reduce)"):]
    assert ".tr-sx-hero-radar .scan" in reduced and "animation:none" in reduced
    new = css.replace(__import__("ui.radar", fromlist=["CSS"]).CSS, "")
    animated = __import__("re").findall(r"@keyframes [^{]+\{(.*?)\}\s*\}", new, __import__("re").S)
    for block in animated:
        props = {d.split(":")[0].strip() for part in block.split("{")[1:] for d in part.split(";") if ":" in d}
        assert props <= {"transform", "opacity"}, props


def test_home_login_radar_and_auth_are_untouched():
    """The Explorer's work changes none of these. (Home itself changed once, on
    purpose: its big reel's glow became a static halo — see
    tests/test_sidebar_ambience.py — and its radar is the same code.)"""
    import subprocess

    changed = subprocess.run(["git", "diff", "--name-only", "b37d93d", "--", "ui/radar.py",
                              "ui/login.py", "ui/crafts.py", "auth/"], capture_output=True, text=True).stdout
    assert changed.strip() == ""
