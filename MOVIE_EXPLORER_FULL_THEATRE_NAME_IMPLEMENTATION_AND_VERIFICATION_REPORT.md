# Movie Explorer — Full Official Theatre Names: Implementation & Verification Report

Date: 18 Nov 2026 (UTC) · Branch: `claude/relaxed-turing-mw4fer`

## Status at a glance

| Stage | Status |
|---|---|
| Implemented | **Yes** — commit `9beb6a4` |
| Tested locally | **Yes** — 61 new tests pass; full suite 2,084 passed, 0 failed, 48 skipped (the skips are the opt-in browser tests); opt-in browser suite: see §8 |
| Browser-verified locally | **Yes** — real app in Chromium, desktop 1280×800 and mobile 390×844 (§8) |
| CI verified | **Pending** — run in progress at time of writing |
| Merged into `main` | **Not yet** — waits for green CI |
| Verified on live production | **No — blocked.** See §10 |

---

## 1. Original problem

In the Showtime Explorer's **By movie** results and grouped headings, the first line of a theatre showed only the part of BookMyShow's venue name before the colon, which is the chain name:

```
PVR                      PVR                      PVR
Atrium Gachibowli        Preston, Gachibowli      Next Galleria Mall, Panjagutta
```

Eleven PVR branches in Hyderabad all had "PVR" as their first line. The theatre's own name was split across two lines. BookMyShow lists venues as **full name** first, then **locality** (your screenshot).

## 2. Root cause

- BookMyShow publishes one venue string: `"PVR: Atrium Gachibowli, Hyderabad"`. It has no separate area field.
- `platforms.bookmyshow.split_venue_name()` splits it at the first colon. `Venue.name = "PVR"` and `Venue.area = "Atrium Gachibowli, Hyderabad"` are stored that way in `data/catalogue.json` and `data/showtimes.json`. The split is lossless apart from whitespace.
- `ui/explorer.time_rows_markup()` printed `venue_name` (`"PVR"`) on line 1 and `_where()` (the area text) on line 2. `explorer.query.group_by_theatre()` used `venue_name` for the heading title. The By-theatre picker label was `"PVR · Atrium Gachibowli, Hyderabad"`.

**The full official name was already available.** It is `name + ": " + area`, so the ingestion pipeline, stored data and venue codes did not need to change.

## 3. Files changed

| File | Change |
|---|---|
| `explorer/classify.py` | New `venue_display(name, area) -> (title, where)` (cached), plus helper `_trailing_place()` |
| `explorer/query.py` | `ExplorerShow.theatre_title` / `.theatre_where` properties; `group_by_theatre()` uses them for heading title/subtitle |
| `explorer/catalog.py` | `Theatre.title` / `.where`; picker `label` now uses the full name |
| `ui/explorer.py` | Rows use `theatre_title`; `_where()` returns the locality; By-theatre result heading and empty state use the full name; unused `re` / `_CITY_TAIL` removed |
| `tests/test_explorer_full_theatre_names.py` | **New:** 61 regression tests |
| `tests/test_explorer_window_and_theatre_names.py`, `tests/test_showtime_explorer.py`, `tests/test_explorer_by_theatre.py`, `tests/test_explorer_find_a_movie.py` | Expectations that pinned the old `PVR / Atrium Gachibowli` lines and old picker labels updated to the new contract |
| `docs/showtime-explorer.md` | Documents the display-name rule |
| `docs/screenshots/explorer-full-theatre-names/*.jpg` | 7 browser screenshots (§8) |

Not touched: `platforms/bookmyshow.py`, `explorer/source.py`, catalogue sync and stored data, venue codes, booking-URL construction, filters, Firebase credentials and rules, auth, monitoring, workflows.

## 4. Implementation details

`venue_display(name, area)`:

1. **Rebuild the full name** from BookMyShow's two halves. If `area` is empty and `name` still contains a colon, it is split the same way.
2. **Peel trailing place names off the tail (after the colon).** The tail can end with a known Hyderabad place name or with the city. Place names come from the existing `LOCALITIES` vocabulary, aliases included, matched as whole words and case-insensitively. Peeling repeats so `"Hitech, Madhapur, Hyderabad"` drops both `Hyderabad` and `Madhapur`. Separators `, : ; -` and spaces are trimmed.
3. **Never strip the tail to nothing.** If the tail is *only* a place (`"PVR: Musarambagh"`), that place is the branch's distinguishing name and stays on line 1. A tail that is only the city (`"Prasads Multiplex: Hyderabad"`) is dropped, because the city never tells two theatres apart.
4. **Leave the brand prefix (`head`) alone**: `PVR`, `PVR ICON`, `PVR Superplex Inorbit`, `INOX`, `Miraj Cinemas` and the rest. Words after a place name are not cut either (`"Gachibowli SLN Terminus"`, `"Secunderabad (Newly Renovated)"`).
5. **Line 2** is the peeled place names in canonical spelling (`NAMPALLY`→`Nampally`, `Banjara Hil`→`Banjara Hills`). If none were peeled, it is the first known place in the name or area. Otherwise it is `Hyderabad`, the city every catalogue venue is in. No locality is invented.

