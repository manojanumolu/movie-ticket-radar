# TicketRadar — UI design package (V1)

Personal movie-ticket release monitor · BookMyShow · Hyderabad.

## What's in here

| File | What it is |
|---|---|
| `TicketRadar.dc.html` | The design, as a live interactive HTML file. Open it in a browser. Movie / theatre / format / frequency selection all work; states are shown as static screens below the dashboard. |
| `DESIGN-SPEC.md` | Full spec: palette, typography, spacing, component system, every state, microcopy, responsive rules, Streamlit implementation notes. |
| `screenshots/01-setup-dashboard.png` | Home / setup dashboard (desktop) |
| `screenshots/02-tickets-live.png` | Ticket-release + notification state |
| `screenshots/03-states.png` | Stopped · expired · error · empty |
| `screenshots/04-mobile.png` | Mobile flow (3 steps) |

## For Claude Code

Build the Streamlit app from `DESIGN-SPEC.md` (section 10 has the widget-by-widget
mapping). Use the screenshots as the visual target and `TicketRadar.dc.html` for exact
colors, sizes and copy — every value in it is literal inline CSS, so it reads as a spec.

Posters are striped placeholders: real poster URLs come from the movie source at runtime.

Not included by design: backend, scraper, scheduler, email, database.
