"""A stored monitor never carries more than 12 sibling events.

Discovery (``monitor.discovery``) merges sibling events the city listing names
onto running monitors and persists them. Until Phase 7 the merge had no limit,
while the app, the worker's screen (``policy.shape_problem``) and the Phase 5
Firestore rules all allow at most ``policy.MAX_VARIANTS`` (12). A thirteenth
sibling therefore turned a healthy monitor into one the worker refuses to
check at all, and — under the Phase 5 rules — one its owner could not stop.

The cap keeps every sibling a monitor already has, adds new ones in the
listing's order, and stops at 12. Anything past it is logged and reported
(``DiscoveryReport.capped``), never stored; the checker still sweeps it once
the catalogue records it (``checker.with_current_variants``).
"""

from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path

import pytest

from config.timezone import now_ist
from monitor import discovery, policy
from monitor.discovery import DiscoveryReport, merge_variants
from monitor.models import ANY_FORMAT, Monitor, MovieRef, TheatreTarget

OWN = "ET00000001"
LIMIT = policy.MAX_VARIANTS


def code(i: int) -> str:
    return f"ET9{i:07d}"


def sibs(start: int, n: int, label: str = "4DX 3D") -> tuple[tuple[str, str], ...]:
    return tuple((code(i), label) for i in range(start, start + n))


def movie(variants=()) -> MovieRef:
    return MovieRef(platform="bookmyshow", event_code=OWN, title="Film", region_code="HYD",
                    region_slug="hyderabad", city="Hyderabad", language="Telugu", variants=variants)


def monitor(variants=()) -> Monitor:
    return Monitor(movie=movie(variants), targets=[TheatreTarget("ALLU", "ALLU Cinemas", "Kokapet", ANY_FORMAT)],
                   interval_minutes=10, monitor_until=now_ist() + timedelta(days=1),
                   notify_email="owner@example.com", owner_uid="uid-owner")


def merge_from_listing(m: Monitor, family) -> DiscoveryReport:
    """Run discovery's merge step for one monitor against a listing row."""
    report = DiscoveryReport()
    listed = [MovieRef(platform="bookmyshow", event_code=OWN, title="Film", region_code="HYD",
                       region_slug="hyderabad", city="Hyderabad", language="Telugu", variants=tuple(family))]
    discovery._merge_into(m, listed, report)
    return report


# ── merge_variants ───────────────────────────────────────────────────────
@pytest.mark.parametrize("n", [0, 1, 11, 12])
def test_up_to_the_limit_every_sibling_is_kept_in_order(n):
    assert merge_variants((), sibs(0, n), own_code=OWN) == sibs(0, n)


@pytest.mark.parametrize("n", [13, 50, 500])
def test_past_the_limit_the_first_twelve_are_kept(n):
    out = merge_variants((), sibs(0, n), own_code=OWN)
    assert out == sibs(0, LIMIT) and len(out) == LIMIT


def test_known_siblings_are_never_displaced_by_new_ones():
    known = sibs(100, 10)
    out = merge_variants(known, sibs(0, 20), own_code=OWN)
    assert out[:10] == known                       # all ten known, first, in their order
    assert out[10:] == sibs(0, 2) and len(out) == LIMIT


def test_a_monitor_already_at_the_limit_gains_nothing():
    known = sibs(100, LIMIT)
    assert merge_variants(known, sibs(0, 5), own_code=OWN) == known


def test_a_list_already_over_the_limit_is_cut_back_to_its_first_twelve():
    assert merge_variants(sibs(100, 20), (), own_code=OWN) == sibs(100, LIMIT)


def test_repeated_merging_is_stable_and_never_grows_past_the_limit():
    stored: tuple = ()
    for start in range(0, 200, 7):                 # a listing that keeps naming new events
        stored = merge_variants(stored, sibs(start, 9), own_code=OWN)
        assert len(stored) <= LIMIT
    assert stored == sibs(0, LIMIT)
    assert merge_variants(stored, sibs(0, 200), own_code=OWN) == stored   # idempotent


def test_the_primary_event_duplicates_and_empty_codes_are_never_stored():
    family = ((OWN, "2D"), (code(1), ""), (code(1), "IMAX 2D"), ("", "x"), (code(2), "3D"))
    out = merge_variants(((code(1), ""),), family, own_code=OWN)
    assert out == ((code(1), "IMAX 2D"), (code(2), "3D"))      # an empty label is filled in, not duplicated