Where it is used:

- **By movie** rows: line 1 = `theatre_title`, line 2 = `_where()` = `theatre_where`.
- **Grouped headings** (By movie → By theatre): title = `theatre_title`; subtitle starts with `theatre_where`, followed by the existing operator and show-count text.
- **By theatre picker**: `"PVR: Atrium · Gachibowli"`. The `· locality` suffix is left out when the name already contains it (`"AMB Cinemas: Gachibowli"`).
- **By theatre** result heading and the "Nothing listed here" message use the full name.

Unchanged: `ExplorerShow.venue_name`, `.area`, `.locality`, `.operator`, `.venue_code` and `.booking_url`. The Area, Operator and Type filters, dedupe and sort keys therefore work exactly as before.

## 5. Before / after

All 97 venues in the synced catalogue were checked. A selection:

| BookMyShow's venue name | Before (line 1 / line 2) | **After (line 1 / line 2)** |
|---|---|---|
| PVR: Musarambagh, Hyderabad | PVR / Musarambagh | **PVR: Musarambagh** / Musarambagh |
| PVR: RK Cineplex, Hyderabad | PVR / RK Cineplex | **PVR: RK Cineplex** / Hyderabad |
| PVR: Irrum Manzil, Hyderabad | PVR / Irrum Manzil | **PVR: Irrum Manzil** / Irrum Manzil |
| PVR: Central Mall, Panjagutta | PVR / Central Mall, Panjagutta | **PVR: Central Mall** / Panjagutta |
| PVR: Preston, Gachibowli Hyderabad | PVR / Preston, Gachibowli | **PVR: Preston** / Gachibowli |
| PVR: Atrium Gachibowli, Hyderabad | PVR / Atrium Gachibowli | **PVR: Atrium** / Gachibowli |
| PVR: Next Galleria Mall, Panjagutta | PVR / Next Galleria Mall, Panjagutta | **PVR: Next Galleria Mall** / Panjagutta |
| PVR ICON: Hitech, Madhapur, Hyderabad | PVR ICON / Hitech, Madhapur | **PVR ICON: Hitech** / Madhapur |
| PVR: Nexus Mall Kukatpally, Hyderabad | PVR / Nexus Mall Kukatpally | **PVR: Nexus Mall** / Kukatpally |
| PVR Superplex Inorbit: LUXE, PXL, 4DX: Cyberabad | PVR Superplex Inorbit / LUXE, PXL, 4DX: Cyberabad | **PVR Superplex Inorbit: LUXE, PXL, 4DX** / Cyberabad |
| PVR Lakeshore PXL 4K Laser ATMOS DTS-X Y Junction | (same) / Hyderabad | **PVR Lakeshore PXL 4K Laser ATMOS DTS-X Y Junction** / Hyderabad |
| Miraj Cinemas: CineTown, Miyapur | Miraj Cinemas / CineTown, Miyapur | **Miraj Cinemas: CineTown** / Miyapur |
| INOX: Odeon 4K, LASER, ATMOS, DTS-X: RTC X Roads | INOX / Odeon 4K, … : RTC X Roads | **INOX: Odeon 4K, LASER, ATMOS, DTS-X** / RTC X Roads |
| Cinepolis: TNR North City, Suchitra, Hyderabad | Cinepolis / TNR North City, Suchitra | **Cinepolis: TNR North City** / Suchitra |
| MovieMax: AMR, ECIL Secunderabad | MovieMax / AMR, ECIL Secunderabad | **MovieMax: AMR** / ECIL, Secunderabad |
| AMB Cinemas: Gachibowli | AMB Cinemas / Gachibowli | **AMB Cinemas: Gachibowli** / Gachibowli |
| Prasads Multiplex: Hyderabad | Prasads Multiplex / Hyderabad | **Prasads Multiplex** / Hyderabad |

