# TicketRadar — UI/UX Design Specification (V1)

Personal movie-ticket release monitor. Platform: BookMyShow only. City: Hyderabad.
Target implementation: **Streamlit + custom CSS**.

Reference design file: `TicketRadar.dc.html` (open in any browser).
Screenshots: `screenshots/`.

---

## 1. Visual direction

Dark cinematic, editorial, quiet-until-it-matters. The screen should read as an
instrument panel, not a movie website: deep near-black ground, one crimson accent for
intent/actions, one mint accent reserved **exclusively** for "available / success", and
amber reserved for "expired / scheduled". Gradients appear only in the hero band and on
primary buttons. No neon, no glassmorphism, no emoji.

Hierarchy rule: at any moment exactly one thing is the loudest element on screen —
`START MONITORING` before a monitor exists, the status card while it runs,
`TICKETS ARE LIVE` when it fires.

## 2. Color palette

| Token | Value | Use |
|---|---|---|
| `bg` | `#08080A` | page ground |
| `surface` | `#101014` | cards, panels |
| `surface-sunken` | `#0A0A0D` | inputs, rows, nested cards |
| `border` | `rgba(255,255,255,.07)` | default hairline |
| `border-strong` | `rgba(255,255,255,.14)` | secondary buttons |
| `text` | `#F2F2F4` | primary text |
| `text-2` | `#B0B0BA` | secondary text |
| `text-3` | `#8E8E98` | captions, help text |
| `text-4` | `#6E6E7A` | mono eyebrows, disabled |
| `accent` | `#FF3355` | selection, primary CTA, stop |
| `accent-deep` | `#D4123F` | CTA gradient end |
| `success` | `#3ED598` | tickets available, monitoring-alive dot, email sent |
| `warning` | `#E8B25C` | expired, format badges, scheduling |
| `error` | `#FF3355` on `#130C0E` | could-not-check |

Tints: `rgba(accent,.07–.14)` fills, `rgba(accent,.35–.45)` borders.
Never use mint for anything except "good news", or amber for anything except
time/expiry — that consistency is what makes status scannable.

## 3. Typography

- Display / UI: **Archivo** (400/500/600/700/800). Headlines `-0.03em` tracking.
- Data / eyebrows / timers: **JetBrains Mono** (400/500), uppercase, `.14–.2em` tracking, 9.5–11px.

Scale: 44 / 38 (hero, page titles) · 27 (mobile hero) · 20–22 (state titles) ·
16–17 (card titles) · 14–15 (body, values) · 12.5–13.5 (secondary) · 11–12 (captions) ·
9.5–11 mono (labels). Body line-height 1.4; headlines 1.02–1.12.

## 4. Spacing, radius, elevation

- Spacing scale: 4 / 6 / 8 / 12 / 16 / 22 / 26 / 36 px.
- Radius: 20 (app shell), 16–18 (cards), 11–13 (controls, buttons, rows), 999 (pills), 6–10 (checkbox, chips).
- Borders do the work; shadows only on primary CTAs and the selected movie
  (`0 20px 50px -18px rgba(255,51,85,.85)`).
- Layout: sidebar 236px · fluid main · right rail 344px. Content max 1600px.

## 5. Component system