@pytest.mark.parametrize("junk", [
    None, "ET91234567", b"ET9", 7, (), [], ("",), (None, "x"), (12345, "x"), ["not-a-code"],
    {"code": "ET91234567"},
])
def test_malformed_entries_are_skipped_not_fatal(junk):
    family = (junk, (code(1), "3D")) if junk is not None else ((code(1), "3D"),)
    assert merge_variants((), family, own_code=OWN, valid=policy.EVENT_CODE.match) == ((code(1), "3D"),)


def test_codes_the_worker_would_refuse_are_never_stored():
    family = (("not-a-code", "x"), ("ET", "x"), (code(1), "3D"), ("et91234567", "x"))
    assert merge_variants((), family, own_code=OWN, valid=policy.EVENT_CODE.match) == ((code(1), "3D"),)


def test_without_a_validator_any_non_empty_code_is_kept():
    """Other platforms have their own code shapes; only BookMyShow's is checked."""
    assert merge_variants((), (("PVR-123", "x"),), own_code="PVR-1") == (("PVR-123", "x"),)


# ── discovery's merge step ─────────────────────────────────────────────────
def test_discovery_stores_new_siblings_up_to_the_limit_and_reports_the_rest(capsys):
    m = monitor(sibs(100, 10))
    report = merge_from_listing(m, sibs(0, 5))
    assert m.movie.variants == (*sibs(100, 10), *sibs(0, 2))
    assert report.updated[m.id] == list(sibs(0, 2))
    assert report.capped[m.id] == list(sibs(2, 3))
    assert "over_limit=3" in report.summary()
    assert "not stored" in capsys.readouterr().out


def test_discovery_leaves_a_monitor_at_the_limit_unchanged_and_unwritten():
    before = sibs(100, LIMIT)
    m = monitor(before)
    report = merge_from_listing(m, sibs(0, 4))
    assert m.movie.variants == before
    assert m.id not in report.updated                 # nothing to persist
    assert report.capped[m.id] == list(sibs(0, 4))


def test_repeated_discovery_never_grows_a_monitor_past_the_limit():
    m = monitor()
    for start in range(0, 120, 5):
        merge_from_listing(m, sibs(start, 5))
        assert len(m.movie.variants) <= LIMIT
    assert m.movie.variants == sibs(0, LIMIT)


def test_malformed_listing_rows_cannot_stop_discovery():
    m = monitor()
    report = merge_from_listing(m, [(code(1), "3D"), ("bad", "x")])
    assert m.movie.variants == ((code(1), "3D"),)
    assert report.updated[m.id] == [(code(1), "3D")]


def test_a_capped_monitor_is_still_one_the_worker_checks():
    """The point of the cap: before it, a thirteenth sibling made the worker's
    own screen refuse the monitor outright."""
    m = monitor(sibs(100, 11))
    merge_from_listing(m, sibs(0, 30))
    assert len(m.movie.variants) == LIMIT
    assert policy.shape_problem(m) == ""                # "" = nothing wrong
    assert policy.request_cost(m) == 1 + LIMIT
    over = monitor(sibs(100, LIMIT + 1))
    assert policy.shape_problem(over) == "it carries too many sibling events"


# ── no other path persists a longer list ──────────────────────────────────
def test_discovery_is_the_only_code_that_rewrites_a_stored_monitors_film():
    """Every assignment to ``<monitor>.movie`` outside the tests, and every
    place a MovieRef is rebuilt with a ``variants=`` argument, is known."""
    root = Path(__file__).resolve().parent.parent
    movie_writes, variant_rebuilds = [], []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(("tests/", ".venv", "venv/")) or "/site-packages/" in rel:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute) and target.attr == "movie":
                        movie_writes.append(rel)
            if isinstance(node, ast.Call) and any(k.arg == "variants" for k in node.keywords):
                variant_rebuilds.append(rel)
    assert movie_writes == ["monitor/discovery.py"]
    # The provider builds families from BookMyShow (deduplicated, validated) and
    # siblings with none; the checker's union is for a fetch and never saved.
    assert sorted(set(variant_rebuilds)) == ["monitor/checker.py", "monitor/discovery.py",
                                             "platforms/bookmyshow.py"]


def test_the_app_refuses_to_save_a_running_monitor_with_too_many_siblings():
    """The other way a list reaches the store is a person creating a monitor
    from the catalogue; that path refuses rather than stores."""
    assert policy.shape_problem(monitor(sibs(0, LIMIT))) == ""
    assert policy.shape_problem(monitor(sibs(0, LIMIT + 1))) == "it carries too many sibling events"
