#!/usr/bin/env python3
"""Second-pass shape dump: venue cards, showtimes, and poster URLs."""
import argparse, json, pathlib, sys
SITE = "https://in.bookmyshow.com"

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--city", default="hyderabad")
    a = ap.parse_args(argv); code, lat, lon = "HYD", "17.385", "78.487"
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from platforms.http import ImpersonatingSession
    s = ImpersonatingSession()   # rotates profiles on a refusal

    qb = s.get(f"{SITE}/serv/getData", params={"cmd":"QUICKBOOK","type":"MT","f":"json"},
               headers={"Cookie": f"Rgn=Code%3D{code}"}, timeout=30).json()

    print("===== CITY VENUES =====")
    ven = qb.get("cinemas", {}).get("BookMyShow", {}).get("aiVN", {})
    print("aiVN keys:", list(ven.keys()))
    vlist = ven.get("venues")
    if isinstance(vlist, list):
        print("venues len:", len(vlist))
        print(json.dumps(vlist[0], indent=2)[:900])

    events = qb["moviesData"]["BookMyShow"]["arrEvents"]
    print(f"\n===== MOVIE GROUPS: {len(events)} =====")
    for e in events[:6]:
        kids = e.get("ChildEvents", [])
        print(f"  {e.get('EventTitle')!r} grp={e.get('EventCode')} children="
              f"{[(k.get('EventCode'), k.get('EventLanguage'), k.get('EventDimension'), k.get('EventStatus')) for k in kids]}")
    print("\n  EventStatus values seen:",
          sorted({k.get("EventStatus","") for e in events for k in e.get("ChildEvents",[])}))

    # poster URL check
    img = events[0]["ChildEvents"][0].get("EventImageCode","")
    for tpl in ["https://assets-in.bmscdn.com/discovery-catalog/events/tr:w-400,h-600,bg-CCCCCC/{}.jpg",
                "https://in.bmscdn.com/events/moviecard/{}.jpg"]:
        url = tpl.format(img)
        try:
            r = s.get(url, timeout=20)
            print(f"\n  poster {r.status_code} {len(r.content)}B  {url}")
        except Exception as ex:
            print("\n  poster ERR", ex)

    # showtimes for a movie that is actually running (EventStatus AB = advance booking?)
    target = None
    for e in events:
        for k in e.get("ChildEvents", []):
            if k.get("EventStatus") in ("AB","CS","NS") and not target:
                target = (e.get("EventTitle"), k.get("EventCode"), k.get("EventStatus"))
    print(f"\n===== SHOWTIMES {target} =====")
    sh = s.get(f"{SITE}/api/movies-data/v4/showtimes-by-event/primary-dynamic",
        params={"eventCode": target[1], "regionCode": code, "isDesktop":"true","dateCode":"","lat":lat,"lon":lon},
        headers={"x-region-code":code,"x-region-slug":"hyderabad","x-app-code":"WEB",
                 "x-latitude":lat,"x-longitude":lon,"Accept":"application/json"}, timeout=30)
    d = sh.json().get("data", {})
    print("widget types:", [w.get("type") for w in d.get("showtimeWidgets",[])])
    print("topSticky types:", [w.get("type") for w in d.get("topStickyWidgets",[])])
    for w in d.get("topStickyWidgets", []):
        if w.get("type") == "horizontal-block-list":
            print("DATE STRIP sample:", json.dumps(w.get("data",[])[:2], indent=2)[:700])
    for w in d.get("showtimeWidgets", []):
        if w.get("type") != "groupList": continue
        for g in w.get("data", []):
            print("group type:", g.get("type"), "len", len(g.get("data",[])))
            for card in g.get("data", [])[:1]:
                print("card keys:", list(card.keys()))
                print("card.type:", card.get("type"))
                print("additionalData:", json.dumps(card.get("additionalData",{}), indent=2)[:900])
                st = card.get("showtimes", [])
                print("showtimes len:", len(st))
                if st:
                    print("showtime[0]:", json.dumps(st[0], indent=2)[:1600])
            break
        break
    return 0
sys.exit(main())
