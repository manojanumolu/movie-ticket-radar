# Showtime Explorer

*Find your movie. Find your theatre. Find your time.*

A browsing page inside TicketRadar (sidebar → **Showtime Explorer**). It never
creates, changes or deletes a monitor.

## Modes

| Mode | You choose | You get |
| --- | --- | --- |
| **By movie** | a title (all its languages), date, time window; area, operator, theatre type, language, format | every matching show across Hyderabad |
| **By theatre** | a theatre, date, time window; language, format | every film and show at that theatre |

The results can be shown **by time** (chronological, across every theatre, in hour bands) or
**grouped** (by theatre for a movie, by movie for a theatre). Switching views or modes keeps
the date, the time window, language and format. Switching to By theatre clears area, operator
and type, because they mean nothing for a single theatre.

## Data sources

| Need | Source | Requests |
| --- | --- | --- |
| Movie and theatre pickers | `data/catalogue.json` (scheduled sync) | none |
| By movie showtimes | `showtimes-by-event` (the endpoint monitoring uses) for each language row and each premium sibling event | 1 + siblings, per language |
| By theatre showtimes | the same endpoint, only for films the catalogue lists at that theatre on that date (falling back to the films' theatre lists past the catalogue's per-date sweep) | ≤ `MAX_EVENTS` (40) events |
| Fallback | `data/showtimes.json`, the times the catalogue sync already read (no extra requests) | none |

BookMyShow's theatre page has a venue-level source, but its request shape has not been verified
from a runner, so it is not used. Probing it with `tools/bms_diagnose.py` is the next step
before relying on it.

## Freshness

Every result names where it came from: **Live** (read in the last 2 minutes), **Cached**
(served from the 10-minute cache, with its age), **From the catalogue sync** (not live, with
its read time), or a failure. A partial read (a premium sibling that failed) says so.
"Sold out" appears only when every seat category BookMyShow returned says so. A listed show
is never presented as having seats.

## Safeguards (`explorer/source.py`)

- One process-wide cache keyed by `(event, date)`, shared by all sessions. Filters, view
  switches and reruns never trigger a read.
- Single-flight per key. A token bucket (30 burst, 0.5/s). At most 2 requests in flight.
- 45-second refresh cooldown.
- A 403 opens a 5-minute circuit, and the page falls back to the snapshot.
- 429/5xx are retried with backoff by the provider. Timeouts and malformed JSON are honest failures.
- The Explorer runs in the Streamlit process. The monitor worker runs on GitHub runners with its own client.

## Classification (`explorer/classify.py`)

These come only from BookMyShow's own theatre name and area text:

- **Operator**: the brand must start the name (PVR, INOX, Cinepolis, AMB Cinemas, Asian…, Miraj
  Cinemas). Everything else is "Independent & others".
- **Type**: *Multiplex* when the name says Multiplex/Cineplex/Superplex, or the operator is a
  national multiplex chain. *Single screen* when the hall is listed by its film gauge (70MM/35MM).
  Anything else is *Unknown*, and those theatres appear only under "All theatres".
- **Locality**: a vocabulary of Hyderabad place names, matched in the theatre's area or name. If
  no place is named, the locality is *Unknown* and is never guessed.
- **Display name** (`venue_display`): every theatre is shown on two lines, as BookMyShow lists
  venues. Line 1 is the full official name rebuilt from the catalogue's `name` + `area` (the two
  halves of BookMyShow's venue name, split at its colon), less only trailing place names and the
  city: "PVR: Atrium Gachibowli, Hyderabad" → **PVR: Atrium** / Gachibowli; "PVR ICON: Hitech,
  Madhapur, Hyderabad" → **PVR ICON: Hitech** / Madhapur. When the words after the colon are only
  a place ("PVR: Musarambagh"), they stay on line 1, so a branch is never reduced to its chain.
  Line 2 is the locality only; with no place named it is "Hyderabad". The By-theatre picker uses
  the same name ("PVR: Atrium · Gachibowli").

## Ticket prices (`explorer/prices.py`)

Prices come from the same `showtimes-by-event` response, at no extra request: each show's
`additionalData.categories[]` lists every seat category with BookMyShow's `curPrice`
(`platforms.bookmyshow.parse_seat_categories`). Each row shows, beside its Book button:

| What BookMyShow listed | Row says |
| --- | --- |
| every category priced, different prices | `₹150 – ₹450` · "3 seat types" |
| every category priced, one price | `₹295` |
| some categories priced, some without a price | `₹180 onwards` · "1 of 2 seat types priced" |
| no categories, or no usable price (missing, zero, unreadable) | `Price unavailable` |

Hovering the price lists each category in BookMyShow's own words ("EXECUTIVE ₹150 · GOLD ₹295 ·
RECLINER ₹450 (sold out)"). Above the results, a **Tickets** line gives the range across every
listed show, and says how many shows had a price when not all did.

These are *listed* ticket prices from the read the freshness line describes. The payload does not
say whether GST is included, and the booking fee is added at checkout, so the page says the final
amount is shown at seat selection. Nothing is estimated. A show from the catalogue sync's snapshot
never carries a price (the snapshot stores none, and the code refuses one), and prices are never
carried from one read to another.

## Format filter (`explorer/formats.py`)

BookMyShow names a show's screen in free text, theatre by theatre ("Atmos", "4K Dolby Atmos",
"Barco Flagship Laser Dolby Atmos"). Each string is read into the features it names, as whole
words, and the Format field opens a short popover of pills in four groups:

- **Standard**: 2D, 3D, 2K
- **Premium screen & projection**: IMAX, Dolby Cinema, 4K, 4K Laser, Laser projection, RGB Laser,
  HDR, PXL, 4DX, MX4D, Infinity Vision, EPIQ, PCX, Macro XE, ICE, LED screen, 70MM, Barco projection,
  Christie projection
- **Premium audio**: Dolby Atmos (also spelled "Atmos"), Dolby 7.1, 7.1 surround (no "Dolby"
  named), Dolby sound ("Dolby" with no system named)
- **More formats**: LUXE, Recliner, VIP screen, Large screen, Air cooled, Air conditioned,
  Playhouse, and any string that names nothing known ("Screen 3"), kept whole

Only the features present among the shows on screen are offered, for the chosen date, and in By
theatre for that theatre only. Matching is **OR**: a show is listed when it has any chosen
feature, so a show with Dolby Atmos and 4K Laser appears under either. Nothing is inferred:
Dolby Cinema does not imply Atmos, 4K does not imply laser, and a theatre's *name* ("Sudarshan
35MM 4k Laser & Dolby Atmos") never gives its shows a feature. A show with no format text appears
only under "All formats". **Select all** is the same as **All formats**, so ticking everything
never hides such a show. The chosen formats show as chips under the filters, with **Clear
formats**; the popover has Select all and Clear at its top.

## Performance notes

- Building a read's shows caches theatre classification (`classify`, a pure function of
  BookMyShow's two strings) and builds each theatre-and-date booking link once per read.
- The snapshot file is read only when a fallback needs it, once per query.
- The page turns off scroll anchoring, so a filter tap that shrinks the results does not move a
  phone's page (and the open format picker) off screen.
