"""The hand-picked artwork, by name.

``static/`` holds the images that were prepared by hand for the UI — the
Login page's poster wall and the avatar gallery. None of it comes from
BookMyShow: the catalogue's posters belong to whatever is showing this week,
these belong to the brand. A page asks for ``poster("interstellar")`` or
``avatar("doraemon")`` and never spells a filesystem path.

The folder is called ``static`` because that is the one name Streamlit
serves: with ``server.enableStaticServing`` on, ``static/x/y.jpg`` is
``app/static/x/y.jpg`` to the browser — fetched once, revalidated by ETag
after that — instead of riding inside the page as base64 on every rerun.
:func:`static_url` builds that address.

Layout (one folder per category; a file's stem is its key)::

    static/
        login/posters/    avengers_endgame.jpg  interstellar.jpg  …
        avatars/<category>/   doraemon.png  iron_man.jpg  batman.jpg  …
        branding/         (reserved: logo lockups, favicon)

Keys are the lowercase, underscore-separated stems — ``kung_fu_panda``, not
``KUNG FU PANDA`` — so a key is safe in a widget key, a CSS class or a URL.
A folder's name is the category (``marvel``, ``dc``, ``tollywood`` …) and
the picker shows them in :data:`AVATAR_CATEGORIES` order.

Two rules of the road:

* The format is read from the bytes, not the extension
  (:func:`sniff`). The posters arrived as JPEG data under ``.webp`` names;
  a data URI that lied about its type would still render in a browser,
  but ``st.image`` or Pillow would not be so forgiving.
* A page uses :func:`static_url` for anything the browser can cache.
  :func:`data_uri` is for the odd tiny image that must be inline; it is
  cached, but whatever it holds travels inside the page on every rerun,
  and :func:`oversized` names what is too heavy for that.

This module is plain Python — no Streamlit — so a test, a build script or
the worker can read the inventory without a script run.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "static"
#: Where Streamlit serves ``ROOT``. Relative on purpose: the app may sit
#: under a base path, and on Community Cloud inside the host's iframe.
STATIC_PREFIX = "app/static"
POSTERS_DIR = ROOT / "login" / "posters"
AVATARS_DIR = ROOT / "avatars"
BRANDING_DIR = ROOT / "branding"

#: The Login page's wall, in display order. A poster that is on disk but not
#: listed here still shows — after these — so a seventh or eighth can be
#: dropped into the folder without a code change.
LOGIN_POSTERS = (
    "avengers_endgame",
    "interstellar",
    "avatar",
    "rrr",
    "baahubali",
    "spiderman_brand_new_day",
)

#: Avatar categories in picker order. A folder not named here is still
#: discovered; it sorts after these.
AVATAR_CATEGORIES = ("animation", "cartoon", "marvel", "dc", "tollywood")

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"})

#: Heavier than this and an image should not be embedded inline as it is.
#: 300 KB of JPEG is ~400 KB of base64 in the page; six posters at that size
#: are already 2.4 MB of HTML per rerun.
EMBED_LIMIT = 300 * 1024

#: Display names where ``key.replace("_", " ").title()`` gets it wrong.
LABELS = {
    "rrr": "RRR",
    "avengers_endgame": "Avengers: Endgame",
    "spiderman_brand_new_day": "Spider-Man: Brand New Day",
    "spiderman": "Spider-Man",
    "iron_man": "Iron Man",
    "kung_fu_panda": "Kung Fu Panda",
    "dc": "DC",
    "bookmyshow": "BookMyShow",
    "pvr": "PVR Cinemas",
}


@dataclass(frozen=True)
class Asset:
    key: str
    kind: str        # "poster" | "avatar" | "brand"
    category: str    # avatars: the folder; posters: "login"; brand: "branding"
    path: Path

    @property
    def label(self) -> str:
        return label_for(self.key)

    @property
    def size(self) -> int:
        return self.path.stat().st_size

    @property
    def mime(self) -> str:
        return sniff(self.path)


def label_for(key: str) -> str:
    return LABELS.get(key, key.replace("_", " ").title())


_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
_BY_SUFFIX = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml"}


def sniff(path: Path) -> str:
    """The image's MIME type from its first bytes; the suffix only as a last resort."""
    try:
        with path.open("rb") as fh:
            head = fh.read(12)
    except OSError:
        head = b""
    for magic, mime in _MAGIC:
        if head.startswith(magic):
            return mime
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.lstrip()[:1] == b"<":
        return "image/svg+xml"
    return _BY_SUFFIX.get(path.suffix.lower(), "application/octet-stream")


def _scan(directory: Path, kind: str, category: str) -> list[Asset]:
    if not directory.is_dir():
        return []
    return [
        Asset(key=p.stem, kind=kind, category=category, path=p)
        for p in sorted(directory.iterdir())
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
    ]


def _category_rank(name: str) -> tuple[int, str]:
    try:
        return AVATAR_CATEGORIES.index(name), name
    except ValueError:
        return len(AVATAR_CATEGORIES), name


@lru_cache(maxsize=1)
def _posters() -> dict[str, Asset]:
    found = {a.key: a for a in _scan(POSTERS_DIR, "poster", "login")}
    ordered = [found.pop(k) for k in LOGIN_POSTERS if k in found]
    ordered.extend(found[k] for k in sorted(found))
    return {a.key: a for a in ordered}


