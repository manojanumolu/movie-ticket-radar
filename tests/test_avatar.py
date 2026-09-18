"""Phase 2 — the avatar preference.

One field, ``avatar_key`` on ``users/{uid}``; one resolver, the asset
registry; one write, on Save, only when the key changed. These tests pin
the security of the resolver (a stored value can only ever become one of
our files), the write discipline (never on select, never for the same
key, never for a bad key), the fallback for people who have not chosen
(the initial, untouched), the separation from Google's ``photo_url``,
and the picker as a person drives it.
"""

from __future__ import annotations

import json
import re

import pytest

from auth import session
from monitor import state as state_mod
from ui import assets_registry as art
from ui import avatar
from tests.test_app import run
from tests.test_isolation import cloud  # noqa: F401 - fixture

UID = "uid-test-1"

HOSTILE = ("../../../etc/passwd", "..\\..\\x", "https://evil.example/x.png", "javascript:alert(1)",
           "data:image/png;base64,AAAA", "doraemon.webp", "/static/avatars/cartoon/doraemon.webp",
           "DORAEMON", "doraemon ", " doraemon", "dora emon", "doraemon;rm", "jerry", "", None, 0, 3.5,
           ["doraemon"], {"k": "doraemon"}, "a" * 41, "\x00doraemon", "doraemon\n")


def user(**kw) -> session.AuthUser:
    base = dict(uid=UID, email="tester@example.com", display_name="Test Person")
    base.update(kw)
    return session.AuthUser(**base)


# ──────────────────────────────────────────────────────────────────────────
# The registry
# ──────────────────────────────────────────────────────────────────────────
def test_every_registered_avatar_is_a_valid_key_and_nothing_else_is():
    for asset in art.avatars():
        assert art.is_avatar_key(asset.key)
        assert art.resolve_avatar(asset.key) is asset
    for bad in HOSTILE:
        assert not art.is_avatar_key(bad), repr(bad)
        assert art.resolve_avatar(bad) is None, repr(bad)


def test_categories_are_grouped_in_picker_order_and_never_empty():
    groups = art.avatars_by_category()
    assert list(groups) == [c for c in art.AVATAR_CATEGORIES if c in groups]
    assert all(groups.values())
    assert "tollywood" not in groups                                  # the folder is empty today
    assert {"cartoon", "animation", "marvel", "dc"} <= set(groups)
    assert [a.key for a in groups["dc"]] == ["batman"]
    assert "doraemon" in [a.key for a in groups["cartoon"]] and "iron_man" in [a.key for a in groups["marvel"]]


def test_the_inventory_is_what_the_pictures_show():
    keys = {a.key for a in art.avatars()}
    assert "dorami" in keys and "jerry" not in keys      # the "jerry" file was Dorami's picture, twice
    assert art.duplicates() == []
    assert art.avatar("iron_man").label == "Iron Man" and art.avatar("kung_fu_panda").label == "Kung Fu Panda"


def test_every_avatar_is_sized_for_a_profile_picture():
    from PIL import Image

    for asset in art.avatars():
        with Image.open(asset.path) as im:
            assert max(im.size) <= 512, f"{asset.key}: {im.size}"
        assert asset.size <= 150 * 1024, f"{asset.key}: {asset.size} bytes"
        assert asset.mime in {"image/webp", "image/jpeg", "image/png"}
    assert not [a.key for a in art.oversized() if a.kind == "avatar"]


# ──────────────────────────────────────────────────────────────────────────
# Reading: the mark on the chip
# ──────────────────────────────────────────────────────────────────────────
def test_no_key_means_the_initial_exactly_as_before(bare_state):
    assert avatar.chosen({}) is None
    assert avatar.mark(user(), {}) == '<div class="av">T</div>'
    assert avatar.mark(user(display_name=""), {"notify_email": "x"}) == '<div class="av">T</div>'   # email's first letter
    assert avatar.current_key({}) == ""


def test_a_registered_key_becomes_our_own_served_file(bare_state):
    html = avatar.mark(user(), {"avatar_key": "doraemon"})
    assert html.startswith('<div class="av has-avatar"><img src="app/static/avatars/cartoon/doraemon.webp?v=')
    assert 'alt="Doraemon"' in html and 'loading="lazy"' in html
    assert "data:" not in html and "http" not in html


