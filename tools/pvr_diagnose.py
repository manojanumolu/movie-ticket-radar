#!/usr/bin/env python3
"""Can this machine read PVR INOX Hyderabad? Read-only, four requests at most.

    python tools/pvr_diagnose.py

Run by ``.github/workflows/pvr-diagnose.yml`` (manual only) to answer the
one question PVR INOX's enablement waits on: does the API answer a GitHub
Actions runner? It asks, in order and paced like the provider:

1. ``content/city``        — is Hyderabad (id 32) listed, and with how many cinemas?
2. ``content/cinemas``     — the cinemas themselves (checked against that count)
3. ``content/nowshowing``  — how many films and prints are listed
4. ``content/csessions``   — one cinema's shows today (the busiest cinema)

The first refusal (401/403/429) ends the run: nothing is retried and no
workaround is attempted. Nothing is written anywhere — no Firestore, no
monitor, no email, no seat layout, no booking. The log holds shapes, counts,
cinema names and the distinct format/status values; never a header, a
booking token or a URL built from one.

The last line is the verdict, one of::

    PVR_DIAGNOSTIC_OK
    PVR_DIAGNOSTIC_BLOCKED_403        (also 401)
    PVR_DIAGNOSTIC_RATE_LIMITED_429
    PVR_DIAGNOSTIC_API_ERROR
    PVR_DIAGNOSTIC_MALFORMED_RESPONSE
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.timezone import fmt_datetime, now_ist  # noqa: E402
from platforms.base import PlatformBlocked, PlatformError  # noqa: E402
from platforms.pvr_inox import (  # noqa: E402
    CITIES,
    PvrInoxProvider,
    _ok_output,
    parse_sessions,
    sale_dates,
)

OK = "PVR_DIAGNOSTIC_OK"
BLOCKED = "PVR_DIAGNOSTIC_BLOCKED_403"
RATE_LIMITED = "PVR_DIAGNOSTIC_RATE_LIMITED_429"
API_ERROR = "PVR_DIAGNOSTIC_API_ERROR"
MALFORMED = "PVR_DIAGNOSTIC_MALFORMED_RESPONSE"

#: The hard ceiling. The run stops before a fifth request, whatever happens.
MAX_REQUESTS = 4


class Malformed(Exception):
    """The API answered, but not in the shape the provider reads."""


def _verdict_for_block(exc: PlatformBlocked) -> str:
    return RATE_LIMITED if "429" in str(exc) else BLOCKED


def _keys(value: Any) -> str:
    return ", ".join(sorted(value)[:25]) if isinstance(value, dict) else type(value).__name__


def _show_summary(output: dict[str, Any]) -> None:
    """Distinct values of the fields the provider reads, and nothing else."""
    blocks = output.get("cinemaMovieSessions")
    if not isinstance(blocks, list):
        raise Malformed("csessions output has no cinemaMovieSessions list")
    fields = ("experience", "screenType", "movieFormat", "filmFormat", "soundFormat",
              "language", "status", "statusCode", "statusTxt")
    seen: dict[str, Counter] = {f: Counter() for f in fields}
    shows = 0
    show_keys: set[str] = set()
    for block in blocks:
        for exp in (block.get("experienceSessions") or []) if isinstance(block, dict) else []:
            for show in (exp.get("shows") or []) if isinstance(exp, dict) else []:
                if not isinstance(show, dict):
                    continue
                shows += 1
                show_keys.update(show)
                seen["experience"][str(exp.get("experience", ""))] += 1
                for f in fields[1:]:
                    seen[f][str(show.get(f, ""))] += 1
    print(f"  films: {len(blocks)}, shows: {shows}")
    print(f"  show fields: {', '.join(sorted(show_keys - {'encrypted'}))}"
          + (" (+ encrypted, not printed)" if "encrypted" in show_keys else ""))
    for f in fields:
        print(f"  {f}: {dict(seen[f].most_common(12))}")
    parsed = parse_sessions(output, "diagnose", now_ms=now_ist().timestamp() * 1000)
    labels = Counter(f"{s.showtime.format_label or '·'} / {s.showtime.event_format} -> {s.showtime.availability.value}"
                     for s in parsed)
    print(f"  as the provider reads them (screen class / movie format -> availability): {dict(labels.most_common(12))}")
    print(f"  shows with a booking link: {sum(1 for s in parsed if s.showtime.booking_url)} of {len(parsed)}")


def run(provider: PvrInoxProvider | None = None, region: str = "hyderabad") -> str:
    provider = provider or PvrInoxProvider()
    city = CITIES[region]
    print(f"PVR INOX diagnose — {city.name} (id {city.city_id}) at {fmt_datetime(now_ist())} IST")
    print(f"at most {MAX_REQUESTS} requests, paced; stops at the first refusal\n")
    try:
        # 1 · city
        print("1. content/city")
        record = provider.city_record(region)
        expected = record.get("cinemaCount")
        print(f"  {record.get('name')} · id {record.get('id')} · state {record.get('state')} · "
              f"cinemaCount {expected}")

        # 2 · cinemas
        print("\n2. content/cinemas")
        rows = provider.cinemas(region)
        print(f"  cinemas returned: {len(rows)} (city says {expected})")
        if isinstance(expected, int) and len(rows) < expected:
            print("  ::warning::fewer cinemas than the city's own count — the list is incomplete")
        screen_types: Counter = Counter()
        for row in rows:
            screens = row.get("screens") if isinstance(row.get("screens"), dict) else {}
            types = sorted({str(s.get("screenType", "")) for s in screens.values() if isinstance(s, dict)})
            screen_types.update(types)
            print(f"  {row.get('theatreId'):>6} · {row.get('name')} · shows {row.get('showCount')} · "
                  f"screens {len(screens)} {types}")
        print(f"  screen types across the city: {dict(screen_types.most_common())}")
        if not rows:
            raise Malformed("the city has no cinemas in the answer")

        # 3 · now showing
        print("\n3. content/nowshowing")
        films = provider.list_movies(region)
        print(f"  film/language rows: {len(films)}")
        print(f"  sample: {[f'{m.title} · {m.language}' for m in films[:8]]}")

        # 4 · one cinema's shows today
        if provider.requests_sent >= MAX_REQUESTS:
            raise PlatformError("request ceiling reached before the showtimes read")
        busiest = max(rows, key=lambda r: int(r.get("showCount") or 0))
        today = sale_dates(days=1)[0]
        print(f"\n4. content/csessions — {busiest.get('name')} ({busiest.get('theatreId')}) on {today}")
        body = {"city": city.name, "cid": str(busiest.get("theatreId")), "lat": city.lat, "lng": city.lng,
                "dated": f"{today[:4]}-{today[4:6]}-{today[6:]}", "qr": "NO", "cineType": "", "cineTypeQR": ""}
        status, payload = provider.post("content/csessions", body, city.name)
        output = _ok_output(payload) if status == 200 else None
        print(f"  HTTP {status} · body status {payload.get('status') if isinstance(payload, dict) else '—'}"
              f" · output keys: {_keys(payload.get('output') if isinstance(payload, dict) else None)}")
        if output is None:
            print("  today is not on sale at this cinema (a real answer, not an error)")
        else:
            _show_summary(output)
    except PlatformBlocked as exc:
        print(f"\n  refused: {exc}")
        return _verdict_for_block(exc)
    except Malformed as exc:
        print(f"\n  malformed: {exc}")
        return MALFORMED
    except PlatformError as exc:
        text = str(exc)
        print(f"\n  error: {text}")
        return MALFORMED if ("shape" in text or "without" in text or "isn't a JSON" in text) else API_ERROR
    finally:
        print(f"\nrequests sent: {provider.requests_sent}")
    return OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only PVR INOX reachability check")
    parser.add_argument("--city", default="hyderabad", choices=sorted(CITIES))
    args = parser.parse_args(argv)
    verdict = run(region=args.city)
    print(f"\n{verdict}")
    if verdict != OK:
        print(f"::error::{verdict}")
    return 0 if verdict == OK else 1


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["API_ERROR", "BLOCKED", "MALFORMED", "MAX_REQUESTS", "OK", "RATE_LIMITED", "run"]
