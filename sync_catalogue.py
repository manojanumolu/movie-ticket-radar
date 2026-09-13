#!/usr/bin/env python3
"""Sync a city's BookMyShow catalogue into data/catalogue.json.

    python sync_catalogue.py                     # sync every enabled city
    python sync_catalogue.py --city hyderabad
    python sync_catalogue.py --no-detail         # movie list only, no theatres
    python sync_catalogue.py --probe             # report what each strategy did

This is the backend half of the location-first flow: the UI never calls
BookMyShow to build its movie grid, it reads what this job committed. Run by
``.github/workflows/catalogue-sync.yml`` on a schedule.

``--probe`` exists because BookMyShow has no documented city-listing API and
which internal endpoint answers depends on where the request comes from.
Running the probe on a CI runner is how the working strategy was identified.
"""

from __future__ import annotations

import argparse
import json
import sys

from config.locations import DEFAULT_LOCATION, enabled_locations, get_location
from config.timezone import fmt_datetime, now_ist
from monitor.catalogue import DETAIL_LIMIT, SyncStatus, sync_region
from platforms import get_provider


def probe(city: str, platform: str) -> int:
    provider = get_provider(platform)
    print(f"Probing {platform} listing strategies for '{city}'…\n")
    report = provider.probe_listing(city)
    print(json.dumps(report, indent=2))

    winners = [r for r in report if r.get("ok")]
    print()
    for row in report:
        mark = "OK  " if row.get("ok") else "FAIL"
        detail = (
            f"{row.get('count', 0)} movie(s): {', '.join(row.get('sample', []))}"
            if row.get("ok")
            else row.get("error", "returned nothing")
        )
        print(f"  [{mark}] {row['strategy']}: {detail}")

    if not winners:
        print("\n::error::no listing strategy worked — the catalogue cannot be built")
        return 1
    print(f"\n::notice::working strategy: {winners[0]['strategy']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync the city movie catalogue")
    parser.add_argument("--city", default="", help="city slug (default: every enabled city)")
    parser.add_argument("--platform", default="bookmyshow")
    parser.add_argument("--no-detail", action="store_true",
                        help="only list movies; skip per-movie theatre/format reads")
    parser.add_argument("--limit", type=int, default=DETAIL_LIMIT,
                        help="max movies to resolve detail for in one run")
    parser.add_argument("--probe", action="store_true",
                        help="report what every listing strategy does, then exit")
    args = parser.parse_args(argv)

    if args.probe:
        return probe(args.city or DEFAULT_LOCATION, args.platform)

    cities = [get_location(args.city)] if args.city else enabled_locations()
    print(f"Catalogue sync at {fmt_datetime(now_ist())} IST — "
          f"{', '.join(c.name for c in cities)}\n")

    results = []
    for city in cities:
        print(f"── {city.label} ──")
        result = sync_region(
            city.slug,
            args.platform,
            mirror=False,          # the workflow commits data/ once, at the end
            detail=not args.no_detail,
            detail_limit=args.limit,
        )
        results.append((city, result))
        print()

    ok = [r for _, r in results if r["ok"]]
    for city, result in results:
        status = result["status"]
        if status is SyncStatus.OK:
            print(f"  {city.name}: {result['movies']} movie(s), "
                  f"{result.get('detailed', 0)} detailed, {result.get('failed', 0)} failed")
        else:
            print(f"  {city.name}: {status.value} — {result.get('message', '')}")

    if not ok:
        # A failed sync leaves the previous catalogue in place, so the app
        # keeps working on yesterday's data. Fail the job so it's visible.
        print("::error::every city failed to sync; the existing catalogue was left unchanged")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