@pytest.mark.parametrize("bad", HOSTILE)
def test_anything_unregistered_falls_back_to_the_initial(bad, bare_state, capsys):
    html = avatar.mark(user(), {"avatar_key": bad})
    assert html == '<div class="av">T</div>'
    assert "<img" not in html
    out = capsys.readouterr().out
    if bad:                                                # a real value was ignored — said once, never quoted
        assert "[avatar] ignoring an unregistered avatar_key" in out
        assert str(bad) not in out.replace("str, 8 chars", "")


def test_the_warning_is_said_once_per_session(bare_state, capsys):
    for _ in range(3):
        avatar.mark(user(), {"avatar_key": "https://evil.example/x"})
    assert capsys.readouterr().out.count("[avatar] ignoring") == 1


def test_google_photo_url_is_neither_shown_nor_touched(bare_state):
    google = user(photo_url="https://lh3.googleusercontent.com/p")
    assert avatar.mark(google, {}) == '<div class="av">T</div>'           # never the Google picture
    html = avatar.mark(google, {"avatar_key": "iron_man"})
    assert "googleusercontent" not in html and "iron_man.webp" in html
    assert google.photo_url == "https://lh3.googleusercontent.com/p"
    src = open("ui/avatar.py", encoding="utf-8").read()
    assert "photo_url" not in src.split('"""', 2)[2]                       # only mentioned in the docstring


# ──────────────────────────────────────────────────────────────────────────
# Writing
# ──────────────────────────────────────────────────────────────────────────
@pytest.fixture
def writes(monkeypatch):
    calls = []
    monkeypatch.setattr(avatar, "save_settings", lambda settings, *, mirror: calls.append((settings, mirror)))
    return calls


def test_saving_the_same_key_writes_nothing(writes, bare_state):
    assert avatar.save({"avatar_key": "doraemon", "notify_email": "a@b"}, "doraemon", mirror=False) is False
    assert avatar.save({}, "", mirror=False) is False
    assert writes == []


def test_a_change_is_one_write_of_the_whole_settings_with_the_new_key(writes, bare_state):
    settings = {"notify_email": "a@b", "default_interval": 15, "avatar_key": "doraemon"}
    assert avatar.save(settings, "iron_man", mirror=True) is True
    assert writes == [({"notify_email": "a@b", "default_interval": 15, "avatar_key": "iron_man"}, True)]
    assert settings["avatar_key"] == "doraemon"                          # the caller's dict is not mutated


def test_clearing_back_to_the_initial_is_a_write_of_an_empty_key(writes, bare_state):
    assert avatar.save({"avatar_key": "doraemon"}, "", mirror=False) is True
    assert writes[0][0]["avatar_key"] == ""


@pytest.mark.parametrize("bad", [b for b in HOSTILE if b])
def test_a_bad_key_is_refused_before_it_can_reach_the_store(bad, writes, bare_state):
    assert avatar.save({"avatar_key": "doraemon"}, bad, mirror=False) is False
    assert writes == []


# ──────────────────────────────────────────────────────────────────────────
# Firestore: users/{uid}
# ──────────────────────────────────────────────────────────────────────────
def test_avatar_key_lives_on_the_users_document_and_survives_a_reload(cloud, bare_state):  # noqa: F811
    cloud.as_user("uid-a")
    state_mod.save_settings({"notify_email": "a@example.com", "default_interval": 10}, mirror=False)
    state_mod.invalidate_cache()
    settings = state_mod.load_settings()
    assert avatar.chosen(settings) is None                                # an existing account: nothing yet
    before = len(cloud.calls)

    assert avatar.save(settings, "doraemon", mirror=False) is True
    doc = cloud.docs["users"]["uid-a"]
    assert doc["avatar_key"] == "doraemon" and doc["notify_email"] == "a@example.com"
    assert doc["default_interval"] == 10 and doc["owner_uid"] == "uid-a"
    assert set(doc) == {"avatar_key", "notify_email", "default_interval", "owner_uid"}
    assert [c for c in cloud.calls[before:] if c[0] == "POST"] == [("POST", ":commit", "uid-a")]   # one write

    state_mod.invalidate_cache()
    reloaded = state_mod.load_settings()
    assert avatar.current_key(reloaded) == "doraemon"
    assert avatar.mark(user(uid="uid-a"), reloaded).startswith('<div class="av has-avatar">')

    # the same choice again: no traffic at all
    n = len(cloud.calls)
    assert avatar.save(reloaded, "doraemon", mirror=False) is False
    assert cloud.calls[n:] == []
    # a change: exactly one more commit
    assert avatar.save(reloaded, "iron_man", mirror=False) is True
    assert [c for c in cloud.calls[n:] if c[0] == "POST"] == [("POST", ":commit", "uid-a")]
    assert cloud.docs["users"]["uid-a"]["avatar_key"] == "iron_man"


