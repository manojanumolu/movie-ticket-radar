#!/usr/bin/env python3
"""Find out what BookMyShow will actually answer from this host.

BookMyShow sits behind Cloudflare bot management. Whether a request is
refused depends on far more than the URL: the TLS fingerprint of the HTTP
client, whether the caller looks like a browser that warmed up on the
homepage first, and which headers are present. A plain ``requests`` call and
a TLS-impersonating call to the same URL routinely get different answers.

This script tries the combinations in a fixed order and prints a table of
what each one did, so the provider's strategy chain can be built from
evidence instead of guesswork. It changes nothing and is never imported by
the app.

    python tools/bms_diagnose.py
    python tools/bms_diagnose.py --city hyderabad
"""

from __future__ import annotations

import argparse
import json
import re
import sys

sys.path.insert(0, __file__.rsplit("tools", 1)[0])

SITE = "https://in.bookmyshow.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

BROWSER_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua": '"Chromium";v="140", "Not:A-Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Connection": "keep-alive",
}

API_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    "x-app-code": "WEB",
    "x-region-code": "HYD",
    "x-region-slug": "hyderabad",
    "x-latitude": "17.385",
    "x-longitude": "78.487",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

EVENT_RE = re.compile(r"ET\d{6,}")
RESULTS: list[dict] = []


def record(name: str, status, note: str = "", body: str = "", headers=None) -> None:
    codes = sorted(set(EVENT_RE.findall(body or "")))
    row = {
        "probe": name,
        "status": status,
        "bytes": len(body or ""),
        "event_codes": len(codes),
        "sample": codes[:5],
        "server": (headers or {}).get("server", ""),
        "cf_ray": bool((headers or {}).get("cf-ray")),
        "note": note,
    }
    RESULTS.append(row)
    flag = "OK  " if str(status) == "200" else "FAIL"
    print(f"  [{flag}] {name:<38} status={status} bytes={row['bytes']:<7} "
          f"ET-codes={row['event_codes']:<4} {note}")


# ──────────────────────────────────────────────────────────────────────────
def probe_requests(city: str) -> None:
    import requests

    print("\n-- plain `requests` --")
    try:
        r = requests.get("https://api.ipify.org", timeout=15)
        record("control: api.ipify.org", r.status_code, f"egress ok ({r.text[:20]})",
               r.text, r.headers)
    except Exception as exc:
        record("control: api.ipify.org", "ERR", f"{type(exc).__name__}")

    targets = [
        ("requests: homepage", f"{SITE}/", BROWSER_HEADERS, None),
        ("requests: explore html", f"{SITE}/explore/movies-{city}", BROWSER_HEADERS, None),
        ("requests: explore api", f"{SITE}/api/explore/v1/discover/movies-{city}",
         API_HEADERS, {"regionCode": "HYD"}),
    ]
    for name, url, headers, params in targets:
        try:
            r = requests.get(url, headers=headers, params=params, timeout=20)
            record(name, r.status_code, "", r.text, r.headers)
        except Exception as exc:
            record(name, "ERR", f"{type(exc).__name__}: {exc}")

    print("\n-- `requests` with homepage warm-up (cookie carry) --")
    try:
        s = requests.Session()
        home = s.get(f"{SITE}/", headers=BROWSER_HEADERS, timeout=20)
        record("session: homepage", home.status_code,
               f"cookies={len(s.cookies)}", home.text, home.headers)
        r = s.get(f"{SITE}/explore/movies-{city}", headers=BROWSER_HEADERS, timeout=20)
        record("session: explore html", r.status_code,
               f"cookies={len(s.cookies)}", r.text, r.headers)
    except Exception as exc:
        record("session warm-up", "ERR", f"{type(exc).__name__}: {exc}")