**Card** — `surface`, 1px border, radius 16, padding 22.
**Step card** — card + 27px mono-numbered badge (`1`–`5`) + title 16/700 + help line 13/`text-3`.
**Input** — `surface-sunken`, 1px border, radius 11, padding 13×16, 14px text, mono icon at left.
**Primary button** — gradient `accent → accent-deep`, radius 14, 19px padding, 17px/700, `.04em`, uppercase.
**Success button** — gradient mint, ink-dark label (`#04120C`).
**Destructive/stop** — accent tint fill + `rgba(accent,.42)` border + accent text (not solid; stop should be obvious but not scream).
**Secondary button** — transparent, `border-strong`, `text-2`.
**Checkbox row** — 20px box, radius 6; unselected `1.5px rgba(255,255,255,.2)`; selected solid accent + white `✓`; the whole row is the hit target (min height 44px).
**Radio row** — 14px circle, selected = `4px solid accent` ring on sunken ground; row gets accent tint + border.
**Poster tile** — 13px radius, 172px image area on flat `#14141A` (selected `#1B1216`), real poster art fills it edge to edge; selected = 1.5px accent border + glow + accent check badge top-right. In the reference file these are drop slots — drag a poster file onto one to fill it.
**Small thumbnail** (rail 62×88, history 34×46) — flat `#16161C`, 1px border, centred 13px film-strip glyph. Never a text caption at this size.
**Logo mark** — 36px rounded-10 crimson gradient tile, white 1.8px-stroke ticket glyph (notched stub + dashed perforation). Not a letter.
**Nav item** — 18px line icon (1.5–1.8px stroke) + 14.5px label, 12×14 padding, radius 11. Icons: home, monitor-with-lens, clock-with-rewind-arrow, gear. Active = crimson horizontal gradient wash + `rgba(accent,.32)` border + accent-tinted icon + white label. Inactive icons `#8E8E98`.
**Platform lockup** — the platform's own logo as an image, not redrawn type: BookMyShow at 35px height (`assets/logo-bookmyshow.png`, alpha-keyed, neutral glyphs recoloured white for the dark ground), District as a 34px rounded-9 app-icon tile (`assets/logo-district.png`), PVR at 32px height + "Cinemas" label (`assets/logo-pvr.png`, `opacity:.85`). City line below: 15px accent map-pin + 13.5px/600 text. Status pill top-right: mint check-circle glyph + label, `white-space:nowrap`.

Logo files in `assets/` are user-supplied brand marks, trimmed to their alpha bounds. Swap in official SVGs when available — reference them at the same heights.
**Status pill** — 999 radius, tinted bg, 1px tinted border, 10.5px/700, `.1em`, optional 6–7px dot; dot animates (`pulse 1.8s`) only when live.
**Spinner** — 12–15px ring, 2px, `border-top-color` accent/mint, `spin 1s linear`.
**Eyebrow** — mono 10px uppercase `.18em` `text-4`.
**Disabled / coming soon** — 1px dashed border, `opacity .55`, mono `COMING SOON`.

## 6. Screen hierarchy

```
Home (Setup)                     → screenshots/01-setup-dashboard.png
 ├ Hero: title + promise line
 ├ Platform selector: BookMyShow (enabled) · District / PVR Cinemas (Coming soon)
 │   each tile = 34px icon tile (ticket / map-pin / film-strip, 1.7–1.8px stroke SVG) + wordmark
 ├ 1 Select movie      (search + poster grid, single select)
 ├ 2 Select theatres   (multi select + Select all)
 ├ 3 Select formats    (contextual per selected theatre, "Any format" default)
 ├ 4 Check frequency   (10 / 15 / 30 min, default 10)
 ├ 5 Monitor until     (date + time + "start immediately" toggle)
 └ START MONITORING
Right rail (persistent)
 ├ Active monitor card (status, last/next check, until, stop)
 ├ Per-theatre status list
 └ Recent history (3 items + View all)
Ticket release + notification      → screenshots/02-tickets-live.png
States: stopped · expired · error · empty  → screenshots/03-states.png
Mobile flow                        → screenshots/04-mobile.png
```

## 7. State definitions (never collapse these)

| State | Signal | Copy | Action |
|---|---|---|---|
| Monitoring | mint pulsing dot + spinner | "Checking for tickets… / Not released yet · checked 14 times" | STOP MONITORING |
| Available | mint card, `TICKETS ARE LIVE` | theatre · format · date · showtimes · "Detected at 10:23 PM" | BOOK ON BOOKMYSHOW / Keep monitoring |
| Notified | mint check row | "Email sent to you@… · 10:23 PM" | — |
| Stopped (manual) | neutral grey, square icon | "Monitoring stopped — you stopped this alert at 10:41 PM." | START NEW MONITOR |
| Expired | amber, clock icon | "Monitoring expired — this alert stopped on its own at the end time you set. Nothing went wrong." | EXTEND BY 24 HOURS / Start new monitor |
| Error | crimson, `!` icon | "Couldn't check BookMyShow — this is a connection problem on our side, not a 'no tickets' answer. Last successful check 10:20 PM." | RETRY NOW (monitor keeps running) |
| Empty | dashed, `◎` | "No active monitors — set an alert and we'll watch BookMyShow for you." | CREATE NEW ALERT |