def test_a_bad_value_planted_on_the_document_is_ignored_safely(cloud, bare_state, capsys):  # noqa: F811
    cloud.as_user("uid-a")
    cloud.docs.setdefault("users", {})["uid-a"] = {"owner_uid": "uid-a", "avatar_key": "../../etc/passwd"}
    state_mod.invalidate_cache()
    html = avatar.mark(user(uid="uid-a"), state_mod.load_settings())
    assert html == '<div class="av">T</div>'
    assert "passwd" not in capsys.readouterr().out


# ──────────────────────────────────────────────────────────────────────────
# The picker, as a person drives it (JSON store, signed in)
# ──────────────────────────────────────────────────────────────────────────
def seed(data, **settings) -> None:
    (data / "settings.json").write_text(json.dumps({"users": {UID: settings}}), encoding="utf-8")


def stored(data) -> dict:
    raw = json.loads((data / "settings.json").read_text(encoding="utf-8")) if (data / "settings.json").exists() else {}
    return raw.get("users", {}).get(UID, {})


def all_markdown(app) -> str:
    """Everything rendered, minus the stylesheets."""
    return re.sub(r"<style>.*?</style>", "", " ".join(m.value for m in app.markdown), flags=re.S)


def main_markdown(app) -> str:
    return re.sub(r"<style>.*?</style>", "", " ".join(m.value for m in app.main.markdown), flags=re.S)


def test_an_existing_account_without_a_key_shows_the_initial_and_writes_nothing(isolated_data):
    seed(isolated_data, notify_email="me@example.com")
    app = run("Home")
    assert not app.exception
    main = main_markdown(app)
    assert '<div class="tr-acct-chip">' in main and '<div class="av">T</div>' in main
    assert "has-avatar" not in main
    assert "avatar_key" not in stored(isolated_data)                     # lazy: nothing written to display a default
    assert not [b for b in app.button if b.key and b.key.startswith("av_")]  # the picker is not on the page


def test_the_picker_opens_from_the_account_menu_with_only_the_categories_that_exist(isolated_data):
    app = run("Home")
    assert app.button(key="acct_avatar").label == "Change avatar"
    app.button(key="acct_avatar").click().run()
    assert not app.exception
    md = all_markdown(app)
    assert "YOUR TICKETRADAR AVATAR" in md and "Choose your avatar" in md
    heads = re.findall(r'<div class="tr-avp-cat"><span>([^<]+)</span>', md)
    assert heads == ["Default", "Animation", "Cartoon", "Marvel", "DC"]  # no Tollywood: the folder is empty
    tiles = {b.key for b in app.button if b.key and b.key.startswith("av_")}
    assert tiles == {"av_initial"} | {f"av_{a.key}" for a in art.avatars()}
    assert app.button(key="av_doraemon").label == "Doraemon"             # the accessible name
    tiles_html = re.findall(r'<div class="tr-avt[^"]*">.*?</div></div>', md)
    assert len(tiles_html) == 1 + len(art.avatars())
    assert 'app/static/avatars/cartoon/doraemon.webp' in md
    assert not [t for t in tiles_html if "data:" in t or "http" in t]            # only our served files
    assert md.count('class="tr-avt selected"') == 1                     # one selection: the initial


def test_choosing_previews_locally_and_only_save_writes(isolated_data, monkeypatch):
    seed(isolated_data, notify_email="me@example.com")
    app = run("Home")
    app.button(key="acct_avatar").click().run()
    app.button(key="av_doraemon").click().run()
    md = all_markdown(app)
    assert "Doraemon · Cartoon" in md                                     # the preview changed…
    assert 'class="tr-avt selected"><div class="ring"><img src="app/static/avatars/cartoon/doraemon.webp' in md
    assert "avatar_key" not in stored(isolated_data)                     # …and nothing was written
    assert app.session_state[avatar.DRAFT_KEY] == "doraemon"

    app.button(key="avatar_save").click().run()
    assert not app.exception
    assert stored(isolated_data) == {"notify_email": "me@example.com", "default_interval": 10, "avatar_key": "doraemon"}
    assert app.session_state.get(avatar.OPEN_KEY) is None
    main = main_markdown(app)
    assert 'class="av has-avatar"><img src="app/static/avatars/cartoon/doraemon.webp' in main
    assert "Avatar updated." in main


