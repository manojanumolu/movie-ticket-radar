"""Showtime Explorer — By theatre → Find a movie.

One film at one theatre, on one date, inside a time window: the actual
BookMyShow sessions, each language kept apart. The fixtures are
``tests.test_explorer_by_theatre``'s — AMB Cinemas, 5 November, Ramayana in
Hindi (a 2D event with a 3D sibling), Telugu and Kannada, parsed by the
production provider from the real response shape. They are a fixture, not a
captured response.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from datetime import time as dtime

from config.timezone import IST
from explorer import catalog
from explorer import query as Q
from explorer import source as S
from tests.test_explorer_by_theatre import (  # noqa: F401 - amb_page is a fixture
    AMB,
    ANSWERS,
    HINDI_2D,
    HINDI_3D,
    KANNADA_2D,
    NEWFILM,
    NOV5,
    NOV6,
    PVR,
    TELUGU_2D,
    UDTA,
    amb,
    amb_page,
    at_theatre,
    body,
    clocks,
    entries,
    ok,
    run,
    window,
)
from tests.test_showtime_explorer import FakeBMS, make_reader, payload, show


def ramayana_at_amb():
    films = catalog.films_by_title(catalog.films_at(entries(), "AMBH", NOV5))
    return next(f for f in films if f.title == "Ramayana")


def read_film(film=None, answers=None):
    film = film or ramayana_at_amb()
    bms = FakeBMS(dict(answers or ANSWERS))
    days = S.read_many(list(film.rows), NOV5, rd=make_reader(bms))
    return film, days, bms


def find(days, film, start=dtime(0), end=dtime(23, 59), **kw):
    return at_theatre(days, start=start, end=end, title_key=film.key, **kw)


# ── the film, its languages, and only its reads ──────────────────────────
def test_the_theatre_s_films_are_gathered_by_title_with_each_language_kept():
    films = catalog.films_by_title(catalog.films_at(entries(), "AMBH", NOV5))
    by = {f.title: f for f in films}
    assert set(by) == {"Ramayana", "Udta Teer", "Summer Holidays"}
    assert by["Ramayana"].languages == ("Hindi", "Telugu", "Kannada")
    assert [w.movie.event_code for w in by["Ramayana"].rows] == [HINDI_2D, TELUGU_2D, KANNADA_2D]
    assert by["Ramayana"].label == "Ramayana · Hindi, Telugu, Kannada"
    assert films[-1].title == "Summer Holidays" and not films[-1].listed      # not listed here: offered last
    assert catalog.title_key("  RAMAYANA ") == by["Ramayana"].key == "ramayana"


def test_finding_a_film_reads_only_that_film():
    _film, days, bms = read_film()
    read = {code for code, _date in bms.calls}
    assert read == {HINDI_2D, HINDI_3D, TELUGU_2D, KANNADA_2D}               # no Udta Teer, no Summer Holidays
    assert UDTA not in read and NEWFILM not in read
    assert all(d.origin == S.LIVE for d in days)


def test_each_language_is_its_own_group_with_every_show_in_time_order():
    film, days, _ = read_film()
    out = find(days, film)
    groups = Q.group_by_language(out.shows)
    assert [g.title for g in groups] == ["Hindi", "Telugu", "Kannada"]       # by each language's first show (8:59, 9:00, 10:00)
    times = {g.title: [" ".join(s.display_time) for s in g.shows] for g in groups}
    assert times["Telugu"] == ["9:00 AM", "12:00 PM", "3:30 PM"]               # many shows, one language
    assert times["Kannada"] == ["10:00 AM"]
    assert times["Hindi"] == ["8:59 AM", "11:15 AM", "11:20 AM", "11:30 AM", "12:01 PM", "7:00 PM"]   # 2D and 3D together
    assert all(s.venue_code == "AMBH" and s.movie_title == "Ramayana" for s in out.shows)


def test_hindi_2d_and_3d_are_one_language_two_formats_never_duplicates():
    film, days, _ = read_film()
    hindi = [s for s in find(days, film).shows if s.language == "Hindi"]
    assert {s.dimension for s in hindi} == {"2D", "3D"}
    assert len({s.session_id for s in hindi}) == len(hindi)                  # each a different session


def test_the_language_filter_narrows_and_none_means_all():
    film, days, _ = read_film()
    assert {s.language for s in find(days, film).shows} == {"Hindi", "Telugu", "Kannada"}
    assert {s.language for s in find(days, film, languages=("Telugu",)).shows} == {"Telugu"}


# ── date, window and boundaries ──────────────────────────────────────────
def test_only_the_chosen_date():
    film, days, _ = read_film()
    out = find(days, film)
    assert all(s.date_code == NOV5 for s in out.shows)
    assert out.wrong_date >= 1                                                # the 6 Nov 9:00 AM show is left out


def test_the_window_is_inclusive_at_both_ends():
    film, days, _ = read_film()
    assert clocks(find(days, film, dtime(11, 20), dtime(11, 30), languages=("Hindi",))) == ["11:20 AM", "11:30 AM"]
    assert clocks(find(days, film, dtime(11, 21), dtime(11, 29), languages=("Hindi",))) == []
    out = find(days, film, dtime(10), dtime(22))
    assert all(600 <= s.minutes <= 1320 for s in out.shows)                  # nothing outside, ever
    assert "8:59 AM" not in clocks(out)


def test_an_end_before_the_start_is_refused_not_guessed():
    import pytest

    with pytest.raises(Q.WindowError):
        Q.validate_window(dtime(22), dtime(10))


def test_times_are_india_standard_time_and_today_hides_begun_shows():
    film, days, _ = read_film()
    show0 = find(days, film).shows[0]
    assert show0.starts_at.tzinfo is IST
    out = find(days, film, hide_started_before=datetime(2026, 11, 5, 11, 0, tzinfo=IST))
    assert all(s.minutes >= 660 for s in out.shows) and out.already_started > 0


# ── identity: this theatre, this film ────────────────────────────────────
def test_other_theatres_and_other_films_never_appear():
    film, days, _ = read_film()
    out = find(days, film)
    assert "PVFS" not in {s.venue_code for s in out.shows}                   # the city-wide read's other theatres
    udta = catalog.films_by_title(catalog.films_at(entries(), "AMBH", NOV5))
    udta = next(f for f in udta if f.title == "Udta Teer")
    _, udta_days, _ = read_film(udta)
    assert find(udta_days + days, film).shows == find(days, film).shows      # a mixed read is still one film


def test_a_similar_title_is_not_the_same_film():
    film = ramayana_at_amb()
    fake = Q.ExplorerShow(movie_id="x", movie_title="Ramayana: The Legend", language="Hindi", event_code="E",
                          venue_code="AMBH", venue_name="AMB Cinemas", area="", locality="", operator="",
                          theatre_type="", date_code=NOV5, time_label="10:00 AM", minutes=600)
    out = Q.apply([fake], Q.Filters(NOV5, venue_code="AMBH", title_key=film.key))
    assert out.shows == []


# ── messy data ───────────────────────────────────────────────────────────
def test_a_row_with_no_language_is_kept_apart_not_guessed():
    film = ramayana_at_amb()
    blank = replace(film.rows[1], movie=replace(film.rows[1].movie, language=""))
    film = replace(film, rows=(film.rows[0], blank, film.rows[2]))
    _, days, _ = read_film(film)
    groups = Q.group_by_language(find(days, film).shows)
    assert Q.NO_LANGUAGE in [g.title for g in groups]
    assert "Telugu" not in [g.title for g in groups]                         # never inferred from the title


def test_the_same_session_read_twice_is_one_show():
    dup = dict(ANSWERS)
    dup[KANNADA_2D] = payload([amb("10:00 AM", "1000", "M B LUXE"), amb("10:00 AM", "1000", "M B LUXE")], date=NOV5)
    film, days, _ = read_film(answers=dup)
    out = find(days, film, languages=("Kannada",))
    assert clocks(out) == ["10:00 AM"] and out.duplicates >= 1


def test_an_unreadable_time_is_left_out_and_counted():
    bad = dict(ANSWERS)
    bad[KANNADA_2D] = payload([amb("soon", "", "M B LUXE"), amb("10:00 AM", "1000", "M B LUXE")], date=NOV5)
    film, days, _ = read_film(answers=bad)
    out = find(days, film, languages=("Kannada",))
    assert clocks(out) == ["10:00 AM"] and out.unreadable_time == 1


def test_booking_links_are_bookmyshow_https_or_absent():
    film, days, _ = read_film()
    for s in find(days, film).shows:
        assert s.booking_url == "" or s.booking_url.startswith("https://in.bookmyshow.com/")


def test_a_failed_read_is_a_failure_not_an_empty_film():
    failing = {code: 403 for code in ANSWERS}
    _film, days, _ = read_film(answers=failing)
    assert all(d.origin == S.FAILED for d in days)


# ── the page ─────────────────────────────────────────────────────────────
def at_amb_film(app, film="Ramayana · Hindi, Telugu, Kannada"):
    app = ok(app.button(key="sxmode_theatre").click().run())
    app = ok(app.selectbox(key="sx_theatre").select("AMB Cinemas · Gachibowli").run())
    return ok(app.selectbox(key="sx_film").select(film).run())


def test_page_find_a_movie_sits_under_the_theatre_and_shows_its_languages(amb_page):
    bms, _ = amb_page
    app = ok(run())
    assert not [b for b in app.selectbox if b.key == "sx_film"]               # not in By movie
    app = ok(app.button(key="sxmode_theatre").click().run())
    assert not [b for b in app.selectbox if b.key == "sx_film"]               # not before a theatre
    app = ok(app.selectbox(key="sx_theatre").select("AMB Cinemas · Gachibowli").run())
    box = app.selectbox(key="sx_film")
    assert box.value is None and "Ramayana · Hindi, Telugu, Kannada" in box.options
    before = len(bms.calls)
    app = ok(box.select("Ramayana · Hindi, Telugu, Kannada").run())
    html = body(app)
    assert "At AMB Cinemas" in html and "3 languages" in html
    assert app.session_state["sx_view"] == "group"                            # grouped by language
    assert "Selected" in [b.label for b in app.button if b.key == "sxview_group"] or "By language" in html
    for lang in ("Telugu", "Kannada", "Hindi"):
        assert f'<div class="n">{lang}</div>' in html
    new = {code for code, _d in bms.calls[before:]}
    assert new <= {HINDI_2D, HINDI_3D, TELUGU_2D, KANNADA_2D}                 # only the film's events
    assert "Udta Teer" not in html.split("showtime")[-1]


def test_page_language_filter_and_window(amb_page):
    app = at_amb_film(ok(run()))
    app = ok(app.multiselect(key="sx_lang").select("Telugu").run())
    html = body(app)
    assert '<div class="n">Telugu</div>' in html and '<div class="n">Hindi</div>' not in html
    app = window(app, dtime(10), dtime(22))
    html = body(app)
    assert "12:00" in html and "3:30" in html and "9:00 AM" not in html.split("showtime")[-1]


def test_page_the_film_survives_a_date_change_and_says_when_it_isn_t_playing(amb_page):
    app = at_amb_film(ok(run()))
    app = ok(app.button(key="sxdate_20261106").click().run())
    assert app.session_state["sx_film"] == "Ramayana · Hindi, Telugu, Kannada"
    html = body(app)
    assert "Ramayana isn't playing here on this date" in html or ">1</b> showtime" in html


def test_page_not_playing_here_is_not_a_failure(amb_page):
    bms, _ = amb_page
    for code in (HINDI_2D, HINDI_3D, TELUGU_2D, KANNADA_2D):
        bms.answers[code] = payload([show(*PVR, "11:00 AM", "1100", date=NOV5)], date=NOV5)
    app = at_amb_film(ok(run()))
    html = body(app)
    assert "Ramayana isn't playing here on this date" in html
    assert "couldn't be read" not in html


def test_page_a_provider_failure_is_said_plainly(amb_page):
    bms, _ = amb_page
    for code in list(bms.answers):
        bms.answers[code] = 403
    app = at_amb_film(ok(run()))
    html = body(app)
    assert "BookMyShow couldn't be read" in html
    assert "isn't playing here" not in html


def test_page_clearing_the_film_brings_every_film_back(amb_page):
    app = at_amb_film(ok(run()))
    app = ok(app.selectbox(key="sx_film").set_value(None).run())
    html = body(app)
    assert "At AMB Cinemas" not in html and "Udta Teer" in html


def test_dates_are_the_explorers_own_india_dates():
    from ui import explorer as ux

    codes = ux.date_codes(datetime(2026, 11, 5, 23, 30, tzinfo=IST))
    assert codes[0] == NOV5 and codes[1] == NOV6
