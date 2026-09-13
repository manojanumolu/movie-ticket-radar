# Movie Ticket Radar

**Know the moment your tickets go live.**

A personal monitor that watches a BookMyShow listing in the background and
emails you the instant a theatre and format you care about becomes bookable.
Built for Hyderabad releases, where tickets open at unpredictable hours and
the alternative is refreshing a booking page all evening.

> Personal project, one user, no accounts. Not a SaaS, and not trying to be.

---

## What it does

1. You paste a BookMyShow **Book tickets** link.
2. It reads the listing and discovers the theatres and the formats each one is
   actually screening.
3. You pick one or more theatres, a format per theatre, a check interval
   (10 / 15 / 30 min) and an end time.
4. A scheduled GitHub Action checks BookMyShow in the background.
5. When a theatre you're watching goes from *not bookable* (or *sold out*) to
   *available*, you get one email with the showtimes and a booking link.
6. The monitor stops itself at the end time you set. You can also stop it
   manually, and either way the *persisted* state changes — not just the view.

### What it deliberately does not do

- It does not email you on every check. Four consecutive `AVAILABLE` reads
  produce exactly one email.
- It does not report a failed check as "no tickets". A connection problem is
  rendered as *"Couldn't check BookMyShow"*, with the last successful check
  time, and the last real observation is left untouched.
- It does not stop watching your other theatres when one goes live.
- It does not invent booking URLs, movie titles, theatres or formats. Every
  one of those is read from the listing, or absent.

---

## Status and honest limitations

| | |
|---|---|
| Platforms | **BookMyShow only.** District / PVR / Cinépolis are shown as *Coming soon* and are not implemented. |
| City | Hyderabad (other Indian cities are wired up in `platforms/bookmyshow.py` but untested). |
| BookMyShow API | An **undocumented internal endpoint**. It can change shape or start refusing us with no notice. Every parse step is defensive and every failure is surfaced as an error, never as an availability. |
| Bot checks | BookMyShow sits behind Cloudflare and **403s some networks** — this was reproduced from the development machine with both `requests` and `curl`. GitHub Actions runners generally get through; Streamlit Cloud may not. See [Troubleshooting](#troubleshooting). |
| Scheduling | GitHub Actions cron is **best-effort**. Runs are commonly a few minutes late and can be much later under load. The UI shows a configured interval and an expected next check, and never pretends a check happened that didn't. |
| Email | Gmail SMTP with an app password. One recipient. |

The live BookMyShow integration has **not** been verified end-to-end against
the real site from the development machine, because that machine is
Cloudflare-blocked. The parsing is built to the structure the endpoint is
known to return and is covered by tests against a recorded-shape fixture. The
first real proof will be a `Resolve movie` workflow run — see
[First run](#first-run).

---

## How it works

```
  Streamlit UI                    data/ (the database)              GitHub Actions
  ────────────                    ────────────────────              ──────────────
  pick movie ─────► resolve ────► catalogue.json  ◄──── resolve-movie.yml
  pick theatres                                            (workflow_dispatch)
  pick formats
  pick interval ──► save ───────► monitors.json  ─────►  bookmyshow-monitor.yml
  pick end time                                           (every 5 min)
                                                                │
  show status  ◄──────────────── state.json  ◄────────────── check, compare,
                                                            notify, commit
                                                                │
                                                                ▼
                                                        Gmail SMTP ──► your inbox
```

The repository **is** the database. `data/*.json` is the single source of
truth; the worker commits what it observed back to the repo, and the UI
mirrors its writes through the GitHub API so a Streamlit Cloud instance with
an ephemeral filesystem doesn't lose them.

Two files, two owners, so they never race:

- `data/monitors.json` — what you asked for. Written by the UI.
- `data/state.json` — what we observed. Written by the worker.

### Layout

```
app.py                      Streamlit entrypoint
run_monitor.py              worker CLI — what the schedule runs
resolve_movie.py            resolve a listing into the catalogue

platforms/
  base.py                   Provider protocol + PlatformError / PlatformBlocked
  bookmyshow.py             the only implemented provider
monitor/
  models.py                 normalised domain types (no platform knowledge)
  state.py                  persistence + monitor lifecycle
  changes.py                when something is actually worth an email
  checker.py                the engine
  catalogue.py              resolved movies / theatres / formats cache
notifications/
  email.py                  Gmail SMTP + the alert template
config/
  store.py                  atomic JSON IO + GitHub mirroring
  timezone.py               everything is IST
ui/
  theme.py                  the design system, as CSS
  components.py             status cards
tests/                      93 tests, no network, no SMTP
.github/workflows/
  bookmyshow-monitor.yml    the schedule
  resolve-movie.yml         resolve/refresh a listing from a runner
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
GH_TOKEN = "github_pat_..."          # fine-grained PAT, Contents: read & write
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

1. Open the app → **Settings** → set your notification email → **Save**.
2. **Home** → *Add a movie from BookMyShow* → paste a **Book tickets** URL
   (the one containing an `ET…` code) → **Resolve movie**.
   - If that fails with a bot check, press **Resolve on GitHub** instead. It
     dispatches `resolve-movie.yml`; the runner writes the catalogue back to
     the repo and the movie appears after you reload.
3. Pick the movie, tick the theatres, choose a format for each, pick an
   interval and an end time → **START MONITORING**.
4. Optional: **Settings → Run a check now** to force the worker immediately
   rather than waiting for the schedule.

Verify the plumbing before you rely on it: run `resolve-movie.yml` once from
the Actions tab and confirm it lists real theatres and formats. If it does,
the monitor will work.

---

## Running the worker yourself

```bash
python run_monitor.py                # check everything that is due
python run_monitor.py --force        # ignore the interval
python run_monitor.py --dry-run      # evaluate, report, send nothing
python run_monitor.py --monitor <id> # one monitor only
```

The worker exits `0` whenever the *run* completed, even if a check failed —
BookMyShow being briefly unreachable is expected, and a red X every time it
happens trains you to ignore the one that matters. Exit `1` means the run
itself broke.

## Tests

```bash
python -m pytest
```

93 tests, no network and no SMTP — a fixture fails the run if anything tries
to open a real SMTP connection. BookMyShow is replaced by a fake session
replaying a payload with the real response's widget structure, so the suite is
deterministic and doesn't poke a third party on every commit.

Covered: URL parsing, availability mapping, every failure mode (403 / 429 /
5xx / timeout / malformed JSON / restructured payload), per-theatre format
matching, the full duplicate-suppression matrix, failed-email retry, expiry,
manual stop, the Streamlit UI driven end-to-end through `AppTest`, and a check
that the worker still imports and runs with Streamlit blocked.

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
GitHub disables scheduled workflows on repos with no activity for 60 days —
push anything to re-enable. Also, scheduled runs are delayed under load; the
interval is a floor, not a promise.

**The theatre list is stale.**
**Settings → Refresh every movie's theatres**, or press *Refresh* on the
individual movie. Theatres and formats are whatever the listing said when it
was last resolved.

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
- UI from `TicketRadar UI Design System/` in this repo.