@lru_cache(maxsize=1)
def _avatars() -> dict[str, Asset]:
    if not AVATARS_DIR.is_dir():
        return {}
    folders = sorted((d for d in AVATARS_DIR.iterdir() if d.is_dir()),
                     key=lambda d: _category_rank(d.name))
    found: dict[str, Asset] = {}
    for folder in folders:
        for asset in _scan(folder, "avatar", folder.name):
            # First folder wins on a clash; the audit reports the loser.
            found.setdefault(asset.key, asset)
    return found


@lru_cache(maxsize=1)
def _branding() -> dict[str, Asset]:
    return {a.key: a for a in _scan(BRANDING_DIR, "brand", "branding")}


def refresh() -> None:
    """Forget what was scanned — after the folders change, or in a test."""
    _posters.cache_clear()
    _avatars.cache_clear()
    _branding.cache_clear()
    data_uri.cache_clear()
    static_url.cache_clear()
    fingerprint.cache_clear()


# ── lookups ───────────────────────────────────────────────────────────────
def posters() -> list[Asset]:
    """The Login wall, in :data:`LOGIN_POSTERS` order, then any extras."""
    return list(_posters().values())


def poster(key: str) -> Asset | None:
    return _posters().get(key)


def avatars() -> list[Asset]:
    """Every avatar, grouped by category in picker order, alphabetical within."""
    return list(_avatars().values())


def avatar(key: str) -> Asset | None:
    return _avatars().get(key)


#: What a stored avatar key may look like: a registered stem, nothing else.
#: ``resolve_avatar`` is the only way a value from outside (a Firestore
#: document, a query string) becomes an image — a path, a URL or a scheme
#: can never get past it, because the answer is always one of our files.
AVATAR_KEY = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")
AVATAR_KEY_MAX = 40


def is_avatar_key(value: object) -> bool:
    """Is ``value`` the key of an avatar we have on disk?"""
    return (isinstance(value, str) and 0 < len(value) <= AVATAR_KEY_MAX
            and AVATAR_KEY.match(value) is not None and value in _avatars())


def resolve_avatar(value: object) -> Asset | None:
    """The avatar a stored value names, or None for anything unregistered."""
    return _avatars()[value] if is_avatar_key(value) else None


def avatars_by_category() -> dict[str, list[Asset]]:
    groups: dict[str, list[Asset]] = {}
    for asset in avatars():
        groups.setdefault(asset.category, []).append(asset)
    return groups


def branding() -> list[Asset]:
    return list(_branding().values())


def brand(key: str) -> Asset | None:
    return _branding().get(key)


# ── rendering ─────────────────────────────────────────────────────────────
@lru_cache(maxsize=64)
def data_uri(asset: Asset) -> str:
    """The image inline, for an ``<img src>`` in ``st.markdown``.

    Read once per process. Anything in :func:`oversized` should not come
    through here as it is — the page would carry it on every rerun.
    """
    try:
        payload = base64.b64encode(asset.path.read_bytes()).decode("ascii")
    except OSError:
        return ""
    return f"data:{asset.mime};base64,{payload}"


@lru_cache(maxsize=64)
def static_url(asset: Asset) -> str:
    """The served address, for an ``<img src>`` the browser can cache.

    ``?v=`` carries a content fingerprint, so a swapped file under the same
    name is fetched afresh rather than found in a stale cache entry.
    """
    try:
        rel = asset.path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return ""
    return f"{STATIC_PREFIX}/{rel}?v={fingerprint(asset)}"


@lru_cache(maxsize=64)
def fingerprint(asset: Asset) -> str:
    try:
        return hashlib.md5(asset.path.read_bytes()).hexdigest()[:8]
    except OSError:
        return "0"


# ── audit ─────────────────────────────────────────────────────────────────
def all_assets() -> list[Asset]:
    return posters() + avatars() + branding()


def oversized(limit: int = EMBED_LIMIT) -> list[Asset]:
    """Assets heavier than ``limit`` bytes — too big to embed unprepared."""
    return [a for a in all_assets() if a.size > limit]


def mismatched() -> list[Asset]:
    """Assets whose extension disagrees with their bytes."""
    return [a for a in all_assets()
            if _BY_SUFFIX.get(a.path.suffix.lower()) not in (None, a.mime)]


def duplicates() -> list[tuple[Asset, Asset]]:
    """Pairs of assets with identical content — one of them is the wrong picture."""
    seen: dict[str, Asset] = {}
    pairs = []
    for asset in all_assets():
        digest = hashlib.md5(asset.path.read_bytes()).hexdigest()
        if digest in seen:
            pairs.append((seen[digest], asset))
        else:
            seen[digest] = asset
    return pairs


__all__ = [
    "AVATAR_CATEGORIES", "Asset", "EMBED_LIMIT", "LOGIN_POSTERS", "ROOT", "STATIC_PREFIX",
    "AVATAR_KEY", "AVATAR_KEY_MAX",
    "all_assets", "avatar", "avatars", "avatars_by_category", "brand", "branding",
    "data_uri", "duplicates", "fingerprint", "is_avatar_key", "label_for", "mismatched", "oversized",
    "poster", "posters", "refresh", "resolve_avatar", "sniff", "static_url",
]