def test_the_choice_is_there_after_a_fresh_run_and_the_same_choice_is_not_rewritten(isolated_data, monkeypatch):
    seed(isolated_data, notify_email="me@example.com", avatar_key="doraemon")
    app = run("Home")
    main = main_markdown(app)
    assert main.count("doraemon.webp") == 2                               # the chip and the menu header
    calls = []
    real = state_mod.save_settings
    monkeypatch.setattr(state_mod, "save_settings", lambda s, *, mirror=True: (calls.append(s), real(s, mirror=mirror)))
    monkeypatch.setattr(avatar, "save_settings", state_mod.save_settings)
    app.button(key="acct_avatar").click().run()
    assert app.session_state[avatar.DRAFT_KEY] == "doraemon"
    app.button(key="av_doraemon").click().run()
    app.button(key="avatar_save").click().run()
    assert calls == [] and "Avatar updated." not in " ".join(m.value for m in app.main.markdown)

    app.button(key="acct_avatar").click().run()
    app.button(key="av_iron_man").click().run()
    app.button(key="avatar_save").click().run()
    assert [c["avatar_key"] for c in calls] == ["iron_man"]
    assert stored(isolated_data)["avatar_key"] == "iron_man" and stored(isolated_data)["notify_email"] == "me@example.com"


def test_cancel_keeps_what_was_stored(isolated_data):
    seed(isolated_data, avatar_key="batman")
    app = run("Home")
    app.button(key="acct_avatar").click().run()
    app.button(key="av_thor").click().run()
    app.button(key="avatar_cancel").click().run()
    assert stored(isolated_data)["avatar_key"] == "batman"
    assert app.session_state.get(avatar.OPEN_KEY) is None and avatar.DRAFT_KEY not in app.session_state


def test_a_planted_bad_key_never_crashes_the_page_or_loads_anything(isolated_data):
    seed(isolated_data, avatar_key="../../../../etc/passwd")
    app = run("Home")
    assert not app.exception
    main = main_markdown(app)
    assert '<div class="av">T</div>' in main and "passwd" not in main and "has-avatar" not in main


def test_the_other_settings_writes_keep_the_avatar(isolated_data):
    seed(isolated_data, notify_email="old@example.com", avatar_key="oggy")
    app = run("Settings")
    app.text_input(key="settings_email").set_value("new@example.com")
    app.button(key="save_settings").click().run()
    assert stored(isolated_data) == {"notify_email": "new@example.com", "default_interval": 10, "avatar_key": "oggy"}


# ──────────────────────────────────────────────────────────────────────────
# Responsive and accessible, by construction
# ──────────────────────────────────────────────────────────────────────────
def test_the_picker_reuses_the_theme_grid_and_folds_to_three_on_a_phone():
    from ui import theme

    assert "@media (max-width: 768px)" in avatar.CSS
    assert '[class*="st-key-trgrid_av_"] [data-testid="stColumn"] { --tr-cols: 3; }' in avatar.CSS
    assert "--tr-cols" in theme.CSS and '[class*="st-key-trgrid_"]' in theme.CSS       # the folding the grid inherits
    assert avatar.PER_ROW == 7
    assert '[class*="st-key-pick_av_"]:has(button:focus-visible)' in avatar.CSS         # a visible focus ring
    assert '.tr-avt.selected .ok { display:flex; }' in avatar.CSS                        # selection is a mark, not only a colour
    assert avatar.CSS.startswith("<style>") and len(avatar.CSS) < 6000                   # small, and only inside the dialog


def test_the_chip_and_menu_carry_the_avatar_styles_once(isolated_data):
    from ui import theme

    assert ".tr-acct-chip .av.has-avatar" in theme.CSS and ".tr-acct-menu .av" in theme.CSS
    app = run("Home")
    assert " ".join(m.value for m in app.main.markdown).count("tr-avp-head") == 0       # the picker's CSS is not on the page


@pytest.fixture
def bare_state(monkeypatch):
    """``ui.avatar`` outside a script run: a plain dict as session state."""
    import types

    state: dict = {}
    monkeypatch.setattr(avatar, "st", types.SimpleNamespace(session_state=state))
    return state
