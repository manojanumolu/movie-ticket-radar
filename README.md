# Movie Ticket Radar

**Know the moment your tickets go live.**

A personal monitor that watches a BookMyShow listing in the background and
emails you the instant a theatre and format you care about becomes bookable.
Built for Hyderabad releases, where tickets open at unpredictable hours and
the alternative is refreshing a booking page all evening.

> Personal project, one user, no accounts. Not a SaaS, and not trying to be.

---

## What it does

    1 Location  →  2 Movie  →  3 Theatres  →  4 Formats  →  5 Monitoring  →  START

1. Pick your city. (Hyderabad today; the rest are wired up but off.)
2. Pick from the movies **actually showing there** — real titles, real posters,
   pulled from BookMyShow by a background job.
3. Pick one or more theatres that are screening it.
4. Pick formats **per theatre** — only the ones that theatre actually runs.
   AMB's HDR by Barco and Allu's Dolby Cinema are separate choices, and each
   theatre × format is watched independently.
5. Choose a check interval (10 / 15 / 30 min), an end time, and — separately —
   which **show dates** count: any date, a single date, or a range such as
   25–28 Sep. Shows outside those dates are ignored.
6. Press **Start monitoring**. The app saves the monitor and immediately
   dispatches the worker, so the first check happens within about a minute —
   then it keeps checking at the interval you chose. When a target goes from
   *not bookable* (or *sold out*) to *available*, you get one email with each
   showtime as a link and a booking button.
7. The monitor stops itself at the end time you set. You can also stop it
   manually, and either way the *persisted* state changes — not just the view.

**You never paste a URL.** No BookMyShow link, no `ET…` code, nothing about how
the data got there. That is a hard rule, enforced by a test that fails if the
string `bookmyshow.com` appears anywhere in the user-facing flow.

### What it deliberately does not do

- It does not email you on every check. Four consecutive `AVAILABLE` reads
  produce exactly one email.
- It does not report a failed check as "no tickets". A connection problem is
  rendered as *"Couldn't check BookMyShow"*, with the last successful check
  time, and the last real observation is left untouched.
- It does not stop watching your other theatres when one goes live.
- It does not invent booking URLs, movie titles, theatres or formats. Every
  one of those is read from BookMyShow, or absent.
- It does not show an empty movie grid when the problem was a bot check. Loaded,
  genuinely-empty, refused, broken and never-synced are five different messages.

---

## Status and honest limitations

