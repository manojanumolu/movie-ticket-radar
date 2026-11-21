# Movie Explorer — Premium Upgrade: Implementation & Verification Report

Date: 21 Nov 2026 (UTC) · Branch: `claude/optimistic-thompson-07yce7`

## Status at a glance

| Stage | Status |
|---|---|
| Implemented and tested locally | **PASS** — full suite 2,139 passed / 0 failed (§9) |
| Verified with **real BookMyShow data** (merged code, local server, Chromium) | **PASS** — §14.2 |
| CI verified | **PASS** — branch run 44939107965 and `main` merge run 44939907285, both success (§11) |
| Merged into `main` | **PASS** — merge commit `954554d` (§12–§13) |
| Deployment of `954554d` to Streamlit Cloud | **PENDING** — not observable from here (§14.1) |
| Verified on the live deployed Explorer | **PENDING** — blocked by sign-in (§14.1) |
| Opt-in browser suite | 1 pre-existing intermittent failure in sign-in cookie clearing, unrelated (§10) |

Scope delivered: live ticket prices in every row and above the results, a grouped format
picker with correct Dolby Atmos / multi-format filtering, a phone scroll-jump fix, and a faster
data path. Nothing was invented: every price shown is BookMyShow's own `curPrice` from the read on
screen, and every format is read from the show's own BookMyShow text.

---

## 1. Original problems

1. **No prices.** A row had no ticket price; you had to open each BookMyShow link.
2. **Format filter missed most shows.** Choosing "Dolby Atmos" listed only shows whose format string was exactly "Dolby Atmos". BookMyShow writes Atmos as "Atmos", "4K Dolby Atmos", "Laser 4K Atmos", "Barco Flagship Laser Dolby Atmos", "Infinity Vision Dolby Atmos"… — all missed.
3. **Long, unstructured Format dropdown.** Every whole string was an option (2K, 2K Air Cooled, 2K Dolby, 2K Laser Dolby 7.1, …), alphabetically, with no grouping.
4. **"Select all" ≠ "All formats".** Selecting every option hid shows that list no format at all.
5. **Sometimes slow / jumpy.**

## 2. Root causes

| Problem | Root cause (file) |
|---|---|
| 1 | Prices were already parsed (`platforms.bookmyshow.parse_seat_categories` → `Showtime.categories[].price`) but `explorer.source.shows_from_day` dropped them when building `ExplorerShow`. |
| 2 | `explorer.query._matches_format` compared the chosen value to each whole format string (casefolded equality). |
| 3 | `explorer.query.options` offered every distinct raw string. **Also:** in By theatre the options were built from the *city-wide* read, and in both modes from every date BookMyShow answered — so the list included formats from other theatres and other dates. |
| 4 | OR over every option still requires a show to have *some* option; a show with no format text has none. |
| 5a | **Phone scroll jump (found in browser testing):** a filter tap reruns the page and the results shrink; Chrome's scroll anchoring then moved the page ~840px, carrying the open picker off screen so the next tap missed. Measured: `stMain.scrollTop` 1279 → 2118 after one tap (iPhone 13 viewport). |
| 5b | Building shows from one read re-ran `classify()` (≈60 locality regexes ×2) per show, and rebuilt the same booking URL per show; for a 711-show city-wide read this was ~22.6 ms, repeated for every film in By theatre. |
| 5c | `read_many` parsed the 611 KB snapshot file (`data/showtimes.json`, ~3.8 ms) on every load, even when every live read succeeded and the snapshot was never used. |

## 3. Files changed

