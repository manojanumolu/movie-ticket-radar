#!/usr/bin/env python3
"""Shrink oversized avatar artwork to what a profile picture needs.

    python tools/optimize_avatars.py            # report what would change
    python tools/optimize_avatars.py --write    # do it

An avatar is shown at 30–128 px, so a 1254×1254 PNG of ~1.9 MB is forty
times the pixels and fifty times the bytes it will ever need. This
rewrites any avatar wider than ``MAX_PX`` or heavier than ``MAX_BYTES`` as
a WebP of at most ``MAX_PX`` on its long side, at a quality that is
visually lossless for this kind of artwork, with no metadata. The picture
itself — framing, colour, subject — is never altered: this is a resize and
a re-encode, nothing more. Files already small enough are left exactly as
they are, and the originals stay retrievable from git history.

Pillow is Streamlit's own dependency, so nothing new is installed for
this; it is a one-off maintenance step, never run by the app.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ui import assets_registry as art  # noqa: E402

MAX_PX = 512
MAX_BYTES = 150 * 1024
QUALITY = 92


def plan() -> list[tuple[art.Asset, tuple[int, int]]]:
    from PIL import Image

    out = []
    for asset in art.avatars():
        with Image.open(asset.path) as im:
            size = im.size
        if max(size) > MAX_PX or asset.size > MAX_BYTES:
            out.append((asset, size))
    return out


def convert(asset: art.Asset) -> Path:
    from PIL import Image

    target = asset.path.with_suffix(".webp")
    with Image.open(asset.path) as im:
        im = im.convert("RGBA") if "A" in im.getbands() else im.convert("RGB")
        im.thumbnail((MAX_PX, MAX_PX), Image.Resampling.LANCZOS)
        im.save(target, "WEBP", quality=QUALITY, method=6)       # no exif, no icc
    if target != asset.path:
        asset.path.unlink()
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="rewrite the files; otherwise only report")
    args = parser.parse_args()
    todo = plan()
    if not todo:
        print("nothing to do: every avatar is already within limits")
        return 0
    for asset, (w, h) in todo:
        before = asset.size
        if args.write:
            target = convert(asset)
            after = target.stat().st_size
            print(f"{asset.category}/{asset.key}: {w}x{h} {before / 1024:.0f} KB -> {target.name} {after / 1024:.0f} KB")
        else:
            print(f"{asset.category}/{asset.key}: {w}x{h} {before / 1024:.0f} KB  (would become {MAX_PX}px WebP)")
    if args.write:
        art.refresh()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