| | |
|---|---|
| Platforms | **BookMyShow only.** District / PVR / Cinépolis are shown as *Coming soon* and are not implemented. |
| City | Hyderabad (other Indian cities are wired up in `platforms/bookmyshow.py` but untested). |
| BookMyShow API | **Undocumented internal endpoints.** They can change shape or start refusing us with no notice. Every parse step is defensive and every failure is surfaced as an error, never as an availability. |
| Bot checks | BookMyShow is behind Cloudflare, which fingerprints the **TLS handshake**. Plain `requests` is refused every single time, from every host tested. See [The bot check](#the-bot-check) — the single most important thing to understand about this project. |
| Scheduling | GitHub Actions cron is **not** a clock: measured on this repo, a `*/5` schedule fired one to five *hours* apart. So the cron is only a safety net. Starting a monitor dispatches the worker at once, and the worker runs as a ~50-minute *segment* that ticks every 30s, checks each monitor when its own interval is due, and hands over to the next segment. See [How checks get their cadence](#how-checks-get-their-cadence). |
| Showtime links | Each showtime in the email is a link. BookMyShow's payload publishes **no** show-level URL (its showtime `cta` is `showTimeRedirect` with analytics only), so a showtime links to the date's booking page — a real, derived BookMyShow URL — never a guessed pattern. If the payload ever carries a show-level link, it is used. |
| Email | Gmail SMTP with an app password. One recipient. |

**Verified against the live site.** The catalogue sync has run on a GitHub
runner and pulled the real Hyderabad listing — 59 bookable events across 39
films, with real posters and real per-screen formats. Every parser in
`platforms/bookmyshow.py` was written against payloads captured from that run,
not against guesses. Two guesses that *were* made got caught by doing this: the
poster CDN path 404'd, and venue cards turned out to carry no area field at all.

---

## The bot check

BookMyShow sits behind Cloudflare bot management. What it refuses is not the
URL or the headers — it is the **TLS fingerprint of the HTTP client**.
Measured from GitHub Actions runners:

| Client | Result |
|---|---|
| `requests`, any headers, any URL | 403, every time |
| `requests` + homepage warm-up for cookies | 403 |
| `curl_cffi`, `impersonate="chrome"` | 403 on one run, **200** on the next |
| `curl_cffi`, `impersonate="safari"` | **200** on one run, 403 on the next |

Two conclusions, and they shape the whole design:

1. **Plain `requests` never works.** No amount of header tuning changes it.
   This is why the original paste-a-URL flow was unreliable — the URL was
   never the problem, the client was.
2. **A single browser profile is not enough.** The block is probabilistic, not
   a permanent verdict on a fingerprint. So `platforms/http.py` rotates: on a
   refusal it discards the session, picks the next profile, and retries.

`curl_cffi` is therefore a real dependency, not a nicety. It is also optional
at import time — without it the app still runs, and refusals surface as the
honest `BLOCKED` state rather than an `ImportError` at startup.

Reproduce any of this with the **BookMyShow diagnose** workflow, which prints
the table above for the current runner and changes nothing.

---

## How it works

```
  Streamlit UI                  data/ (the database)          GitHub Actions
  ------------                  --------------------          --------------
  1 pick city  -----reads------> catalogue.json  <---writes--  catalogue-sync.yml
  2 pick movie                    movies, theatres,             (4x/day + manual)
  3 pick theatres                 formats per theatre
  4 pick formats
  5 interval+end ---writes-----> monitors.json   ----reads--->  bookmyshow-monitor.yml
     START ---------dispatches-------------------------------->  (now, then every
                                                                 10/15/30 min inside
                                                                 a 50-min segment)
                                                                     |
  active monitoring <--reads---- state.json      <---writes---  check, compare,
                                                                notify, commit
                                                                     |
                                                                     v
                                                            Gmail SMTP --> inbox
```

A film is one row per language in the picker, but BookMyShow lists each
premium format (3D, 4DX 3D, IMAX, EPIQ, MS-Infinity Vision…) as a *separate
event* with its own theatres. The sync records those sibling events on the row
(`MovieRef.variants`) and the provider sweeps them on every read — for the
catalogue and for the worker's checks alike — so the theatre list is the whole
film, not one format of it. (Avengers Endgame: Encore was 3 theatres before
this and 9 after.) Detail is re-read every six hours, because theatres are
added as a release approaches and drop off as the day's shows run out.

A theatre often *releases* a film as a brand-new sibling event — a "Barco
Laser" or "Dolby Cinema" event that did not exist when the monitor was saved.
A running monitor cannot wait for the next catalogue sync to hear about it, so
the worker re-lists the city itself (`monitor/discovery.py`): one QUICKBOOK
request per city, shared by every monitor there, at most once every 15 minutes
and only while a due monitor still has a theatre or format it hasn't found.
New siblings are added to the monitor and swept in that same check; a failed or
blocked listing changes nothing and is retried after five minutes. A manual
**Refresh catalogue** still works and counts as a listing — it is just no
longer the only way a running monitor learns of a new event.

Note that a film is one row *per language*, and each row's family is that
language's events only. A monitor on the Telugu row will not see a theatre
BookMyShow lists under the English row; when that happens the worker log says
so (`[hint] … is listed for '… · English'`).

The page is one column on a phone (≤768px, Streamlit's own sidebar
breakpoint): every `st.columns` row stacks, except grids the flow wraps in a
`trgrid_*` container (posters two-up, tile pickers three-up), `trpair_*` inputs
and the compact step rail. `python tools/ui_audit.py` walks every page at
375–1600px in a real browser and fails on any horizontal overflow or
one-letter-per-line wrap.

The UI **never calls BookMyShow to build its movie grid.** It reads what the
sync job committed. That is the whole point: a Streamlit Cloud container is
exactly the kind of host the bot check refuses, and the user should not have to
care. The app stays instant and always available, and the one place that has to
fight Cloudflare is a background job that can retry on a schedule.

The repository **is** the database. `data/*.json` is the single source of
truth; the worker commits what it observed back to the repo, and the UI
mirrors its writes through the GitHub API so a Streamlit Cloud instance with
an ephemeral filesystem doesn't lose them.

Two files, two owners, so they never race:

- `data/monitors.json` — what you asked for. Written by the UI.
- `data/state.json` — what we observed. Written by the worker.

### How checks get their cadence

The one thing GitHub's scheduler does not give you is timing. On this
repository the `*/5 * * * *` cron fired at 03:57, 05:51, 10:42 and 16:14 IST —
a monitor created at 02:55 was first checked at 03:57. Three mechanisms fix
that, in order of importance:

1. **Start triggers the worker.** Saving a monitor commits
   `data/monitors.json` through the GitHub API, and the workflow listens for
   pushes to that file — so the commit the app already makes *is* the trigger,
   using nothing beyond the Contents permission. The app also tries a
   `workflow_dispatch` (`force=true` for that monitor) when its token has
   *Actions: write*; either one is enough. Only if **neither** happened does
   the monitor get a persisted problem, shown as a red **PROBLEM OCCURRED**
   button whose panel names the actual cause (403, no token, …) with a
   **Retry now** — never a countdown that pretends a check is coming.
2. **Segments.** `run_monitor.py --loop` (`monitor/worker.py`) keeps one run
   alive for up to 50 minutes. Every 30s it pulls `main` (so monitors created
   or stopped in the UI are noticed), runs `run_once` (which checks only the
   monitors whose interval is due — so 10/15/30 minutes mean what they say),
   and commits any new observation immediately. When time is up and monitors
   are still running it dispatches the next segment; when nothing is running
   it exits at once and dispatches nothing.
3. **The cron stays** as the net under both. A concurrency group makes sure
   two segments never overlap, so no monitor is ever checked twice at once.

The UI reads through to the repository (rate-limited to once per 20s) so
"Last checked" and "Next check" reflect what the worker committed, even on
Streamlit Cloud where the container's files are otherwise frozen at deploy.

### Layout

```
app.py                      Streamlit entrypoint (page + rail)
run_monitor.py              worker CLI — one pass, or --loop for a segment
sync_catalogue.py           catalogue CLI — what the catalogue schedule runs
resolve_movie.py            admin-only: resolve one listing URL. Not user-facing.

platforms/
  base.py                   Provider protocol + PlatformError / PlatformBlocked
  http.py                   the TLS-impersonating client, and why it exists
  bookmyshow.py             the only implemented provider
monitor/
  models.py                 normalised domain types (no platform knowledge)
  catalogue.py              city listing cache + sync, with SyncStatus
  discovery.py              a running monitor learns of new sibling events itself
  state.py                  persistence + monitor lifecycle
  changes.py                when something is actually worth an email
  checker.py                the engine (one pass)
  worker.py                 the segment loop: tick, pull, commit, hand over
notifications/
  email.py                  Gmail SMTP + the alert template
config/
  locations.py              supported cities
  store.py                  atomic JSON IO + GitHub mirroring
  timezone.py               everything is IST
ui/
  theme.py                  the design system, as CSS (Manrope + DM Mono)
  flow.py                   the five-step wizard: rail, search, featured picks
  components.py             cards, rows, pills
  catalogue_view.py         the catalogue parsed once per file, for the UI
tools/
  bms_diagnose.py           reachability diagnostics (run it on a runner)
  ui_audit.py               drives the app in Chromium at phone + desktop widths, fails on overflow
tests/                      228 tests, no network, no SMTP, no browser
ticketradar-ui-design-system-2/   the design (visual source of truth)
.github/workflows/
  bookmyshow-monitor.yml    ticket checks: dispatched on start, then segments; cron as fallback
  catalogue-sync.yml        city catalogue, 4x/day + manual
  bms-diagnose.yml          diagnostics, manual, changes nothing
```

### Availability states

Availability is not a boolean. The engine distinguishes:

| State | Meaning |
|---|---|
| `UNKNOWN` | never checked |
| `NOT_FOUND` | the movie/event isn't listed |
| `THEATRE_NOT_AVAILABLE` | movie listed, this theatre isn't |
| `SHOW_NOT_AVAILABLE` | theatre listed, no show in your format |
| `NOT_BOOKABLE` | shows listed, booking hasn't opened |
| `AVAILABLE` | at least one seat category is buyable |
| `SOLD_OUT` | shows exist, every category is full |
| `ERROR` | **we could not look** — never an answer |
| `EXPIRED` / `STOPPED` | monitor lifecycle |

`NOT_BOOKABLE → AVAILABLE` and `SOLD_OUT → AVAILABLE` are the transitions that
email you. `ERROR` can neither trigger nor suppress a notification, and never
overwrites the last real observation.

### Duplicate protection

Each target stores two things: the last state we *observed*, and the last
state we *successfully emailed about*. A notification fires only when the
target is `AVAILABLE` and the second one isn't. So:

- `AVAILABLE ×4` → one email.
- `AVAILABLE → SOLD_OUT → AVAILABLE` → two emails (the alert re-arms).
- Email send fails → nothing is marked notified, and the next check retries it.

New showtimes appearing on an already-live target get their own notice, capped
at one per target per 45 minutes so a staggered release doesn't flood you.

---

## Setup

### 1. Install

```bash
git clone https://github.com/manojanumolu/movie-ticket-radar
cd movie-ticket-radar
pip install -r requirements.txt
streamlit run app.py
```

### 2. Gmail app password

Alerts go out over Gmail SMTP. You need an **app password**, not your account
password:

1. Enable 2-Step Verification on the Google account.
2. <https://myaccount.google.com/apppasswords> → create one for "Mail".
3. Copy the 16-character value.

### 3. GitHub Actions secrets

In the repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Value |
|---|---|
| `GMAIL_ADDRESS` | the Gmail address that sends |
| `GMAIL_APP_PASSWORD` | the 16-character app password |

That is all the worker needs — it uses the built-in `GITHUB_TOKEN` to commit
state.

### 4. Streamlit secrets (optional)

Only needed if you want the app itself to write to GitHub (recommended on
Streamlit Cloud, where the local filesystem is wiped on restart) or to use the
**Send test email** button.

`.streamlit/secrets.toml` locally, or the *Secrets* box in Streamlit Cloud:

```toml
GH_TOKEN = "github_pat_..."          # fine-grained PAT: Contents read & write.
                                     # Add Actions read & write for the
                                     # Retry now / Run a ticket check now buttons.
GMAIL_ADDRESS = "you@gmail.com"      # optional, test button only
GMAIL_APP_PASSWORD = "abcd efgh ijkl mnop"
```

This file is gitignored. **Never commit it.**

### 5. Deploy to Streamlit Cloud

1. <https://share.streamlit.io> → New app → this repo.
2. Main file: `app.py`.
3. Paste the secrets above.

No local-only paths are used and `data/` is created on demand, so the app
starts cleanly on a fresh container.

---

## First run

The catalogue ships populated, so there is nothing to set up before you can
browse:

1. Open the app. **Hyderabad** → the real movie grid appears immediately.
2. Pick a movie → tick theatres → tick formats per theatre → set interval and
   end time → **START MONITORING**.
3. **Settings** → set your notification email (the monitoring step pre-fills
   from it) and press **Send test email** once to confirm delivery.

Optional, any time:

- **Settings → Refresh catalogue now** — pulls the current listing. It also
  runs on a schedule four times a day.
- **Settings → Run a ticket check now** — forces the worker instead of waiting
  for the next scheduled run.

---

## Running the jobs yourself

```bash
python run_monitor.py                # check everything that is due
python run_monitor.py --force        # ignore the interval
python run_monitor.py --dry-run      # evaluate, report, send nothing
python run_monitor.py --monitor <id> # one monitor only

python sync_catalogue.py             # sync every enabled city
python sync_catalogue.py --city hyderabad
python sync_catalogue.py --no-detail # movie list only, skip theatre reads
python sync_catalogue.py --probe     # report what each listing strategy does

python tools/bms_diagnose.py         # which clients/endpoints answer from here
```

Locally these will probably be refused — see [The bot check](#the-bot-check).
That is expected, and it is why they are scheduled on Actions.

The worker exits `0` whenever the *run* completed, even if a check failed —
BookMyShow being briefly unreachable is expected, and a red X every time it
happens trains you to ignore the one that matters. Exit `1` means the run
itself broke.

## Tests

```bash
python -m pytest
```

122 tests, no network and no SMTP — a fixture fails the run if anything tries
to open a real SMTP connection. BookMyShow is replaced by a fake session
replaying payloads **rebuilt from responses captured on a live runner**, so the
suite is deterministic without being fictional.

Covered: the city listing and its strategy chain, availability mapping, every
failure mode (403 / 429 / 5xx / timeout / malformed JSON / restructured
payload), the five catalogue sync states, per-theatre format isolation, the
full duplicate-suppression matrix, failed-email retry, expiry, manual stop, the
whole five-step wizard driven through `AppTest`, and a check that the worker
still imports and runs with Streamlit blocked entirely.

Two of these tests exist because they caught real bugs during this work: one
where resolving theatre detail blanked every poster in the grid, and one where
a genuinely empty city was reported as a connection error.

---

## Troubleshooting

**"BookMyShow refused the request (HTTP 403)."**
A Cloudflare bot check, not a ticket answer. Some networks are blocked
outright. Use the **Resolve on GitHub** button (or run `resolve-movie.yml`
from the Actions tab) — runner IPs usually get through. If the *monitor*
workflow hits this repeatedly, BookMyShow is blocking Actions runners too, and
there is no fix inside this app.

**No emails.**
Check the workflow logs. `email not sent` warnings mean SMTP failed and the
change is still queued for retry. Verify both secrets, and that
`GMAIL_APP_PASSWORD` is an app password.

**Checks aren't running.**
Open the monitor's rail card. *Waiting for first check · any moment now* means
the commit/dispatch went out and a segment is starting (about a minute).
A red **PROBLEM OCCURRED** means nothing could be started — press it for the
cause (no `GH_TOKEN`, a token that cannot write Contents, GitHub down) and
**Retry now** once fixed. *Retry now* and **Settings → Run a ticket check now**
need the token to have *Actions: write*.
GitHub also disables scheduled workflows on repos with no activity for 60
days — push anything to re-enable.

**The theatre list is stale.**
**Settings → Refresh catalogue now**. Theatres and formats are whatever the
listing said when it was last resolved; the sync re-reads any movie older than
six hours, and re-reads immediately when BookMyShow adds a new format event.

---

## Security

- No credential is ever written to a file in this repo or printed to a log.
  Recipient addresses are masked in worker output (`m***@gmail.com`), and the
  SMTP auth-failure path deliberately discards the server's message body,
  which can echo the password.
- `.gitignore` covers `.env`, `.streamlit/secrets.toml`, keys and tokens.
- The worker needs only `contents: write` and the built-in `GITHUB_TOKEN`.
- `data/` holds monitor configuration and observed state — movie titles,
  theatre names, timestamps and your notification address. If that repo is
  public, so is your email address. Consider keeping it private.

## Roadmap

- Verify the live integration end-to-end from a runner.
- Per-date watching (currently every date the listing offers).
- Seat-category thresholds ("tell me when recliners open", not just any seat").
- A second provider, once the abstraction has been proven by a second
  provider — `platforms/base.py` exists for it, but nothing is implemented.

## Credits

- Mechanism for reading BookMyShow showtimes was learned from
  [aviiciii/bms-ticket-notifier](https://github.com/aviiciii/bms-ticket-notifier)
  (endpoint shape and the `availStatus` semantics), then reimplemented here
  with a different state model, error handling and notification design.
- Architecture — Streamlit + scheduled Actions + JSON-in-repo + Gmail SMTP —
  follows [manojanumolu/job-tracker](https://github.com/manojanumolu/job-tracker).
- UI from `ticketradar-ui-design-system-2/` in this repo (Claude Design handoff).