| File | Change |
|---|---|
| `explorer/prices.py` (new) | `SeatPrice`, `PriceRange`, `parse_amount`, `rupees`, `of_show`, `summarise`, `detail`, `NOTE` |
| `explorer/formats.py` (new) | Feature rules over BookMyShow text, four groups, `features_of`, `grouped`, `effective` (Select all ≡ All), `matches` |
| `explorer/query.py` | `ExplorerShow.seats`, cached `price` / `features` / `format_keys`; feature-based OR matching (raw strings still accepted); dedupe keeps the priced copy; `options(date_code, venue_code)` returns features for the chosen date/theatre only; `price_summary` |
| `explorer/source.py` | `seat_prices()` from live categories; snapshot shows always `seats=()`; per-read booking-link memo; lazy, once-per-query snapshot load |
| `explorer/classify.py` | `classify()` cached (`lru_cache`) |
| `ui/explorer.py` | Format popover with grouped pills, Select all / Clear, active-format chips with **Clear formats**; price column in rows; **Tickets** summary; prices in grouped view; price note; scroll anchoring off on this page |
| `ui/theme.py` | Row layout (facts under the name; price and Book in their own columns; phone: price + Book on their own row), price styles, picker styles |
| `tests/test_explorer_premium_upgrade.py` (new) | 55 regression tests (§8) |
| `tests/test_showtime_explorer.py` | One line: the format step drives the new pill group (`sx_fmt_std`) instead of the removed multiselect; same assertion |
| `docs/showtime-explorer.md` | Prices, format filter, performance sections |

Not touched: `platforms/`, monitoring, catalogue sync, `data/`, Firebase config/rules, auth, `.github/workflows/`, `requirements*`/locks. No new dependency.

## 4. Exact pricing data source

The endpoint the Explorer already reads:

```
GET https://in.bookmyshow.com/api/movies-data/v4/showtimes-by-event/primary-dynamic?eventCode=…&dateCode=…
data.showtimeWidgets[].…venue-card.showtimes[].additionalData.categories[] =
  {"priceDesc": "GOLD", "priceCode": "0002", "curPrice": "295.00", "availStatus": "3", …}
```

This shape is documented in `platforms/bookmyshow.py` as verified from a live read (Sept 2026) and
already parsed by `parse_seat_categories` for monitoring. **No additional request** is made for
prices (test `test_6_prices_need_no_extra_request`: 3 reads before and after). No third-party source.

## 5. Price availability and limitations

| BookMyShow listed | Row shows |
|---|---|
| all categories priced, different | `₹150 – ₹450` · "3 seat types" |
| all priced, one price | `₹295` |
| some priced, some not | `₹180 onwards` · "1 of 2 seat types priced" (max never shown) |
| no categories, or price missing/0/unreadable | `Price unavailable` |

- Hover shows each category in BookMyShow's words, with sold-out flags.
- **Tickets** line above the results: range across listed shows; "Listed for X of Y shows" when not all; a range only if every show is fully priced, else "₹X onwards".
- **Stale prices:** the snapshot fallback (up to 30 h old) stores no prices and the code forces `seats=()` for it; prices are never carried between reads. A cached live read (≤10 min) shows the prices of that read, and the freshness line already states its age.
- **Taxes/fees:** the payload does not say whether `curPrice` includes GST, and BookMyShow adds booking fees at checkout. So the UI says "Listed price per ticket; final amount at seat selection" and does not claim tax inclusion either way.
- **Limitation:** prices appear only when the live read succeeds. On a host where BookMyShow's bot check refuses the Streamlit server, the page falls back to the snapshot and every row honestly says "Price unavailable".
- **Verified against real BookMyShow (22 Nov 2026, 00:46 IST read):** live responses do carry `curPrice` per category; see §14.2 for the independent comparison (0 mismatches).

## 6. Format categorisation and filtering behaviour

`explorer/formats.py` reads each show's format strings into features (whole-word, case-insensitive):

- **Standard:** 2D, 3D, 2K
- **Premium screen & projection:** IMAX, Dolby Cinema, 4K, 4K Laser (4K + laser in the same string), Laser projection, RGB Laser, HDR, PXL, 4DX, MX4D, Infinity Vision, EPIQ, PCX, Macro XE, ICE, LED screen, 70MM, Barco projection, Christie projection
- **Premium audio:** Dolby Atmos ("Atmos" or "Dolby Atmos"), Dolby 7.1, 7.1 surround (no Dolby named), Dolby sound (Dolby, no system named)
- **More formats:** LUXE, Recliner, VIP screen, Large screen, Air cooled, Air conditioned, Playhouse, and any unrecognised string kept whole ("Screen 3")