Rules:
1. A theatre going live **never** stops the other theatres in the same monitor.
2. A failed check is never rendered as "no tickets".
3. Expiry is visually distinct from error (amber vs crimson, calm vs corrective copy).

## 8. Microcopy

- Hero: "Movie Ticket Monitor" / "Know the moment your tickets go live." / "We watch the booking page for you, so you don't have to."
- Step 2: "Where do you want to watch?" · "One or more theatres in Hyderabad." · "Select all"
- Step 3: "Formats, per theatre" · "Only formats that theatre actually runs." · options: "Any format" first
- Step 4: "How often should I check?" · "Checks run automatically in the background."
- Step 5: "Monitor until" · "The monitor stops itself after this time." · "Start checking immediately"
- CTA: "START MONITORING" · helper: "You'll get an email the second tickets appear — and the monitor keeps running for the other theatres."
- Rail labels: "Last checked" · "Next check in" · "Monitoring until" · "Watching" · "Available"
- Never surface: cron, GitHub Actions, workers, scraper, job, workflow.

## 9. Responsive behaviour

| Breakpoint | Layout |
|---|---|
| ≥1440 | sidebar + main + right rail (as shot 01) |
| 1100–1439 | rail collapses under main as a full-width band; sidebar stays |
| 768–1099 | sidebar becomes top tab row; steps 2+3 and 4+5 stack |
| <768 | single column, one step at a time, "STEP n OF 5" eyebrow, sticky bottom CTA, poster grid 2-up, 44px min hit targets, no horizontal scroll |

## 10. Streamlit implementation notes

- **Shell**: `st.set_page_config(layout="wide")`; inject the palette/typography with one
  `st.markdown("<style>…</style>", unsafe_allow_html=True)` block. Sidebar = `st.sidebar`
  with `st.radio` styled as nav. Right rail = third column (`st.columns([1.1, 3.2, 1])`),
  which naturally stacks on narrow screens.
- **Movie grid**: `st.columns(6)`; each cell an `st.container(border=True)` with an
  `st.image` (poster) + `st.button("Select", use_container_width=True)`; selection in
  `st.session_state.movie_id`. Selected card gets a CSS class via a hidden marker div.
- **Theatres**: `st.checkbox` per row inside bordered containers; "Select all" is a button
  that writes all ids into session state.
- **Formats**: loop selected theatres → `st.radio(f"format_{theatre_id}", ["Any format", …],
  horizontal=False)`. This is the natural Streamlit shape for "contextual per theatre".
- **Frequency**: `st.segmented_control` (or `st.radio(horizontal=True)`) with values 10/15/30.
- **Monitor until**: `st.date_input` + `st.time_input` side by side; `st.toggle` for start-now.
- **Start**: `st.button(type="primary", use_container_width=True)` → write monitor row, `st.rerun()`.
- **Live status**: `st.fragment(run_every="30s")` re-renders only the status card; countdown
  from `next_check_at` timestamps (no JS timer needed). Avoid `st.autorefresh` on the whole page.
- **Status cards**: single `st.markdown` HTML block per card — cheaper and more faithful than
  composing with widgets, and the mint/amber/crimson variants are one class swap.
- **Animation budget**: only CSS `@keyframes` (pulse dot, spinner, hero sheen). No JS.
- **Icons**: text glyphs / Material symbols via `:material/…:` in Streamlit labels; no icon fonts required.
- **Accessibility**: all status colors are paired with a text label and an icon glyph, so
  status is never color-only; body text ≥12.5px; contrast ≥4.5:1 on all fills used here.
