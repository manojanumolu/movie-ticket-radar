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