Verified against every format string in the synced data (`data/showtimes.json`, 64 distinct strings; test `test_every_real_bookmyshow_format_string_has_a_group`). Never inferred: Dolby Cinema ≠ Atmos; 4K ≠ laser; "7.1" without Dolby ≠ Dolby 7.1; a theatre's *name* (e.g. "Sudarshan 35MM 4k Laser & Dolby Atmos") never gives its shows a feature. Rows still show BookMyShow's own text in their badges.

Behaviour:
- **OR** across selections; a show with Atmos and 4K Laser appears under either.
- Options limited to shows on the chosen date, and in By theatre to that theatre.
- **Select all ≡ All formats** (all ticked = no filter), so no-format shows never vanish.
- Closed field is one line ("All formats", "4K Laser, Dolby Atmos", "A, B +2"), accent-styled when active; popover max `min(72vh, 560px)`, scrolls inside; Select all / Clear at top; chips + **Clear formats** under the filters.
- Choice kept across view switch, mode switch and date change (if still offered); old raw-string values remain accepted by the query layer.

## 7. Performance — before/after

Same harness and data both sides (`git stash` for "before"); real venues/formats from `data/showtimes.json`.

| Measurement | Before | After |
|---|---|---|
| Build shows from a 711-show read, warm (median of 30) | 22.6 ms | **7.5 ms** (now also parsing prices) |
| Same, cold caches | 22.7 ms | **9.6 ms** |
| Snapshot JSON parse per load when live reads succeed | 3.8 ms (611 KB) | **0** (only parsed on fallback, once per query) |
| Phone: page scroll after a format tap | +839 px jump (next tap missed) | **0 px** |
| Format filter over 289 shows | 0.38–0.42 ms | 0.39–0.58 ms (feature sets cached per show) |
| Time-list markup, 120 rows | 0.47 ms | 1.1 ms (price column + tooltips) |
| Whole-page rerun in AppTest (window change, median) | 64–71 ms | 57–58 ms — within noise (±15 ms run to run); **not claimed** as an improvement |
| BookMyShow requests per selection | 3 | 3 |

Investigated and left alone, with reasons:
- **Duplicate requests:** none found — process-wide cache + single-flight already in place; filters never read (tested).
- **Large HTML per rerun:** the 122 KB theme and 60 KB of Explorer decor are re-generated each run but Streamlit 1.67's forward-message cache sends unchanged ≥10 KB elements once per session (as `tests/test_stylesheet_payload.py` documents), so no wire cost to fix.
- **Cache-miss latency** (the main real-world wait): live reads of a film with premium siblings keep the provider's 1 s politeness gap between events and the token bucket. Not reduced — that protects against BookMyShow's bot check.
- **`st.fragment`** for the filter region could skip the app shell (~15 ms of a rerun) but would change rerun/state semantics across the page; not done.

## 8. Regression tests (`tests/test_explorer_premium_upgrade.py`, 55 tests)

