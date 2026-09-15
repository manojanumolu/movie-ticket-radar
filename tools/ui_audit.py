#!/usr/bin/env python3
"""Walk every page and wizard step in a real browser and audit the layout.

    streamlit run app.py --server.port 8601 --server.headless true   # in one shell
    python tools/ui_audit.py 375 390 412 430 768 1366 1440 1600      # in another

For each width it drives the app end to end — city, movie search and Browse
all, a poster, View all theatres, a featured theatre, Any format, the
monitoring step, the step rail back, then My Monitors, History, Settings —
and at every stop checks:

* ``document.documentElement.scrollWidth <= innerWidth`` (and body): no
  horizontal page overflow;
* no element's right edge past the viewport (sidebar and popovers excluded);
* no leaf text box narrower than ~2 characters yet several lines tall —
  the one-letter-per-line wrap that a squeezed column produces.

Screenshots land in ``tools/ui_audit_shots/``. Exit status is non-zero when
any stop fails, so this can gate a release. Needs ``playwright`` and its
Chromium (``pip install playwright && playwright install chromium``); it is
deliberately not part of the pytest suite, which must stay network- and
browser-free.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

OUT = Path(__file__).resolve().parent / "ui_audit_shots"
URL = "http://localhost:8601/"

AUDIT_JS = """
() => {
  const vw = window.innerWidth;
  const wide = [], narrow = [], seen = new Set();
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const cs = getComputedStyle(el);
    if (cs.position === 'fixed') continue;
    if (r.right > vw + 1 && !el.closest('[data-testid="stSidebar"]') && !el.closest('[role="listbox"]')) {
      const key = el.tagName + '.' + String(el.className).slice(0, 60);
      if (!seen.has(key)) { seen.add(key); wide.push({tag: el.tagName, cls: String(el.className).slice(0, 80), right: Math.round(r.right)}); }
    }
    if (el.children.length === 0 && el.textContent && el.textContent.trim().length >= 6) {
      const fs = parseFloat(cs.fontSize) || 14;
      if (r.width < fs * 2.2 && r.height > fs * 3 && cs.whiteSpace !== 'nowrap') {
        narrow.push({text: el.textContent.trim().slice(0, 40), w: Math.round(r.width), h: Math.round(r.height)});
      }
    }
  }
  return {vw, docW: document.documentElement.scrollWidth, bodyW: document.body.scrollWidth,
          wide: wide.slice(0, 20), narrow: narrow.slice(0, 20)};
}
"""


def main(widths: list[int]) -> int:
    from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

    OUT.mkdir(exist_ok=True)
    failures: list[tuple[int, str, dict]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width in widths:
            mobile = width < 800
            ctx = browser.new_context(viewport={"width": width, "height": 2600 if mobile else 1700},
                                      device_scale_factor=1, is_mobile=mobile, has_touch=mobile)
            page = ctx.new_page()

            def audit(stage: str) -> None:
                time.sleep(1.0)
                a = page.evaluate(AUDIT_JS)
                ok = a["docW"] <= a["vw"] and a["bodyW"] <= a["vw"] and not a["wide"] and not a["narrow"]
                print(f"[{'OK ' if ok else 'BAD'}] {width}px {stage:<13} doc={a['docW']} body={a['bodyW']} "
                      f"wide={len(a['wide'])} narrow={len(a['narrow'])}")
                if not ok:
                    failures.append((width, stage, a))
                page.screenshot(path=str(OUT / f"w{width}_{stage}.png"))

            def click(key: str, wait: float = 1.4) -> None:
                page.locator(f'[class*="st-key-{key}"] button').first.click()
                time.sleep(wait)

            def nav(label: str) -> None:
                if mobile:
                    opener = page.locator('button[data-testid="stExpandSidebarButton"]')
                    if opener.count() and opener.first.is_visible():
                        opener.first.click()
                        time.sleep(.6)
                page.locator('[data-testid="stSidebar"] [role="radiogroup"] label', has_text=label).first.click()
                time.sleep(1.4)
                if mobile:
                    try:
                        page.locator('[data-testid="stSidebarCollapseButton"] button').first.click(timeout=1500)
                        time.sleep(.5)
                    except PWTimeout:
                        pass

            page.goto(URL, wait_until="networkidle")
            page.wait_for_selector(".tr-hero", timeout=30000)
            time.sleep(1.2)
            audit("home")
            click("loc_hyderabad")
            audit("movie")
            box = page.locator(".stSelectbox input").first
            box.fill("aveng")
            time.sleep(.8)
            page.screenshot(path=str(OUT / f"w{width}_search.png"))
            page.keyboard.press("Escape")
            box.fill("")
            page.locator('[data-testid="stExpander"] summary').first.click()
            time.sleep(1.0)
            audit("browseall")
            page.locator('[class*="st-key-pick_movie_"] button').first.click()
            time.sleep(1.6)
            audit("theatres")
            view_all = page.locator('[class*="st-key-view_all_theatres"] button')
            if view_all.count():
                view_all.first.click()
                time.sleep(1.4)
                audit("theatres_all")
            page.locator('[class*="st-key-pick_feat_"] button').first.click()
            time.sleep(1.4)
            click("th_continue")
            audit("formats")
            page.locator('[class*="st-key-fmt_"][class*="_any"] label').first.click()
            time.sleep(1.2)
            click("fmt_continue")
            audit("monitoring")
            click("step_2")
            audit("rail_back")
            nav("My Monitors")
            audit("monitors")
            nav("History")
            audit("history")
            nav("Settings")
            audit("settings")
            ctx.close()
        browser.close()

    if failures:
        print("\nFAILURES:")
        for width, stage, a in failures:
            print(f"  {width}px {stage}: {json.dumps({k: a[k] for k in ('docW', 'bodyW', 'wide', 'narrow')})[:600]}")
        return 1
    print(f"\nclean at every stop for {', '.join(str(w) for w in widths)}px")
    return 0


if __name__ == "__main__":
    sys.exit(main([int(w) for w in sys.argv[1:]] or [375, 390, 412, 430, 768, 1366, 1440, 1600]))
