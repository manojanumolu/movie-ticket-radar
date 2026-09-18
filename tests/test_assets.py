"""The hand-picked artwork is where the registry says it is.

Phase 0 of the redesign: ``assets/`` was organised, the registry
(``ui.assets_registry``) was written, and nothing on a page uses either
yet. These tests pin what the later phases will build on — the six Login
posters resolve by name and in order, every avatar has a clean key, Batman
is in ``dc``, an image's type comes from its bytes — and they exercise the
registry against a throwaway tree so a missing folder or a wrong extension
is a finding, not a crash.
"""

from __future__ import annotations

import base64
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ui import assets_registry as R

KEY = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")

#: Avatars the redesign brief names and the folders hold today. Absent ones
#: (Hawkeye, Doctor Strange, Loki, the Tollywood set — and Jerry, whose
#: file turned out to be a second copy of Dorami's picture and was dropped
#: in Phase 2) are a reported gap, not something a test can conjure.
EXPECTED_AVATARS = {
    "doraemon", "nobita", "shizuka", "gian", "suneo", "dorami",
    "kung_fu_panda", "tom", "oggy",
    "iron_man", "thor", "hulk", "captain_america", "black_widow",
    "batman",
}

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\0" * 8


# ──────────────────────────────────────────────────────────────────────────
# The real tree
# ──────────────────────────────────────────────────────────────────────────
def test_the_login_wall_resolves_every_poster_in_display_order():
    assert [a.key for a in R.posters()][: len(R.LOGIN_POSTERS)] == list(R.LOGIN_POSTERS)
    for key in R.LOGIN_POSTERS:
        asset = R.poster(key)
        assert asset is not None, f"poster {key!r} is missing from static/login/posters"
        assert asset.kind == "poster" and asset.category == "login"
        assert asset.mime in {"image/jpeg", "image/png", "image/webp"}
        assert asset.size > 0


def test_every_expected_avatar_is_present():
    keys = {a.key for a in R.avatars()}
    missing = EXPECTED_AVATARS - keys
    assert not missing, f"avatars missing from static/avatars: {sorted(missing)}"


def test_batman_lives_in_dc_not_marvel():
    batman = R.avatar("batman")
    assert batman is not None
    assert batman.category == "dc"
    assert batman.path.parent.name == "dc"
    assert not (R.AVATARS_DIR / "marvel" / "batman.jpg").exists()


def test_avatar_categories_come_out_in_picker_order():
    seen = [a.category for a in R.avatars()]
    order = [c for c in dict.fromkeys(seen)]
    ranked = [c for c in R.AVATAR_CATEGORIES if c in order]
    assert order[: len(ranked)] == ranked
    groups = R.avatars_by_category()
    assert list(groups) == order
    assert sum(len(v) for v in groups.values()) == len(R.avatars())


def test_keys_and_filenames_are_url_safe():
    for asset in R.all_assets():
        assert KEY.match(asset.key), f"{asset.path.name}: key must be lowercase snake_case"
        assert asset.path.suffix == asset.path.suffix.lower(), asset.path.name
        assert " " not in asset.path.name, asset.path.name


def test_no_extension_lies_about_its_bytes():
    assert R.mismatched() == []


def test_folders_exist_for_every_category_in_the_brief():
    for name in R.AVATAR_CATEGORIES:
        assert (R.AVATARS_DIR / name).is_dir(), f"static/avatars/{name} is missing"
    assert R.POSTERS_DIR.is_dir()
    assert R.BRANDING_DIR.is_dir()


def test_labels_read_like_titles():
    assert R.poster("rrr").label == "RRR"
    assert R.poster("avengers_endgame").label == "Avengers: Endgame"
    assert R.poster("spiderman_brand_new_day").label == "Spider-Man: Brand New Day"
    assert R.avatar("iron_man").label == "Iron Man"
    assert R.avatar("black_widow").label == "Black Widow"
    assert R.label_for("dc") == "DC"