| # | Requirement | Tests |
|---|---|---|
| 1 | Price present | `test_1_a_price_present_is_shown_in_its_row` |
| 2 | Price unavailable | `test_2_no_price_says_price_unavailable`, `test_2_an_unusable_listed_price_is_unknown_never_zero` (9 cases) |
| 3 | Minimum only | `test_3_only_the_minimum_known_says_onwards` |
| 4 | Range | `test_4_a_range_and_a_single_price`, `test_4_the_summary_above_the_results`, `test_4_the_summary_is_a_range_only_when_every_show_is_fully_priced` |
| 5 | Stale prices | `test_5_a_snapshot_never_supplies_a_price`, `test_5_prices_are_never_carried_from_an_earlier_read`, `test_5_a_duplicate_keeps_the_copy_that_listed_prices` |
| 6 | Multiple seat categories | `test_6_multiple_seat_categories_from_the_provider`, `test_6_prices_need_no_extra_request` |
| 7 | Dolby Atmos | `test_7_dolby_atmos_finds_every_show_that_names_it`, `test_7_dolby_cinema_and_dolby_7_1_are_not_atmos` |
| 8 | Atmos + 4K Laser | `test_8_atmos_with_4k_laser_is_found_by_either` |
| 9 | Multiple selections | `test_9_several_formats_match_any_of_them`, `test_the_picker_groups_what_the_shows_offer` |
| 10 | Clear | `test_10_clear_shows_everything_again` |
| 11 | Select All | `test_11_select_all_is_all_formats` |
| 12 | Missing/unknown formats | `test_12_missing_and_unknown_formats`, 12 parametrised `test_formats_are_read_from_bookmyshows_own_text`, `test_distinct_technologies_are_never_folded_together`, `test_every_real_bookmyshow_format_string_has_a_group` |
| 13–15 | Full name, locality, booking link | `test_13_14_15_full_name_locality_and_booking_link_survive_the_price` |
| 16 | Date change | `test_16_a_new_date_reads_again_and_keeps_the_format_choice` |
| 17 | Time window | `test_17_the_time_window_survives_format_and_price_work` |
| 18 | Session state | `test_18_session_state_is_the_pages_own_and_consistent`, `test_18_a_group_view_carries_prices`, `test_format_options_are_only_the_chosen_theatres_and_dates` |
| 19 | Cache | `test_19_format_and_price_work_never_reads_bookmyshow`, `test_19_the_snapshot_file_is_not_read_when_live_reads_succeed`, `test_19_theatre_classification_is_cached`, `test_19_one_read_builds_each_theatres_link_once` |
| 20 | Layout | `test_20_the_row_keeps_price_and_book_in_their_own_columns`, `test_20_the_format_picker_is_short_and_the_page_never_jumps`, `test_20_price_markup_never_overlaps_book_in_markup_order` (+ browser checks §10) |

Mutation check: reverting the feature matching or the Select-all rule makes 6 of these tests fail (then restored).

## 9. Full test-suite results

`python -m pytest --timeout=300` (Python 3.13, deps from `ci/requirements-dev.lock`):

| | Collected | Passed | Failed | Skipped |
|---|---|---|---|---|
| Before (baseline) | 2,132 | 2,084 | 0 | 48 |
| After | 2,187 | **2,139** | **0** | 48 |

The 48 skips are the opt-in browser tests (§10). One mid-work run caught 2 failures from my own CSS
(a `font-family` in the Explorer theme section, which the theme forbids); fixed, then the full run above.

