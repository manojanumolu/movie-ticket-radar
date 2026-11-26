"""Admin-only monitor creation.

Only an account carrying the server-set ``admin`` custom claim may create a
new monitor. Ordinary accounts keep everything they already have: they can
still see, extend, re-check, stop and delete their own monitors, but cannot
create one, nor turn an old one into a different watch. Enforced in the store
(monitor/state.py) and in firestore.rules (tests/firestore_rules); the page
only explains it.

Every test in the suite runs with this restriction enforced; tests that need
monitors seed them with the admin claim, as production now does.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from config.timezone import now_ist
from monitor import policy
from monitor import state as state_mod
from monitor.models import ANY_FORMAT, Monitor, MonitorStatus, MovieRef, TheatreTarget
from monitor.state import Scope
from tests.test_monitor_limit import seeded_app  # noqa: F401 - pytest fixture

MEMBER, OTHER = "uid-member", "uid-other"


def _monitor(owner: str, *, title: str = "Film", venue: str = "ALLU") -> Monitor:
    at = now_ist()
    return Monitor(movie=MovieRef("bookmyshow", "ET00000001", title, "HYD", "hyderabad"),
                   targets=[TheatreTarget(venue, f"{venue} Cinemas", "Kokapet", ANY_FORMAT)],
                   interval_minutes=10, monitor_until=at + timedelta(days=1),
                   notify_email=f"{owner}@example.com", owner_uid=owner)


@pytest.fixture
def as_account(isolated_data):
    """Sign the store in as ``uid`` (JSON store scoped to that UID), admin or not."""
    def use(uid: str, *, admin: bool) -> None:
        state_mod.set_scope_provider(lambda: Scope("", uid, lambda: "", firestore_enabled=False,
                                                   admin=admin, email=f"{uid}@example.com"))
        state_mod.invalidate_cache()
    yield use
    state_mod.set_scope_provider(None)


def _ids() -> list[str]:
    return [m.id for m in state_mod.load_monitors()]


def _existing_member_monitor(as_account) -> Monitor:
    """A monitor the member owns, as one created before this change (or by the
    admin on the member's behalf) — written with the admin claim for the member's UID."""
    as_account(MEMBER, admin=True)
    monitor = _monitor(MEMBER)
    state_mod.upsert_monitor(monitor, mirror=False)
    as_account(MEMBER, admin=False)
    return monitor


# ── creation ──────────────────────────────────────────────────────────────
def test_an_ordinary_account_cannot_create_a_monitor_and_nothing_is_written(as_account):
    as_account(MEMBER, admin=False)
    with pytest.raises(policy.MonitorNotAllowed, match="set up by the TicketRadar admin"):
        state_mod.upsert_monitor(_monitor(MEMBER), mirror=False)
    assert _ids() == []


def test_an_admin_can_create_monitors(as_account):
    as_account(MEMBER, admin=True)
    first, second = _monitor(MEMBER), _monitor(MEMBER, title="Another")
    state_mod.upsert_monitor(first, mirror=False)
    state_mod.upsert_monitor(second, mirror=False)
    assert set(_ids()) == {first.id, second.id}


def test_the_page_question_matches_the_claim(as_account):
    as_account(MEMBER, admin=False)
    assert state_mod.may_create_monitors() is False
    as_account(MEMBER, admin=True)
    assert state_mod.may_create_monitors() is True


def test_the_worker_and_tools_are_not_users(isolated_data):
    """No signed-in scope (the worker, maintenance tools): not affected."""
    state_mod.set_scope_provider(None)
    state_mod.upsert_monitor(_monitor(MEMBER), mirror=False)
    assert len(state_mod.load_monitors()) == 1


def test_an_admin_claim_that_is_not_true_is_no_admin(as_account):
    as_account(MEMBER, admin=0)  # falsy, as a revoked or absent claim reads
    with pytest.raises(policy.MonitorNotAllowed):
        state_mod.upsert_monitor(_monitor(MEMBER), mirror=False)


# ── existing monitors stay the member's to manage ─────────────────────────
def test_a_member_still_sees_stops_extends_and_deletes_their_own_monitor(as_account):
    monitor = _existing_member_monitor(as_account)
    assert _ids() == [monitor.id]

    stopped = state_mod.stop_monitor(monitor.id, mirror=False)
    assert stopped.status is MonitorStatus.STOPPED

    extended = state_mod.extend_monitor(monitor.id, 24, mirror=False)
    assert extended.id == monitor.id and extended.status is MonitorStatus.ACTIVE

    again = state_mod.get_monitor(monitor.id)
    again.clear_problem()
    state_mod.upsert_monitor(again, mirror=False)         # the Retry path saves an existing monitor
    assert _ids() == [monitor.id]

    state_mod.delete_monitor(monitor.id, mirror=False)
    assert _ids() == []


@pytest.mark.parametrize("change", ["targets", "movie", "date_codes"])
def test_a_member_cannot_turn_an_existing_monitor_into_a_new_watch(as_account, change):
    monitor = _existing_member_monitor(as_account)
    edited = state_mod.get_monitor(monitor.id)
    if change == "targets":
        edited.targets = [TheatreTarget("AMB", "AMB Cinemas", "Gachibowli", ANY_FORMAT)]
    elif change == "movie":
        edited.movie = MovieRef("bookmyshow", "ET00000002", "Other film", "HYD", "hyderabad")
    else:
        edited.date_codes = ["20261217"]
    with pytest.raises(policy.MonitorNotAllowed):
        state_mod.upsert_monitor(edited, mirror=False)
    state_mod.invalidate_cache()
    stored = state_mod.get_monitor(monitor.id)
    assert [t.venue_code for t in stored.targets] == ["ALLU"]
    assert stored.movie.event_code == "ET00000001" and stored.date_codes == []


def test_an_admin_may_edit_a_watch(as_account):
    as_account(MEMBER, admin=True)
    monitor = _monitor(MEMBER)
    state_mod.upsert_monitor(monitor, mirror=False)
    monitor.targets = [TheatreTarget("AMB", "AMB Cinemas", "Gachibowli", ANY_FORMAT)]
    state_mod.upsert_monitor(monitor, mirror=False)
    assert state_mod.get_monitor(monitor.id).targets[0].venue_code == "AMB"


def test_one_account_still_cannot_see_anothers_monitors(as_account):
    _existing_member_monitor(as_account)
    as_account(OTHER, admin=False)
    assert _ids() == []


def test_no_one_is_made_an_admin_by_this_change():
    """The store reads the claim; nothing here can set it."""
    import inspect

    source = inspect.getsource(state_mod)
    assert "admin=True" not in source.replace("scope.admin", "")
    assert "set_custom_user_claims" not in source


# ── the page ──────────────────────────────────────────────────────────────
def test_the_wizard_explains_and_disables_start_for_members_only(isolated_data, signed_in, seeded_app):
    wizard = dict(step=5, location="hyderabad", movie_id=seeded_app.movie_id,
                  theatres=["ALLU"], formats={"ALLU": [ANY_FORMAT]})
    signed_in.admin = False
    app = seeded_app(**wizard)
    assert not app.exception
    assert next(b for b in app.button if b.key == "start").disabled is True
    assert any(state_mod.CREATE_MESSAGE in i.value for i in app.info)

    signed_in.admin = True
    app = seeded_app(**wizard)
    assert not app.exception
    assert next(b for b in app.button if b.key == "start").disabled is False
    assert not any(state_mod.CREATE_MESSAGE in i.value for i in app.info)
