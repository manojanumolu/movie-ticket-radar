#!/usr/bin/env python3
"""Resolve a BookMyShow listing into the catalogue.

    python resolve_movie.py "https://in.bookmyshow.com/movies/hyderabad/.../ET00123456"
    python resolve_movie.py --refresh bookmyshow:ET00123456
    python resolve_movie.py --refresh-all

Run it locally, or through the ``Resolve movie`` workflow when your own
network is bot-checked by BookMyShow — the runner writes the result back into
``data/catalogue.json`` and the Streamlit app picks it up from the repo.
"""

from __future__ import annotations

import argparse
import sys

from monitor.catalogue import (
    find_entry,
    list_entries,
    movie_from_entry,
    refresh_entry,
    resolve_url,
)
from platforms.base import PlatformBlocked, PlatformError


def _describe(entry: dict) -> None:
    movie = movie_from_entry(entry)
    print(f"  {movie.title}  [{movie.event_code}]  {movie.city}")
    print(f"  {len(entry.get('venues', []))} theatre(s), "
          f"{entry.get('showtime_count', 0)} showtime(s)")
    for venue in entry.get("venues", [])[:40]:
        formats = ", ".join(venue.get("formats", [])) or "no format published"
        print(f"    - {venue.get('name')} ({venue.get('area') or 'area unknown'}): {formats}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve a movie into the catalogue")
    parser.add_argument("url", nargs="?", default="", help="BookMyShow buytickets URL or ET code")
    parser.add_argument("--refresh", default="", help="re-read a catalogue entry by id")
    parser.add_argument("--refresh-all", action="store_true", help="re-read every cached movie")
    parser.add_argument("--platform", default="bookmyshow")
    args = parser.parse_args(argv)

    targets: list[tuple[str, callable]] = []
    if args.url:
        targets.append((args.url, lambda: resolve_url(args.url, args.platform, mirror=False)))
    if args.refresh:
        targets.append((args.refresh, lambda: refresh_entry(args.refresh, mirror=False)))
    if args.refresh_all:
        for entry in list_entries():
            mid = movie_from_entry(entry).id
            targets.append((mid, lambda mid=mid: refresh_entry(mid, mirror=False)))

    if not targets:
        parser.error("give a URL, --refresh <id>, or --refresh-all")

    failures = 0
    for label, action in targets:
        print(f"Resolving {label} …")
        try:
            _describe(action())
        except PlatformBlocked as exc:
            failures += 1
            print(f"::warning::{label}: {exc}")
        except (PlatformError, KeyError) as exc:
            failures += 1
            print(f"::error::{label}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"::error::{label}: unexpected {type(exc).__name__}: {exc}")

    if failures and failures == len(targets):
        print("::error::nothing could be resolved")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