Baseline before any change (same environment): no failures (after `git fetch --unshallow`; one existing test reads fixed old commits and needs full history, as CI's `fetch-depth: 0` provides).

## 10. Browser test results

**Repository's opt-in browser suite** (`TR_BROWSER_TESTS=1`): 47 passed, 2 skipped (by design: narrow viewports), **1 failed** —
`test_auth_bridge_browser.py::test_an_invalid_cookie_is_the_login_page_and_goes_on_the_next_run`, which times out
waiting for the login page. It is **flaky and pre-existing**: run alone 4 times each, it failed 3/4 on the
baseline code and 2/4 with this change, alternating between its desktop and mobile variants. It tests the
sign-in cookie bridge, which this work does not touch. It was not modified.

Investigation (after the merge):
- With a temporary, uncommitted 60 s wait, the passing runs drop the cookie in 0.0–0.2 s. The failing run **never** drops it within 60 s. So it is not slowness; the clear is lost.
- Server log of a failing run: the forged cookie is rejected (`restore: FAILED reason=INVALID_REFRESH_TOKEN`). `restore()` then sets `auth_cookie_clear`, and `flush_cookie()` queues the clear for the browser bridge component (`auth/session.py`). Every later run still reports `session_cookie=True`, so the bridge did not deliver the queued clear.
- It reproduces on the pre-merge baseline `a449c5c` (3 of 4 runs). `auth/`, the bridge component and Firebase code are not in this change.
- **Conclusion:** a pre-existing intermittent defect in delivering a cookie *clear* through the sign-in bridge. Its effect: an invalid session cookie can survive a failed sign-in, so a later visit spends one more refresh call before showing the login page. It does not sign anyone in. It is left for a separate auth task, as instructed not to change auth/Firebase code here. (Run with Python Playwright 1.63 in
a scratch venv, matching the pre-installed Chromium build; not a repository dependency.)

**Explorer, real app in Chromium** (project harness `tools/fake_firebase_app.py`, in-memory Firebase; a scratch launcher, not committed, replaced BookMyShow with a fake answering from the real synced venues/format strings and **fixture prices** — the screenshots' prices are test values, not BookMyShow's):

| Check | Desktop 1440×900 | Phone (iPhone 13) |
|---|---|---|
| Rows rendered | 120 | 120 |
| Price/badges/name overlapping Book | 0 | 0 |
| Elements overflowing their row | 0 | 0 |
| Rows without a Book link | 0 | 0 |
| Horizontal page scroll | none | none |
| Price kinds shown | range 52, single 25, onwards 13, unavailable 30 | same |
| Dolby Atmos → shows | 26, all with Atmos | 26 |
| + 4K Laser (OR) | 28 | 28 |
| Clear formats → | 150 (all) | 150 |
| Select all → | 150, label "All formats" | 150 |
| Picker height | 512 px | 458 px |
| Scroll change on pill tap | 0 | 0 (was +839 px before the fix) |
| By theatre AMB — formats offered | 2D, Laser projection, HDR, Infinity Vision, Barco projection, Dolby Atmos, LUXE, VIP screen (AMB's own only) | — |

Screenshots: `docs/screenshots/explorer-premium-upgrade/`.

## 11. CI runs

| Run | Commit | Result |
|---|---|---|
| [44939107965](https://github.com/manojanumolu/movie-ticket-radar/actions/runs/44939107965) | `db78476` (branch) | **success** — test suite ✅ (Python 3.12), pip-audit ✅ "No known vulnerabilities found" |
| [44939907285](https://github.com/manojanumolu/movie-ticket-radar/actions/runs/44939907285) | `954554d` (merge into `main`) | **success** |

A follow-up commit adding this report's final evidence is merged the same way; its run is given in the session's closing summary (a file cannot hold the SHA of the commit that contains it).

## 12. Final commit

- Feature commit: `db78476` — feat(explorer): listed ticket prices, grouped format filter, faster data path
- Merge commit on `main`: `954554d` — Merge Movie Explorer premium upgrade (db78476) into main (tree identical to `db78476`)

## 13. Merge status

**Merged.** `main` is a protected branch; this repository has no pull requests and every earlier feature landed as a `--no-ff` merge commit pushed to `main` (e.g. `ffa040b`). The same was done: `a449c5c..954554d main -> main`, accepted normally — no force-push, no bypass. The merge was made only after branch CI was green, and `main`'s own CI on the merge is green.

## 14. Deployment verification

### 14.1 The deployed app — PENDING

- `https://movie-ticket-radar.streamlit.app/` was reached in Chromium after the merge: it serves the TicketRadar sign-in page (the app is up).
- The Showtime Explorer is behind Firebase sign-in. This session has no account and must not create or use one, so **the deployed Explorer was not verified**.
- Which commit Streamlit Community Cloud is serving is not observable from here (no deploy-status access; the version tag is visible only after sign-in). It redeploys from `main` on push, but that is not confirmed for `954554d`.
- To confirm yourself: sign in → Showtime Explorer → By movie → Ramayana. Expect a price beside every Book button, a **Tickets** line (on 22 Nov it read ₹50 – ₹666 in §14.2), and Format → Premium audio → Dolby Atmos. If the strip says "From the catalogue sync", BookMyShow refused the Streamlit server and every row will honestly say "Price unavailable".

### 14.2 Real BookMyShow data, merged code — PASS

To get real prices, `in.bookmyshow.com` was added (with the user's approval card) to this session's cloud-environment network allow-list. No repository file was changed for this. Then the merged `main` (`954554d`, clean tree) ran locally through the project's harness `tools/fake_firebase_app.py`: only sign-in is faked; **BookMyShow was real**, with no fake reader installed. **Every price below is BookMyShow's own; none is fixture data.**

Ramayana (Hindi + Telugu rows and their premium siblings), Today 22 Nov, read live at 00:48 IST:

| Check | Desktop 1440×900 | Phone (iPhone 13) |
|---|---|---|
| Showtimes · theatres | 214 · 57 | 214 · 57 |
| Rows rendered (first page) | 120 | 120 |
| Rows with a real listed price | 120 (53 single, 67 ranges) | same |
| Tickets line | ₹50 – ₹666 | same |
| Overlap with Book / row overflow / horizontal scroll | 0 / 0 / none | 0 / 0 / none |
| Dolby Atmos | 36 shows, every one naming Atmos | 36 |
| Dolby Atmos + 4K Laser (OR) | 38 | 38 |
| Clear formats | 214 | 214 |
| Select all | 214, label "All formats" | 214 |
| Scroll change on pill tap | 0 | 0 |
| By theatre, AMB Cinemas: Gachibowli — formats offered | 2D, Laser projection, HDR, Barco projection, Dolby Atmos, LUXE, VIP screen | same |
| First live read (cache miss) | 7.8 s (network + the provider's 1 s politeness gap per sibling event) | 0.9 s (cached) |

Rows match the original screenshot, now priced: Tivoli Cinemas: Secunderabad 8:50 AM 3D Hindi "Laser Dolby 7.1" **₹325** (2 seat types); Aparna Cinemas: Nallagandla 9:00 AM VIP Screen **₹350**; UK Cineplex: Nacharam 9:00 AM **₹295** (2 seat types).

**Independent check** (no Explorer code in the reference): the raw JSON of one live response (Ramayana Telugu base event, 22 Nov) was walked directly for `sessionId`, `screenAttr` and `curPrice`:
- 57 shows compared: **0** price mismatches (the min/max of raw `curPrice` = the Explorer's range).
- Dolby Atmos: raw shows whose `screenAttr` contains "atmos" = 7; Explorer = the same 7.
- Atmos OR 4K Laser: raw = 7; Explorer = the same 7. This response had no 4K-laser screens; the 4K Laser additions in the table above come from the other language row and siblings.

Real examples read: AMB Cinemas: Gachibowli 9:15 AM VIP screen ₹350 (PLATINUM, sold out); Asian Lakshmikala Cinepride: Moosapet 2:20 PM ₹50 – ₹175 (COUPLE SEATS ₹175 sold out, PLATINUM ₹175, GOLD ₹150, SILVER ₹50).

Screenshots with real BookMyShow prices: `docs/screenshots/explorer-premium-upgrade/REAL_BMS_*.jpg`. The other files in that folder (§10) use **fixture prices**.

Not observed in this real read: a "₹X onwards" or "Price unavailable" case (every category was priced). Those are covered by tests §8 and by the fixture browser run in §10.

## 15. Remaining work / limitations

- Live prices depend on the live read succeeding from the Streamlit host (§5).
- Deployed-app verification and the deployed commit are PENDING (§14.1).
- Pre-existing sign-in cookie-clear flake (§10) needs its own auth fix.
- GST inclusion of `curPrice` is not stated by BookMyShow; the UI does not claim it.
- Format vocabulary is rule-based; a new BookMyShow spelling that names no known feature appears whole under "More formats" until a rule is added.
- Cache-miss latency (network + politeness gaps) is unchanged by design (§7).