All 12 rows in your reference table match exactly, except one. Your table listed **Miraj Cinemas / Miyapur**, but BookMyShow's own venue name is `Miraj Cinemas: CineTown, Miyapur`. Following "verify against source data, don't hardcode", line 1 is **Miraj Cinemas: CineTown**.

## 6. Test cases and individual results

`tests/test_explorer_full_theatre_names.py`: **61 passed, 0 failed.** On the pre-fix code this file fails at import because `venue_display` does not exist. The old markup also printed `<div class="n">PVR</div>`, which the rendering tests forbid.

| Test | Covers | Result |
|---|---|---|
| `test_full_name_and_locality[…]` ×25 | Preston, Atrium, Next Galleria, Central Mall, Nexus Mall, Lakeshore, ICON Hitech, Superplex Inorbit, Musarambagh, Irrum Manzil, RK Cineplex, Miraj CineTown and Geeta, INOX Odeon and SMR Vinay, Cinepolis TNR and Lulu, CONNPLEX, MovieMax, ROONGTA, INOX GVK One, Sudarshan (single screen), AMB, Platinum Movietime, Prashant | 25 PASSED |
| `test_the_full_name_is_never_replaced_by_the_chain[…]` ×25 | Line 1 starts with the brand exactly as written and is never the brand or operator alone | 25 PASSED |
| `test_a_venue_with_no_distinguishing_name_keeps_what_bookmyshow_gave` | Prasads Multiplex, Aradhana Theatre, bare "PVR" | PASSED |
| `test_missing_or_incomplete_locality_is_never_invented` | Empty area, city-only area, unknown area, empty input, unsplit colon name | PASSED |
| `test_whitespace_is_tidied_without_changing_the_words` | Whitespace normalisation | PASSED |
| `test_the_second_line_never_repeats_the_whole_address` | Line 2 is locality only | PASSED |
| `test_every_catalogue_venue_has_a_unique_full_name` | All 97 real catalogue venues: non-empty, brand kept, unique line 1; every PVR reads `PVR: <branch>` | PASSED |
| `test_by_movie_rows_show_name_then_locality` | Rendered By-movie markup, exact (name, locality) pairs | PASSED |
| `test_grouped_headings_show_name_then_locality` | Rendered grouped headings | PASSED |
| `test_duplicate_chains_in_one_locality_stay_separate_with_their_own_links` | Two PVRs in Gachibowli and two in Panjagutta: separate groups, distinct codes, one distinct BookMyShow URL each containing its own venue code; stored fields unchanged | PASSED |
| `test_the_area_filter_is_unchanged` | Locality keys for the Area filter | PASSED |
| `test_the_by_theatre_picker_uses_the_same_names` | Picker labels | PASSED |
| `test_every_catalogue_picker_label_is_unique` | No picker collisions on the real catalogue | PASSED |

Updated existing tests: `test_explorer_window_and_theatre_names.py` (27/27 pass, theatre-name section rewritten to the new contract), `test_showtime_explorer.py`, `test_explorer_by_theatre.py`, `test_explorer_find_a_movie.py` (Streamlit `AppTest` page tests that drive the real page, picker included).

## 7. Full test-suite results (local)

- Command (same as CI): `python -m pytest -q --timeout=300` with `ci/requirements-dev.lock` installed.
- Result: **2,084 passed, 0 failed, 48 skipped** (all 48 are the opt-in `TR_BROWSER_TESTS=1` browser tests) in 123 s.
- Explorer and catalogue groups (`test_showtime_explorer`, `test_explorer_*`, `test_catalogue`): all pass.
- Opt-in browser suite (`TR_BROWSER_TESTS=1`: auth bridge, bridge origin, transitions): pending at time of writing.
- Lint: `pyflakes` reports nothing on the changed modules. `ruff` is not part of this project's CI or config. With its defaults, the only remaining findings in the touched files were there before this change; the one new finding (`re.I` in the new test) was fixed.
- Local Python 3.13; CI uses 3.12 (see §9).

## 8. Browser verification and screenshots

Harness: the project's own browser infrastructure (`tests/test_auth_bridge_browser.Server` and `sign_in`, running the real `app.py` through `tools/fake_firebase_app.py` with in-memory Firebase), in Chromium.

The sandbox cannot reach BookMyShow, so the page fell back to the catalogue sync's snapshot (`data/showtimes.json`), as it is designed to do. The harness only copies `catalogue.json`, so for this run a scratch copy of the harness also copied `showtimes.json`. Nothing in `tools/` was changed.