def test_data_uri_carries_the_sniffed_type_and_the_exact_bytes():
    asset = R.poster("baahubali")
    uri = R.data_uri(asset)
    head, _, payload = uri.partition(",")
    assert head == f"data:{asset.mime};base64"
    assert base64.b64decode(payload) == asset.path.read_bytes()
    assert R.data_uri(asset) is uri                       # read once per process


def test_the_registry_never_imports_streamlit():
    """The worker and the tests can read the inventory without a script run."""
    code = "import sys, ui.assets_registry; sys.exit('streamlit' in sys.modules)"
    proc = subprocess.run([sys.executable, "-c", code], cwd=Path(R.ROOT).parent,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


# ──────────────────────────────────────────────────────────────────────────
# A throwaway tree
# ──────────────────────────────────────────────────────────────────────────
@pytest.fixture
def tree(tmp_path, monkeypatch):
    root = tmp_path / "assets"
    posters = root / "login" / "posters"
    avatars = root / "avatars"
    posters.mkdir(parents=True)
    avatars.mkdir()
    monkeypatch.setattr(R, "ROOT", root)
    monkeypatch.setattr(R, "POSTERS_DIR", posters)
    monkeypatch.setattr(R, "AVATARS_DIR", avatars)
    monkeypatch.setattr(R, "BRANDING_DIR", root / "branding")
    R.refresh()
    yield root
    R.refresh()


def test_missing_folders_mean_empty_lists_not_errors(tree):
    (tree / "login").rename(tree / "gone")
    R.refresh()
    assert R.posters() == [] and R.avatars() == [] and R.branding() == []
    assert R.poster("rrr") is None and R.avatar("batman") is None and R.brand("logo") is None
    assert R.oversized() == [] and R.mismatched() == [] and R.duplicates() == []


def test_posters_keep_the_fixed_order_and_append_extras(tree):
    posters = tree / "login" / "posters"
    for key in ("rrr", "zzz_extra", "avatar", "aaa_extra"):
        (posters / f"{key}.jpg").write_bytes(JPEG)
    (posters / "notes.txt").write_text("not an image")
    R.refresh()
    assert [a.key for a in R.posters()] == ["avatar", "rrr", "aaa_extra", "zzz_extra"]


def test_avatars_group_by_folder_and_unlisted_folders_sort_last(tree):
    for folder, names in {"marvel": ["thor"], "animation": ["tom"], "extras": ["x"], "dc": ["batman"]}.items():
        (tree / "avatars" / folder).mkdir()
        for name in names:
            (tree / "avatars" / folder / f"{name}.png").write_bytes(PNG)
    (tree / "avatars" / "tollywood").mkdir()                # empty: not a group
    R.refresh()
    assert list(R.avatars_by_category()) == ["animation", "marvel", "dc", "extras"]
    assert R.avatar("batman").category == "dc"


def test_sniff_trusts_bytes_over_the_extension(tree):
    posters = tree / "login" / "posters"
    (posters / "honest.png").write_bytes(PNG)
    (posters / "liar.webp").write_bytes(JPEG)               # what the posters arrived as
    (posters / "vector.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    R.refresh()
    assert R.poster("honest").mime == "image/png"
    assert R.poster("liar").mime == "image/jpeg"
    assert R.poster("vector").mime == "image/svg+xml"
    assert [a.key for a in R.mismatched()] == ["liar"]
    assert R.data_uri(R.poster("liar")).startswith("data:image/jpeg;base64,")


def test_the_audit_names_heavy_files_and_identical_pictures(tree):
    posters = tree / "login" / "posters"
    (posters / "small.jpg").write_bytes(JPEG)
    (posters / "big.jpg").write_bytes(JPEG + b"\0" * 4096)
    (posters / "twin.jpg").write_bytes(JPEG)
    R.refresh()
    assert [a.key for a in R.oversized(limit=1024)] == ["big"]
    assert R.oversized(limit=1 << 20) == []
    assert [(a.key, b.key) for a, b in R.duplicates()] == [("small", "twin")]


def test_a_missing_file_yields_an_empty_uri(tree):
    ghost = R.Asset(key="ghost", kind="poster", category="login", path=tree / "ghost.png")
    assert R.data_uri(ghost) == ""
