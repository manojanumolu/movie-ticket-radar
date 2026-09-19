# TicketRadar — Visual Design System (V2)

The reference for the phased UI redesign. It supersedes the visual
direction in `ticketradar-ui-design-system-2/project/DESIGN-SPEC.md` (V1)
where the two disagree; V1's component and Streamlit notes still apply
unless a later phase says otherwise.

Status: **Phase 2 — avatars done.** The Login page and the avatar
preference are built on this document; the rest of the app is still V1.

## 1. Identity

> **TicketRadar = premium cinema × futuristic radar × dark glass × restrained
> red/pink lighting.**

The visual world is **movies, theatres, cinema, radar.** Hyderabad stays in
the copy and the branding, not in a landmark. The page should feel like the
moment before the lights go down: dark, expensive, quiet, with one thing
glowing.

It must **not** read as:

- a tourism site or a Hyderabad tourism page — no Charminar, no skyline
  illustration (the V1 login's `CHARMINAR` SVG went in Phase 1);
- a pencil-sketch or line-drawn theatre illustration;
- a generic SaaS dashboard;
- a gaming site drowned in neon — the red/pink is *lighting*, not a theme;
- a movie catalogue that changes with the week — the Login posters are
  fixed brand assets (`static/login/posters/`), never BookMyShow's.

Hierarchy rule from V1 still holds: exactly one thing is loudest on screen.

## 2. Tokens

The live tokens are `ui.theme.TOKENS` and the `--tr-*` custom properties
`ui/theme.py` injects. V2 keeps every value below; a later phase that needs
a new one adds it there, in one place, and here.

| Token | Value | Use |
|---|---|---|
| `bg` | `#08080A` | page ground |
| `surface` | `#101014` | cards, panels |
| `sunken` | `#0A0A0D` | inputs, rows |
| `shell` | `#0B0B0E` | app shell |
| `border` / `border_strong` | `rgba(255,255,255,.07)` / `.14` | hairlines |
| `text` … `text_4` | `#F2F2F4` `#B0B0BA` `#8E8E98` `#6E6E7A` | type ramp |
| `accent` / `accent_soft` / `accent_deep` | `#FF3355` `#FF6B85` `#D4123F` | intent, CTA, radar |
| `success` | `#3ED598` | only "tickets available / good news" |
| `warning` | `#E8B25C` | only time / expiry |

Planned additions for the glass surfaces (Phase 1 introduces them; values
are the target, not yet in `TOKENS`):

| Token | Target | Use |
|---|---|---|
| `glass` | `rgba(16,16,20,.55)` + `backdrop-filter: blur(18px)` | the login panel, floating cards |
| `glass_border` | `rgba(255,255,255,.10)` | glass edge |
| `glow` | `0 0 60px -20px rgba(255,51,85,.55)` | the radar, the primary CTA |
| `light_pink` | `rgba(255,107,133,.10–.18)` | ambient lighting on dark ground |

Restraint: glow on the radar and the primary CTA only; ambient pink at
≤ 18 % opacity; never on text.

Phase 1 settled the panel: it is **charcoal, not glass** — an opaque
gradient (`#17131A → #0F0D12`) with a hairline, a top light line and a
pink corner light, no `backdrop-filter`. Two reasons: readability, and a
filtered or transformed panel becomes a containing block that traps the
fixed loading veil (`.tr-auth-veil`). The panel's entrance is opacity only
with `animation-fill-mode: backwards`. Glass is reserved for the veil.

## 3. Typography

**Manrope** (500–800) for everything read; **DM Mono** (400/500) sparingly
for eyebrows, badges, timers and step numbers. Both from Google Fonts with
`display=swap` (`ui.theme.FONTS`). V1's Archivo / JetBrains Mono are
superseded — the deployed app has shipped on Manrope / DM Mono and nothing
technical argues for a change.

## 4. Assets

Hand-prepared artwork lives in `static/` (the one folder Streamlit
serves) and is reached through `ui.assets_registry` by semantic key —
never by a literal path on a page.

```python
from ui import assets_registry as art

art.posters()                    # the Login wall, in display order
art.poster("interstellar")       # → Asset(key, kind, category, path, .mime, .size, .label)
art.avatars_by_category()        # {"animation": [...], "cartoon": [...], "marvel": [...], "dc": [...]}
art.avatar("batman").category    # "dc"
art.static_url(asset)            # app/static/…?v=<fingerprint> — the browser caches it
art.data_uri(asset)              # inline, for a tiny image only; see the size rule below
```

Rules:

- **Keys are stems**: `static/avatars/marvel/iron_man.jpg` is
  `avatar("iron_man")`. Lowercase snake_case, no spaces.
- **Categories are folders**: `AVATAR_CATEGORIES` fixes the picker order
  (`animation, cartoon, marvel, dc, tollywood`); an empty folder is not a
  group.
- **Type comes from bytes** (`sniff`), never the extension.
- **Serve, don't embed**: `static_url()` is how a page shows an image.
  `enableStaticServing = true` (`.streamlit/config.toml`) serves `static/`
  at `app/static/…`; Starlette answers with `ETag`/`Last-Modified`, so a
  poster is one fetch per browser, not 400 KB of base64 per rerun. The
  `?v=` fingerprint busts a stale cache when a file is swapped.
- **Fetch only what the viewport shows**: a card is a `<picture>` whose
  `<source media>` names the viewport it appears on, over a transparent
  pixel. The Login page fetches six posters on a desktop, three on a phone,
  none on a tablet. `loading="lazy"` alone did not achieve this.
- **Size rule**: `oversized()` lists anything above `EMBED_LIMIT`
  (300 KB). The avatars are all ≤ 512 px and < 150 KB since Phase 2
  (`tools/optimize_avatars.py`); two Login posters remain over the line
  and are served, never embedded. See `static/README.md`.
- **Avatar keys**: a stored `avatar_key` becomes an image only through
  `resolve_avatar()` — a registered stem or nothing. `ui/avatar.py` reads
  it off the settings document the app already loads, shows the initial
  when it is absent or invalid, and writes it once, on Save, only when
  it changed. Google's `photo_url` is separate and untouched.
- The platform logos (`ui/assets/logo-*.png`, via `components.asset_uri`)
  are UI chrome, not brand artwork, and stay where they are.

### The Login room (Phase 1 refinement)

The left column is one environment, layered back to front, all of it CSS
and inline SVG: a projector cone from the top right (`.beam`), the radar's
sweep continued across the room as a slow conic wedge on the same 6.5 s
clock as the dish (`.scan`), a curved film strip — one path stroked five
times: halo, band, sprocket holes, film, frame lines (`_strip`) — a faint
aperture in the dark on the right (`_reel`), the radar, the six posters at
three depths hung along the strip (`FAN`: the lead largest and in front,
two flanking a step back, three lower and further back; corners may
overlap, never more than 12 % of a card), the crafts in the negative
space, the headline, and two rows of rim-lit theatre seats fading into the
floor (`_seats`). No Charminar, no skyline, no photograph. Poster hover
lifts the card: the deal-in animation uses `fill-mode: backwards` so it
cannot keep holding `transform` against `:hover`.

## 5. The radar

There is deliberately **no radar image**. The radar is code: an inline SVG
(rings, axes, sweep, blips) driven by CSS `@keyframes` — rotate, pulse,
glow — with `transform` and `opacity` only, so it stays on the compositor.
It scales with its container (`viewBox`, `width: 100%`), doubles as the
loading state, and never becomes a GIF, a video or a raster.

It exists: `ui/radar.py`. `radar.svg(size=, state=, tag=, cls=)` returns
the markup; `radar.CSS` is the stylesheet fragment a page folds into its
one `<style>` block. States are one attribute, `data-state`: `idle`,
`scanning` (the Login hero), `detecting` (a call in flight), `success`.
The `mark` class is the small form (≤ 60px: fewer, heavier rings) for
brand lockups and status lines. Every instance names its own gradient ids,
so a hidden twin cannot shadow a visible one. `prefers-reduced-motion`
freezes the sweep at a composed angle. Later phases reuse this; nobody
draws a second radar.

## 6. Icons — the 24 crafts

No icon library is introduced. The project already has one icon system:
`ui.components.ICON_PATHS` — 24-grid line icons, round caps, 1.7–1.8 px
stroke, rendered inline by `icon(name, size, color)` and, for CSS
backgrounds, `ui.theme._svg_uri`. It is stylistically consistent with the
sidebar and costs nothing to load.

The 24 filmmaking crafts live in `ui/crafts.py` as hand-written paths in
that grid, each with a label and one line of copy (`Craft.tip`:
"Cinematography — The visual language of a film."). `constellation()`
places them at fixed fractions of the Login visual (`LAYOUT`), in the
negative space; the tooltip is CSS (`data-tip` → `::after`), opens
inward near an edge, and works on hover and keyboard focus. Phones get
`strip()` — eight icons, no tooltips, decoration only. Inline SVG, no
icon font, no JS.

### The avatar picker (Phase 2)

A dialog from the account menu ("Change avatar"): the current face large,
then one row per category that has files, in the theme's `pick_` tile
mechanics — a circular 74 px ring, a name under it, a red ring + check +
lift when selected, seven a row on a desktop and three on a phone. Its
CSS rides inside the dialog, so the page never carries it; the thumbnails
are the served static files. Nothing is written on select; Save writes
the one field, and only if it differs.

### Monitor details (Phase 3 — Home)

The rail card stays a card: film, phase, last/next check, the theatre
rows. Everything else a monitor knows sits behind one quiet action,
"View details" — a dashed, mono-labelled button under the card, deliberately
smaller than Stop — which opens a dialog (`ui/detail.py`): the film row
watched, the whole schedule, every theatre × format with its current
answer and, for a live target, the showtime chips and a `tr-book`
BOOK ON BOOKMYSHOW. The link is the stored `TargetState.booking_url` — the
value the alert email carried — shown only when it passes the mail's own
`is_bookmyshow_url` test; anything else is an honest "no booking link yet".
The dialog draws the `Monitor` and `MonitorState` the page already loaded
(no read of its own), two columns on a desktop and one on a phone, and its
CSS rides inside it. The box is the Login panel's charcoal with its top
light line and one pink corner light; each panel eyebrow carries a 3 px
accent bar; every target's answer is a mono pill whose colour is
semantic — green live, amber sold out, red only for a failure, neutral for
anything still watched. Red stays lighting, never a state.

## 7. Performance rules (every phase)

Learned the hard way and not negotiable:

- no unnecessary reruns; keep `st.rerun()` to state changes that need it;
- no duplicate rendering; one `st.markdown` per card;
- one CSS payload per run (`theme.inject`) — page-specific CSS rides
  inside that page's own markdown block, not a second stylesheet;
- no Firestore reads for decoration; the registry is filesystem only;
- never touch `st.session_state` from worker code;
- no deprecated Streamlit APIs;
- no image heavier than `EMBED_LIMIT` inline; see §4;
- animation is CSS `transform`/`opacity` on SVG, never JS timers;
- no JavaScript framework, no icon font, no new dependency without a
  reason written here.

## 8. Phases

| Phase | Scope | Status |
|---|---|---|
| 0 | asset foundation, registry, this document | done |
| 1 | Login / Sign-up — radar, poster hand, crafts, charcoal panel, honest wait | done |
| 2 | Avatar system — `ui/avatar.py`, one field on `users/{uid}` | done |
| 3 | Home | in progress — monitor details dialog |
| 4 | My Monitors | |
| 5 | Create Monitor wizard | |
| 6 | History | |
| 7 | Settings | |
| 8 | micro-interactions, loading states, tooltips | |
| 9 | performance, regression, production audit | |

Frozen throughout: `auth/` architecture, `firestore.rules`, the Firestore
model, `platforms/`, `monitor/`, `run_monitor.py`, `notifications/`,
`.github/workflows/`, the monitor limit, all monitor data.