Flow per viewport: sign in → Showtime Explorer → By movie "Ramayana" (Today) → time list → Area filter "Gachibowli" (desktop) → By theatre grouping → By theatre mode → picker search "PVR" → select "PVR: Atrium · Gachibowli".

Measured in the DOM, desktop and mobile:

| Check | Desktop | Mobile |
|---|---|---|
| By-movie rows rendered | 120 | 120 |
| Rows whose line 1 is just "PVR" | 0 | 0 |
| Locality rendered below the name (separate line) | all | all |
| Name overflowing its box / past the row | 0 / 0 | 0 / 0 |
| Rows without a Book link | 0 | 0 |
| Long name `PVR Lakeshore PXL 4K Laser ATMOS DTS-X Y Junction` | one line, readable | wraps to 2 lines, locality below |
| Area filter "Gachibowli" | AMB Cinemas: Gachibowli · PVR: Atrium · PVR: Preston · Platinum Movietime Cinema: Gachibowli SLN Terminus | — |
| Grouped headings | e.g. "PVR: Preston" / "Gachibowli · PVR · 5 shows" | same |
| By-theatre picker ("PVR") | PVR: Musarambagh, PVR: Irrum Manzil, PVR: Atrium · Gachibowli, PVR: Preston · Gachibowli, PVR ICON: Hitech · Madhapur, PVR: Nexus Mall · Kukatpally, … | same |
| By-theatre results (PVR: Atrium) | 16 showtimes grouped by film | same |

Booking links keep their per-venue form, for example `https://in.bookmyshow.com/buytickets/ramayana-telugu-hyderabad/cinema-hyde-ILKS-MT/20261119`.

Screenshots in `docs/screenshots/explorer-full-theatre-names/`:

- `desktop_by_movie_list.jpg`: time list (PVR: Atrium / Gachibowli, PVR Superplex Inorbit: LUXE, PXL, 4DX / Cyberabad, …)
- `mobile_by_movie_long_name.jpg`: long names wrapping on a phone, locality on its own line
- `desktop_grouped_headings.jpg`, `mobile_grouped_headings.jpg`: grouped headings
- `desktop_area_filter_gachibowli.jpg`: Area filter
- `desktop_by_theatre_picker.jpg`: By-theatre picker
- `desktop_by_theatre_results_list.jpg`: By-theatre results

Compared with your BookMyShow screenshot, line 1 now carries the branch name the way BookMyShow does ("PVR: Preston", "PVR ICON: Hitech", "PVR Lakeshore PXL 4K Laser ATMOS DTS-X Y Junction"). Line 2 holds only the locality, not BookMyShow's full street address.

Posters show as broken images in the screenshots only because the sandbox has no network.

## 9. Git branch, commits and merge status

- Starting state: branch `claude/relaxed-turing-mw4fer` = `origin/main` = `a421230`, clean working tree.
- Commit: `9beb6a4` — fix(explorer): full official theatre name on line one, locality on line two.
- Report commit: on the same branch, after `9beb6a4`.
- CI (GitHub Actions "Tests", run [44381701942](https://github.com/manojanumolu/movie-ticket-radar/actions/runs/44381701942)): in progress at time of writing.
- Merge: not yet merged; will be a `--no-ff` merge commit into `main` once CI is green, as in earlier merges.

## 10. Live deployment verification

**Not verified on live production.**

`https://movie-ticket-radar.streamlit.app/` is reachable from the sandbox (HTTP 303), but the Showtime Explorer is behind Firebase sign-in. This session has no account credentials and must not create or use any. Streamlit Community Cloud redeploys from `main` on push; this session has no access to its deploy status.

To confirm: open the app, sign in, go to Showtime Explorer → By movie, and choose a film playing at several PVR branches. The rows should read "PVR: Atrium / Gachibowli", "PVR: Preston / Gachibowli" and so on.

## 11. Remaining limitations

- **Locality vocabulary.** Line 2 relies on the existing `LOCALITIES` list. A place BookMyShow names that is not in that list (for example "Y Junction" in the Lakeshore name) is not recognised, so line 2 falls back to "Hyderabad" rather than guessing. The name itself is always kept whole.
- **Words after a place stay in the name.** Examples: "Platinum Movietime Cinema: Gachibowli SLN Terminus" and "Prashant Cinema: Secunderabad (Newly Renovated)". This is deliberate: nothing is cut from the middle of an official name.
- **Picker search text changed.** Searching the By-theatre picker for "Hyderabad" no longer matches theatres whose area text only ended in ", Hyderabad". Name and locality searches still work.
- **Live verification** is blocked (§10).
