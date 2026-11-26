"""Every monitor shape production can hold still round-trips, and the one
historical gap is exactly the one firestore.rules treats as unchanged.

tests/fixtures/historical_monitor_shapes.json holds, for each commit that
changed monitor/models.py since monitors moved to Firestore (79336ee, 16 Sep
2026), that commit's own Monitor.to_dict output for a fully populated
monitor. Firestore documents can only have been written by one of them.
"""
import json
from pathlib import Path

import pytest

from monitor.models import Monitor

SHAPES = json.loads((Path(__file__).parent / "fixtures/historical_monitor_shapes.json").read_text())
IDENTITY = ("id", "movie", "targets", "date_codes", "created_at", "categories", "show_time")
DEFAULTS = {"categories": [], "show_time": ""}


def as_stored(value):
    return json.loads(json.dumps(value, default=str))        # Firestore stores tuples as arrays


@pytest.mark.parametrize("commit", sorted(SHAPES))
def test_identity_fields_round_trip_equal_under_the_rules_reading(commit):
    stored = SHAPES[commit]
    rewritten = as_stored(Monitor.from_dict(stored).to_dict())
    for key in IDENTITY:
        # firestore.rules keepsWatchIdentity: an absent categories/show_time
        # equals its empty default; every other identity field must match.
        assert stored.get(key, DEFAULTS.get(key)) == rewritten.get(key, DEFAULTS.get(key)), (commit, key)


def test_the_only_shape_difference_is_the_known_one():
    differing = {}
    for commit, stored in SHAPES.items():
        rewritten = as_stored(Monitor.from_dict(stored).to_dict())
        keys = sorted(k for k in IDENTITY if stored.get(k, "<absent>") != rewritten.get(k, "<absent>"))
        if keys:
            differing[commit] = keys
    assert differing == {"79336ee": ["categories", "show_time"]}


def test_the_rule_encodes_that_reading():
    rules = (Path(__file__).resolve().parents[1] / "firestore.rules").read_text()
    assert "request.resource.data.get('categories', []) == resource.data.get('categories', [])" in rules
    assert "request.resource.data.get('show_time', '') == resource.data.get('show_time', '')" in rules