def probe_curl_cffi(city: str) -> None:
    print("\n-- `curl_cffi` (impersonates a real browser's TLS fingerprint) --")
    try:
        from curl_cffi import requests as creq
    except ImportError:
        record("curl_cffi", "SKIP", "not installed")
        return

    for impersonate in ("chrome", "chrome131", "safari"):
        try:
            s = creq.Session(impersonate=impersonate)
            home = s.get(f"{SITE}/", timeout=25)
            record(f"curl_cffi[{impersonate}]: homepage", home.status_code,
                   "", home.text, home.headers)
            if home.status_code != 200:
                continue

            r = s.get(f"{SITE}/explore/movies-{city}", timeout=25)
            record(f"curl_cffi[{impersonate}]: explore html", r.status_code,
                   "", r.text, r.headers)

            a = s.get(f"{SITE}/api/explore/v1/discover/movies-{city}",
                      headers={"x-region-code": "HYD", "x-region-slug": city,
                               "x-app-code": "WEB"}, timeout=25)
            record(f"curl_cffi[{impersonate}]: explore api", a.status_code,
                   "", a.text, a.headers)

            q = s.get(f"{SITE}/serv/getData",
                      params={"cmd": "QUICKBOOK", "type": "MT", "f": "json"},
                      headers={"Cookie": "Rgn=Code%3DHYD"}, timeout=25)
            record(f"curl_cffi[{impersonate}]: quickbook", q.status_code,
                   "", q.text, q.headers)

            sh = s.get(f"{SITE}/api/movies-data/v4/showtimes-by-event/primary-dynamic",
                       params={"eventCode": "ET00478890", "regionCode": "HYD",
                               "isDesktop": "true", "lat": "17.385", "lon": "78.487"},
                       headers={"x-region-code": "HYD", "x-region-slug": city,
                                "x-app-code": "WEB"}, timeout=25)
            record(f"curl_cffi[{impersonate}]: showtimes api", sh.status_code,
                   "", sh.text, sh.headers)
            break  # one working impersonation is enough
        except Exception as exc:
            record(f"curl_cffi[{impersonate}]", "ERR", f"{type(exc).__name__}: {exc}")


def probe_group(city: str, needle: str) -> int:
    """Dump how BookMyShow lists one movie *group* and what each child sees.

    A group ("Avengers Endgame: Encore") has one child event per language and
    per premium format. This prints every child with its code, language and
    dimension, then fetches each child's showtimes and prints the venues it
    returned — so the question "does the base event see the premium-format
    venues?" is answered from evidence, not from the docstring.
    """
    from platforms.bookmyshow import BookMyShowProvider, _dicts, _text, region_for
    from monitor.models import MovieRef

    provider = BookMyShowProvider()
    region = region_for(city)
    headers = provider._browse_headers(region)
    headers["Cookie"] = f"Rgn=Code%3D{region[0]}"
    resp = provider._raw_get(f"{SITE}/serv/getData", headers,
                             {"cmd": "QUICKBOOK", "type": "MT", "f": "json"})
    print(f"quickbook status={resp.status_code}")
    if resp.status_code != 200:
        return 1
    groups = resp.json().get("moviesData", {}).get("BookMyShow", {}).get("arrEvents", [])
    wanted = needle.lower()
    hits = [g for g in groups if wanted in _text(g.get("EventTitle")).lower()]
    print(f"{len(groups)} group(s) listed; {len(hits)} match '{needle}'")
    for group in hits:
        print(f"\n== {_text(group.get('EventTitle'))} [{_text(group.get('EventCode'))}] ==")
        children = list(_dicts(group.get("ChildEvents"))) or [group]
        for child in children:
            code = _text(child.get("EventCode"))
            print(f"  child {code}: lang={_text(child.get('EventLanguage'))!r} "
                  f"dim={_text(child.get('EventDimension'))!r} name={_text(child.get('EventName'))!r} "
                  f"status={_text(child.get('EventStatus'))!r}")
        for child in children:
            code = _text(child.get("EventCode"))
            movie = MovieRef(platform="bookmyshow", event_code=code, title=_text(group.get("EventTitle")),
                             region_code=region[0], region_slug=region[1])
            try:
                snap = provider.fetch(movie)
            except Exception as exc:  # noqa: BLE001 - diagnostics
                print(f"  -- {code}: fetch failed: {type(exc).__name__}: {exc}")
                continue
            print(f"  -- {code} ({_text(child.get('EventLanguage'))} {_text(child.get('EventDimension'))}): "
                  f"{len(snap.venues)} venue(s), {len(snap.showtimes)} showtime(s), "
                  f"dates={snap.bookable_dates} closed={snap.closed_dates}")
            for v in snap.venues:
                print(f"       {v.code:<6} {v.name} | {v.area} | {', '.join(v.formats)}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose BookMyShow reachability")
    parser.add_argument("--city", default="hyderabad")
    parser.add_argument("--group", default="",
                        help="dump the child events (and each one's venues) of a listed movie group")
    args = parser.parse_args(argv)

    if args.group:
        return probe_group(args.city, args.group)

    print(f"BookMyShow reachability diagnosis — city={args.city}")
    probe_requests(args.city)
    probe_curl_cffi(args.city)

    print("\n===== JSON =====")
    print(json.dumps(RESULTS, indent=2))

    winners = [r for r in RESULTS if str(r["status"]) == "200" and r["event_codes"] > 0]
    print("\n===== VERDICT =====")
    if winners:
        for w in winners:
            print(f"  WORKS: {w['probe']} -> {w['event_codes']} event codes {w['sample']}")
        return 0
    reachable = [r for r in RESULTS if str(r["status"]) == "200" and "bookmyshow" not in r["probe"]]
    print("  Nothing returned usable BookMyShow data from this host.")
    print(f"  (non-BMS control reachable: {bool(reachable)})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
