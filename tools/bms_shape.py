#!/usr/bin/env python3
"""Dump the real shape of the BookMyShow payloads we depend on.

Run from a host that can actually reach BookMyShow (a CI runner — see
tools/bms_diagnose.py for why). Prints the structure of the city listing and
of one movie's showtimes, which is how the parsers in
``platforms/bookmyshow.py`` were written against reality rather than guesswork.

    python tools/bms_shape.py --city hyderabad
"""

from __future__ import annotations

import argparse
import json
import sys

SITE = "https://in.bookmyshow.com"
REGION = {"hyderabad": ("HYD", "17.385", "78.487")}


def walk(node, path="", depth=0, out=None, maxdepth=4):
    """Summarise a payload's structure without printing megabytes."""
    out = out if out is not None else []
    if depth > maxdepth:
        return out
    if isinstance(node, dict):
        keys = list(node.keys())
        out.append(f"{'  ' * depth}{path or '<root>'} {{}} keys={keys[:14]}")
        for k in keys[:14]:
            walk(node[k], k, depth + 1, out, maxdepth)
    elif isinstance(node, list):
        out.append(f"{'  ' * depth}{path}[] len={len(node)}")
        if node:
            walk(node[0], f"{path}[0]", depth + 1, out, maxdepth)
    else:
        text = str(node)
        out.append(f"{'  ' * depth}{path} = {text[:70]}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="hyderabad")
    args = ap.parse_args(argv)
    code, lat, lon = REGION.get(args.city, REGION["hyderabad"])

    from curl_cffi import requests as creq

    s = creq.Session(impersonate="safari")
    print("warming up on the homepage…")
    print("  homepage:", s.get(f"{SITE}/", timeout=30).status_code)

    # ── 1. city listing ──────────────────────────────────────────────────
    print("\n===== QUICKBOOK (city movie list) =====")
    r = s.get(f"{SITE}/serv/getData",
              params={"cmd": "QUICKBOOK", "type": "MT", "f": "json"},
              headers={"Cookie": f"Rgn=Code%3D{code}"}, timeout=30)
    print("status:", r.status_code, "bytes:", len(r.text))
    try:
        data = r.json()
    except Exception:
        print("body head:", r.text[:400])
        return 1

    print("\n-- structure --")
    print("\n".join(walk(data, maxdepth=3)[:60]))

    # Find the list of movie-ish objects.
    def find_movie_lists(node, path="$"):
        hits = []
        if isinstance(node, dict):
            for k, v in node.items():
                hits += find_movie_lists(v, f"{path}.{k}")
        elif isinstance(node, list) and node and isinstance(node[0], dict):
            joined = json.dumps(node[0])[:2000]
            if "ET" in joined and ("Title" in joined or "Name" in joined):
                hits.append((path, len(node), node[0]))
        return hits

    lists = sorted(find_movie_lists(data), key=lambda x: -x[1])
    print(f"\n-- candidate movie arrays: {[(p, n) for p, n, _ in lists[:6]]}")
    if lists:
        path, count, sample = lists[0]
        print(f"\n-- sample movie object from {path} (n={count}) --")
        print(json.dumps(sample, indent=2)[:2500])

    # ── 2. one movie's showtimes ─────────────────────────────────────────
    event = ""
    for _, _, sample in lists:
        for k, v in sample.items():
            if isinstance(v, str) and v.startswith("ET") and v[2:].isdigit():
                event = v
                break
        if event:
            break
    if not event:
        print("\nno event code found; cannot test showtimes")
        return 1

    print(f"\n\n===== SHOWTIMES for {event} =====")
    sh = s.get(f"{SITE}/api/movies-data/v4/showtimes-by-event/primary-dynamic",
               params={"eventCode": event, "regionCode": code, "isDesktop": "true",
                       "dateCode": "", "lat": lat, "lon": lon},
               headers={"x-region-code": code, "x-region-slug": args.city,
                        "x-app-code": "WEB", "x-latitude": lat, "x-longitude": lon,
                        "Accept": "application/json, text/plain, */*"},
               timeout=30)
    print("status:", sh.status_code, "bytes:", len(sh.text))
    if sh.status_code != 200:
        print("body:", sh.text[:600])
        return 1
    try:
        sdata = sh.json()
    except Exception:
        print("body head:", sh.text[:400])
        return 1
    print("body head:", json.dumps(sdata)[:300])
    print("\n-- structure --")
    print("\n".join(walk(sdata, maxdepth=4)[:90]))

    d = sdata.get("data", {})
    if isinstance(d, dict):
        print("\n-- data keys:", list(d.keys()))
        sw = d.get("showtimeWidgets")
        if isinstance(sw, list) and sw:
            print("\n-- showtimeWidgets[0] --")
            print(json.dumps(sw[0], indent=2)[:3000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
