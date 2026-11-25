# Production Safety Audit — Doomsday Monitor Reactivation

| | |
|---|---|
| Date | 25 November 2026 (audit performed ~19:45–21:00 UTC) |
| Scope | `manojanumolu/movie-ticket-radar` @ `main` = `cb9c6a1` (merge of PR #6, head `f8e7c91`) · https://movie-ticket-radar.streamlit.app/ · Firebase/GCP `ticketradar03` |
| Mode | Read-only. No monitor, Firestore record, workflow, setting, credential, IAM, Rule, schedule or BookMyShow behaviour was changed. No email was sent. |
| Auditor access | GitHub REST (repo, PRs, commits, check runs, Actions runs **and public run logs**). **No** GCP/Firebase credentials (every `gcloud` call returned `UNAUTHENTICATED`). GitHub secrets/environments/Actions-settings endpoints were refused by this session's proxy (HTTP 403). docs.github.com was not reachable from the sandbox. |

---

## 1. Executive decision

# 🔴 RED — Do not reactivate on the current architecture

**Can we reactivate the four-theatre Doomsday monitors today?**
**Not on GitHub Actions — no.** The only *deployed* monitoring engine is still the legacy
GitHub Actions workflow (`bookmyshow-monitor.yml`). Reactivating the monitor will restart, within
minutes, the exact pattern that ran ~42 hours non-stop between 23 and 25 November: chained 50-minute
self-dispatching runner segments that scrape a third-party site every 5 minutes and commit to the repository every
~15 minutes. Nothing in PR #6 changed that. The Cloud Run worker exists **only as an isolated
repository candidate**; nothing has been provisioned.

**What *is* in good shape (this is why RED is short, not a rebuild):**

* The original records survive in the logs' evidence, and the four-theatre watch is **one** monitor (`af1711d4…`). It can be
  restarted **with its original ID** through the app's existing *Extend by 24 hours* action.
* The replacement worker (fenced Firestore lease, durable email outbox, paused 1-minute Cloud
  Scheduler, `max_instance_count = 1`) is written, tested in CI (4/4 checks green on `main`) and
  tested locally by this audit.
* PR #6 touched no production runtime code, and the website is up.

**Fastest responsible route to GREEN:** stand up the existing candidate on Cloud Scheduler + private
Cloud Run in `ticketradar03`. Disable the GitHub monitor workflow's schedule and segment chaining, take
a Firestore backup, then press *Extend* on `af1711d4`. The work is about one focused working day for
the owner with GCP admin access (§12). Monitoring can be live **today** if that work starts now. No
product rebuild is needed.

---

## 2. Release verification (Section 1)

| Question | Finding | Evidence | Confidence |
|---|---|---|---|
| PR #6 merged? | **Yes.** `merged=true`, `merged_at 2026-11-25T19:40:49Z`, head `f8e7c911…` | `GET /repos/…/pulls/6` | Confirmed |
| Current `main` | `cb9c6a130b1b3f321435c817fabb133d3742f27b` ("Merge pull request #6…") | `GET /commits/main` | Confirmed |
| CI on `main` | 4/4 check runs **success**: `pytest`, `candidate-pytest`, `firebase-emulators`, `worker-image` (run 45687574453, 2m44s) | `GET /commits/main/check-runs` | Confirmed |
| Required checks | **No branch protection on `main`** (`404 Branch not protected`), so no check is *required*. | `GET /branches/main/protection` | Confirmed |
| Consolidated implementation in `main` | Yes. 45 files, +11,853/−29 vs. baseline `1f46501`. Outside `worker_candidate/ tests/ infra/ docs/ tools/`, only `.gitattributes`, `.gitignore` and `.github/workflows/tests.yml` changed. | `git diff --name-only 1f46501 cb9c6a1` | Confirmed |
| Streamlit auto-update | Source the app runs is byte-identical to the pre-release baseline. Whether Streamlit has re-synced is therefore immaterial. The deployed commit cannot be read without Streamlit console access. | diff above | Confirmed (no impact) / Unverifiable (exact deployed SHA) |
| Website loads | **Yes.** HTTP 200. Login page renders fully (hero, Email/Password, Google sign-in, Create account). | Headless Chromium, 25 Nov ~20:00Z | Confirmed |
| Deployment errors | None from the app. Console errors were Streamlit telemetry/metrics fetches blocked by the audit sandbox's proxy (`ERR_TUNNEL_CONNECTION_FAILED`) plus two 404s on the same calls. They are not app errors. | same | High |

Local test runs at `cb9c6a1` (Python 3.13, shallow clone):
* Production layout: **2,144 passed, 48 skipped, 4 failed**
* Candidate layout (materialised): **2,180 passed, 51 skipped, 4 failed**

All 8 failures are git-history assertions (`git diff f6b73dd e0e4661 …` → exit 128). They fail because the
audit clone is shallow. CI checks out with `fetch-depth: 0`, and the same tests pass there. **No functional test fails.**

---

## 3. Existing product functionality (Section 2)

| Area | Status | Basis |
|---|---|---|
| Streamlit website / login | Working | Live load (above) |
| Showtime Explorer, catalogue, theatre listings | No code change in release. Catalogue data current: last catalogue commit `1f46501` today. | diff + git log |
| BookMyShow integration (`platforms/`) | Unchanged by release. Live logs show successful reads through 25 Nov 05:21Z. | diff + run logs |
| Firebase Auth, users, UIDs | No code change. **Not inspected in production** (no Firebase access). | — |
| Preferences, monitor history | No code change. Production store holds **53 monitor documents** (all users, all statuses) per worker log `monitor_count=53`, 25 Nov 10:29Z. | run 45595460201 |
| Email experience | Unchanged. Last real alert sent 24 Nov 07:18Z (Gmail SMTP from the Actions worker). | run logs |
| **6-hour catalogue sync** | **Intact.** `cron: "0 1,7,13,19 * * *"` (UTC). 224 runs since 13 Sep, avg 2.8 min, max 5.0 min, 223 success. | `catalogue-sync.yml`, runs API |

**Regressions found: none.** **Missing functionality: none.** Production Auth/Firestore internals
are *unverified* rather than broken.

---

## 4. Four-theatre Doomsday monitor verification (Section 3)

Firestore could not be read directly. The evidence below comes from the **public logs of all 54
successful monitor runs** since 23 Nov 10:00Z. These logs print 8-character ID prefixes, titles and targets.

### 4.1 Monitor timeline

| ID prefix | Targets | First seen | Last checked | Reading |
|---|---|---|---|---|
| `e195392d` | 5 | 23 Nov 11:10Z | 23 Nov 11:10Z | First version, replaced within 4 min |
| `febc20b8` | 4 (no ALLU) | 23 Nov 11:13Z | 23 Nov 11:13Z | Short-lived edit |
| `5d133d77` | 1 (ALLU) | 23 Nov 11:13Z | 23 Nov 11:59Z | Short-lived |
| **`af1711d4`** | **5** | **23 Nov 11:14Z** | **25 Nov 05:21Z** | **The four-theatre watch** |
| **`6150dc3b`** | 1 (ALLU Dolby) | 23 Nov 12:06Z | 25 Nov 05:21Z | Second, overlapping watch — **a different account** |
| `c9a80293` | 1 (Prasads, Any format) | 23 Nov 16:34Z | 23 Nov 16:34Z | One-off |

From 23 Nov 12:06Z the logs consistently show `running_monitor_count=2` and
`one read for 2 monitors (2 account(s))`.

### 4.2 Actual theatre coverage

**Two Firestore documents ≠ two theatres.** `af1711d4` alone covers all four theatres:

| # | Theatre | Format condition | Last observed state (25 Nov 05:21Z) |
|---|---|---|---|
| 1 | ALLU Cinemas | Dolby Cinema | SHOW_NOT_AVAILABLE (listed: Infinity Vision 3D, Laser 4K Atmos) |
| 2 | AMB Cinemas | HDR By Barco | AVAILABLE (1 showtime bookable) |
| 3 | PVR Lakeshore PXL 4K Laser ATMOS DTS-X, Y Junction | PXL | SOLD_OUT |
| 4 | Prasads Multiplex | PCX | SHOW_NOT_AVAILABLE |
| 4 | Prasads Multiplex | HDR By Barco | SHOW_NOT_AVAILABLE |

Movie: *Avengers: Doomsday*, Hyderabad. The configuration groups four BookMyShow format events
(`ET00439706`, `ET00526287`, `ET00526306`, `ET00526320`) for show date **17 Dec 2026**
(`@20261217`). Check cadence observed: every **5 minutes**, with a city re-listing every ~15–20 minutes.

`6150dc3b` watches **only** ALLU · Dolby Cinema, for a second account. It duplicates `af1711d4`'s ALLU
target, but the alert goes to a different person.

### 4.3 Preservation and state

| Question | Answer | Confidence |
|---|---|---|
| Are records preserved? | Both still existed at 25 Nov 10:29Z: total 53, running 0. Both were checked until 05:21Z. The last segment ended `stopped=nothing running` at 05:25Z, which matches a manual stop at ~10:52 IST. | High (inferred from logs) |
| Stopped / expired / deleted? | Running count fell 2 → 0 between 05:21Z and the next tick, and the total count did not drop, so they were **stopped (or expired), not deleted**. The app's *Stop* writes `status=STOPPED`, `stopped_reason="Stopped manually."`. Exact status field: **unverified**. | Medium |
| IDs, preferences, intervals, history intact? | Nothing in this release can modify them: Firestore Rules allow an owner only `status/stopped_at/stopped_reason` changes on stop, and no worker has run since. **Not read directly.** | Medium |
| Reactivate without recreating? | **Yes.** *Extend by 24 hours* (`app.py:920`, `state.extend_monitor`) sets `status=ACTIVE` and `monitor_until = max(old_until, now) + 24h`, and keeps the same document ID, targets, interval and history. It is subject to the active-monitor limit and request-cost rule (≤120). | Confirmed in code |
| Would reactivation duplicate monitors? | No new document is created. Reactivating **both** IDs re-creates today's overlap on ALLU · Dolby, with one email per account — not a duplicate to one inbox. Reactivate only `af1711d4` unless the second account's owner asks. | High |
| Would it duplicate emails? | On the **legacy** engine there is no durable send-once record beyond per-monitor state. The 5 alerts sent on 23–24 Nov were all *"New showtime — Avengers: Doomsday at AMB Cinemas"* while the reported count stayed at "1 showtime bookable". These may be genuine rotations of the single AMB HDR show, or re-alerts. **Unresolved — read `af1711d4`'s history before reactivating.** | Open |

**Owner action before reactivation (10 minutes, read-only):** in the Firebase console, open
`monitors/af1711d4…` and `6150dc3b…`. Confirm `status`, `monitor_until`, `interval_minutes`, the 5
targets above, `notify_email`, and the history entries for the five AMB alerts.

---

## 5. Current production architecture

```
                 ┌──────────────────────────────── GitHub (public repo) ───────────────────────────────┐
 User browser ──▶│ Streamlit Community Cloud app (branch main)                                         │
   │  Firebase    │   • reads/writes monitors in Firestore (client Rules)                                │
   │  Auth        │   • holds GH_TOKEN → commits data/*, workflow_dispatch "check now"                   │
   ▼              │                                                                                     │
 Firebase Auth   │  Actions: bookmyshow-monitor.yml  (push data/monitors.json · cron */5 · dispatch)   │
                 │     └─ run_monitor.py --loop 50 min, tick 30 s ──▶ BookMyShow (every 5 min / monitor)│
 Firestore  ◀────┼──── service-account JSON key (secret FIREBASE_SERVICE_ACCOUNT, env "production")    │
 (ticketradar03) │     └─ Gmail SMTP (GMAIL_APP_PASSWORD) ──▶ user inbox                                │
                 │     └─ git commit data/ every ~15 min, then self-dispatches next segment (actions:write)│
                 │  Actions: catalogue-sync.yml  cron 0 1,7,13,19 * * *  (~3 min) ──▶ data/catalogue.json│
                 └─────────────────────────────────────────────────────────────────────────────────────┘
 Cloud Run worker / Cloud Scheduler / backups:  NOT DEPLOYED (repository candidate + unapplied Terraform)
```

## 6. Old vs. new architecture, and the three schedulers

| | **A. Catalogue sync** (keep) | **B. Legacy GitHub monitor** (today's engine) | **C. Proposed dedicated worker** |
|---|---|---|---|
| Where | GitHub Actions | GitHub Actions | Cloud Run (private, IAM invoker) |
| Trigger | `0 1,7,13,19 * * *` UTC | `*/5` cron + push of `data/monitors.json` + self-`workflow_dispatch` | Cloud Scheduler `* * * * *` (Terraform: `paused = true`) |
| Run shape | ~3 min, 4×/day | **50-min segment, chains itself while any monitor runs** | ≤55 s request, `max_instance_count = 1` |
| Writes | `data/catalogue.json`, `showtimes.json` | Firestore + `data/*` commits + email | Firestore only (no git) + email outbox |
| Concurrency control | Concurrency group | Concurrency group only — **no lease, no fencing** | Firestore `worker_runtime/control` lease + epoch fencing + per-request idempotency |
| Email | — | Direct SMTP, state-mark after send | Durable outbox: `pending → sending → accepted/retry/failed/uncertain`, manual reconciliation |
| Credential | none | Long-lived SA JSON key in GitHub secret | Attached service account (ADC). Fenced mode **refuses** legacy key mode. |
| Status | Live, healthy | **Live (enabled), idle because 0 running monitors** | **Not deployed** |

C introduces **no** catalogue scheduler. A remains as is.

---

## 7. GitHub Actions risk assessment (Section 4)

### Confirmed facts

* **8 workflows**, all `active` (enabled). Only two run unattended: *BookMyShow monitor* and *Catalogue sync*.
  The rest are manual (`workflow_dispatch`) or CI.
* *BookMyShow monitor* history (13 Sep → 25 Nov): **823 runs** (388 self/app dispatch, 360 schedule, 75 push).
  360 runs exceeded 30 min. Max 90 min. Wall-clock total ≈ 18,842 min, which over-counts because queued-then-cancelled
  runs are included.
* **Doomsday window:** 23 Nov 11:10Z → 25 Nov 05:25Z, back-to-back 50-min segments.
  Daily wall-clock: 23 Nov 16.2 h, **24 Nov 24.6 h**, 25 Nov 5.5 h. That is effectively an always-on worker.
  September shows the same pattern (~199 h).
* **47 bot commits** to `main` in that window (≈ one per 15 min) and **2,880 BookMyShow-directed checks/day** at the
  5-min interval, plus city re-listings.
* Workflow permissions: `contents: write`, `actions: write`. The workflow self-dispatches the next segment. The concurrency group
  (`cancel-in-progress: false`) queues rather than overlaps. 116 "cancelled" runs are superseded queued dispatches.
* Billable minutes reported `0`. This is a public repository on standard runners. **Free does not mean permitted.**
* The `*/5` schedule is **still enabled**. With zero running monitors each run exits in ~30 s
  (`checks=0`, no BookMyShow request; e.g. run 45595460201). GitHub delivered only one such run today.
* Old monitor **still uses long-running workers**: yes, unchanged.
* New Cloud Run worker deployed: **no**.
* Would monitoring still consume Actions time after reactivation today: **yes, all of it.**

### Terms (cited from the auditor's knowledge; **docs.github.com could not be fetched from the sandbox — verify the current text**)

GitHub's *Additional Product Terms → Actions* list prohibited uses, including *"serverless computing"* and
*"any other activity unrelated to the production, testing, deployment, or publication of the software
project associated with the repository where GitHub Actions are used"*, and reserve the right to limit or
disable Actions usage.

### Assessment

* **Confirmed:** Job Tracker was disabled by GitHub Staff on 24 Nov 2026. The trigger is unknown.
* **Estimate (high confidence):** the legacy monitor matches the profile those terms describe. It is a
  24×7 polling service for a non-software purpose, chained through self-dispatch, with high-frequency bot commits.
  Reactivating it is a **meaningful, material risk** of a similar action against this repository, and
  possibly the account. That would also take down catalogue sync, which drives the website.
* **Speculation:** whether GitHub would act, and when. There is **no evidence** GitHub has approved this usage.
* Catalogue sync (≈12 min/day, purpose: refresh data the app builds from) is a much smaller footprint. It still
  scrapes a third party, so treat it as low-to-moderate residual risk and keep it as is (out of scope to change).

**This is the decisive blocker.**

---

## 8. Cloud Run architecture findings (Section 5)

| Question | Answer |
|---|---|
| Deployed or repo-only? | **Repo-only.** `worker_candidate/` (overlay + `runtime.patch`) is deliberately unreachable from production imports and workflow discovery. `infra/monitoring` is an **unapplied** template naming a *staging* project and **shadow** SAs (`radar-shadow-worker`, `radar-shadow-invoker`). |
| Cloud Scheduler configured / paused? | Defined in Terraform only, `schedule = "* * * * *"`, `paused = true`. Not provisioned (unverifiable in GCP, but no state/backend exists in repo). |
| Intended interval | Scheduler tick every minute. Service attempts a slot at start and ~30 s. Each monitor is still checked only when its own interval (5/10/15/30 min) is due. |
| Only active monitors? | Yes. It reuses the legacy checker: `is_running(at)` → ACTIVE and not past `monitor_until`. |
| Zero active monitors | No BookMyShow request (same code path as today's `checks=0` runs). |
| Unnecessary BMS requests? | No more than legacy. Same sharing/coalescing (one listing read per film+city+date for all watchers), same discovery throttle. |
| Durable leases / version checks? | **Yes.** Transactional `worker_runtime/control` with authority + positive integer epoch + holder + `lease_until`. Every write asserts the lease (`execution.py:68–74`). Per-monitor claim/finish under the lease. |
| Two workers checking one monitor? | Prevented by the lease (only one holder per epoch), `max_instance_count = 1`, and request-id idempotency (`already_checked`). |
| Old workflow + new worker both active? | **Not prevented in code.** The legacy runner has **no** knowledge of `worker_runtime/control`. Exclusion relies on the cutover procedure (disable legacy schedule/dispatch/SMTP first). **This must be an explicit step.** |
| Rollback | Documented (`docs/monitoring-operations.md` §7): pause scheduler, bump epoch, drain, reconcile outbox. Revert of the release is a single commit. |

---

## 9. Firebase, credentials and security (Section 6)

| Item | Finding | Status |
|---|---|---|
| Deployed Firestore Rules | Repo `firestore.rules` is strict: owner-scoped monitors, stop/expire-only diffs, worker-only collections closed. **Deployed version not readable** (no Firebase access). | **Unverified** |
| Effective IAM | Not readable. | **Unverified** |
| Service-account key | Long-lived JSON key used via GitHub secret `FIREBASE_SERVICE_ACCOUNT` by `bookmyshow-monitor`, `rules-audit` (env `production`) and `grant-admin` (env `admin`). Per its header, `grant-admin` is meant to use a *different* claims-only SA, but it reads the **same secret name**. Whether the `admin` environment overrides it is unverifiable (settings endpoints refused). | **Confirm** |
| Keyless / federated identity | Code ready (`config/worker_credentials.py`: `adc` / `federated` / `legacy`). Fenced mode refuses `legacy`. No pool/provider provisioned. | Prepared, not live |
| Exposed credentials in repo | None found. Scanned tracked files for private keys, `AIza…`, `ghp_`, `github_pat_`, SA JSON. Only placeholders in `.env.example` and tests. | Clean (tracked files) |
| Streamlit secrets | App holds `GH_TOKEN` (commits + dispatch) and Gmail credentials (`notifications/email.py`). **Not inspectable.** Owner should confirm `GH_TOKEN` is a fine-grained PAT scoped to this repo only. | **Confirm** |
| Firestore backups | Only an opt-in **staging** schedule in Terraform (`enable_shadow_backup_schedule`, default off). **No evidence of a production backup.** | **Gap** |
| Auth export / recovery | Tooling exists (`tools/export_auth_backup.py`, `tools/verify_recovery.py`). Tested on emulators only. | Prepared |
| Isolated real restore done? | **No.** Only the emulator drill in CI. | Gap (not a blocker for this decision) |
| Monitor records/history recoverable? | Only if a managed backup exists. **Unverified.** | **Gap — fix before activation** |

**Must-fix before activation:** take one on-demand Firestore backup (or enable PITR) so that `af1711d4` and
its history are recoverable. Rules/IAM verification is a 15-minute read-only console check.

---

## 10. Monitoring and email reliability (Section 7)

| Mechanism | Legacy (live) | Candidate |
|---|---|---|
| Claiming / leases | None — relies on GitHub concurrency group | Firestore lease + epoch fencing |
| Concurrent worker protection | Single queued group; a re-run of an old run or manual local run is not excluded | Transactional; stale holder loses lease |
| Durable notification intent | No. Send, then mark. A crash between SMTP accept and the mark → **duplicate on next tick**. | Yes. Outbox row written before send. A stranded `sending` becomes `uncertain`, never blindly resent. |
| Retry | Every tick until success (unbounded) | Only explicit SMTP refusal, exponential backoff, capped attempts |
| SMTP acceptance vs inbox | Not distinguished | `accepted` = SMTP accepted only. Inbox needs separate evidence (documented). |
| Crash / restart recovery | Final "Commit state" step; mark kept in memory | Reconciliation method with evidence digest |
| Only one sender? | Today **yes**: GitHub monitor workflow is the only automated sender. The Streamlit app also holds Gmail creds (used for non-alert mail, e.g. account email). | Cutover must remove legacy SMTP capability **before** enabling fenced sender |

The candidate's tests pass locally (2,180) and in CI, including the emulator suite. Real SMTP and real Firestore
behaviour are untested (by design).

**Verdict:** the candidate is materially safer than legacy for both missed and duplicate alerts. The legacy duplicate
window is narrow but real. The 5 repeated AMB alerts (§4.3) must be explained from history.

---

## 11. BookMyShow assessment (Section 8)

* **Technical (audited):** HTTP client via `platforms/bookmyshow.py` / quickbook catalogue endpoint
  (`catalogue via 'quickbook'` in logs). Per-tick request budget (`[checker] request budget`), listing sharing
  across watchers, and a re-listing throttle (~15 min). **Zero 403/429 responses** in all 54 Doomsday-window logs
  (the only matches were hash/temp-path noise). Load during the window: ~1 listing read per 5 min + 1 city list
  per ~15–20 min. Stored data: show/format/seat-category availability. No personal data from BookMyShow.
* **The candidate preserves collection behaviour:** same checker, same platform module, same budget. Moving from GitHub
  to Cloud Run IPs may change how BookMyShow's bot defences respond. The repository itself notes that Actions runners
  "get through" more often than some hosts. **Test one shadow read from Cloud Run before cutover.**
* **Contractual/legal:** no written authorization from BookMyShow exists in the repository or evidence. Automated
  access to a consumer ticketing site is commonly restricted by its terms of use. This audit makes **no** claim that it
  is permitted.
* **Is it a blocker?** It is **not** the technical blocker for today's decision, and it applies equally to legacy,
  Cloud Run and the six-hour catalogue sync. Treat it as a **separate, parallel authorization step**. Low request
  volume (one shared read per 5 min) keeps the footprint small while that request is pending. If you want
  zero contractual risk, wait for the provider's answer. That is a business decision, not something code can fix.

---

## 12. Remaining actions, owners, time

Owner = repository/GCP owner unless noted. **R** = repository change (none required; the code already exists).
**P** = production/cloud action.

| # | Action | Type | Owner | Est. |
|---|---|---|---|---|
| 1 | Read `af1711d4` + `6150dc3b` in Firebase console; confirm status, targets, interval, history; explain 5 AMB alerts | P (read) | Owner | 15 min |
| 2 | Verify deployed Rules = repo `firestore.rules`; list IAM members on `ticketradar03` | P (read) | Owner | 15 min |
| 3 | On-demand Firestore backup (`gcloud firestore backups schedules create` or export to GCS) and Auth export to private storage | P | Owner | 30 min |
| 4 | **Neutralise legacy engine:** in the GitHub UI, *Disable workflow* "BookMyShow monitor" (reversible). Confirm no queued/in-progress run. | P (GitHub setting) | Owner | 5 min |
| 5 | Create production identities: worker SA (Firestore user + Auth viewer), scheduler invoker SA (run.invoker on this service only). No keys. | P | Owner | 30 min |
| 6 | Build image from materialised candidate (`tools/materialize_worker_candidate.py`), push to Artifact Registry, deploy Cloud Run **private**, `max-instances=1`, `WORKER_MODE=shadow` | P | Owner | 45 min |
| 7 | Shadow smoke test: `/healthz`, `/check fixture-monitor`, and one real BookMyShow read from Cloud Run (no mail) | P | Owner | 30 min |
| 8 | Store Gmail app password in Secret Manager; grant to worker SA only | P | Owner | 15 min |
| 9 | Initialise `worker_runtime/control` {authority, epoch: 1, holder: "", lease_until: ""}; redeploy `WORKER_MODE=fenced`, `FIREBASE_CREDENTIAL_MODE=adc`, `FIREBASE_PROJECT_ID`/`FIREBASE_EXPECTED_PROJECT=ticketradar03`, `WORKER_AUTHORITY` | P | Owner | 30 min |
| 10 | Create Cloud Scheduler job (OIDC, audience = service URL) **paused** → resume | P | Owner | 15 min |
| 11 | Reactivate `af1711d4` via app *Extend by 24 hours* (see §13) | P | Owner | 5 min |
| 12 | Written authorization request to BookMyShow | Business | Owner | parallel, external |
| 13 | Branch protection requiring the 4 CI checks on `main` | P (GitHub) | Owner | 5 min |

Total for actions 1–11: **~4–5 hours** of focused work. Estimated cost: Cloud Run ≈ 1,440 short requests/day
(≈US$0–3/month above free tier), Scheduler (free tier ≤3 jobs), Firestore backup storage (cents). **Under
US$5/month (estimate).**

**Repository fixes needed before GREEN:** none are strictly required. The candidate is complete. Optional hardening, to do
later on a separate branch: make the legacy runner refuse to run when `worker_runtime/control.authority` is set
(belt-and-braces against both engines being live), and gate `app.py`'s dispatch/commit path off when the Cloud Run
worker is authoritative. Until then, step 4 (disable workflow) **is** the safety control.

---

## 13. Activation procedure (after steps 1–10)

1. **One engine:** GitHub → Actions → *BookMyShow monitor* shows **Disabled**. `GET /actions/runs?status=in_progress`
   is empty. Cloud Run shows 1 revision, fenced, `max-instances=1`. Scheduler is resumed.
2. **One sender:** only the Cloud Run worker holds the Gmail secret used for alerts. The GitHub `production` env's
   `GMAIL_APP_PASSWORD` is unreachable because the workflow is disabled. Optionally delete the secret after a week of stable running.
3. **Reactivate:** sign in as `af1711d4`'s owner → Monitors → Finished → **Extend by 24 hours**. The ID, targets and history
   are preserved. Note: the app will try to commit/dispatch the disabled workflow. GitHub rejects the dispatch, and the
   commit is harmless. Leave `6150dc3b` stopped unless its owner asks.
4. **Verify all four theatres:** in Cloud Run logs within ≤6 min, expect `[checker] af1711d4 — Avengers: Doomsday (5 target(s))`
   followed by 5 result lines: ALLU·Dolby Cinema, AMB·HDR By Barco, PVR Lakeshore·PXL, Prasads·PCX, Prasads·HDR By Barco.
5. **Notification test:** do **not** fake a production alert. Before step 3, create a throwaway monitor on your own
   account for a film/theatre currently AVAILABLE, with a 24 h window. Expect exactly one email and an `accepted` outbox row,
   then confirm inbox receipt. Stop and delete it.
6. **Watch the first hour:** scheduler success rate, 0 `uncertain`/`sending` outbox rows, no 403/429 from BookMyShow.
7. **Daily:** *Extend* adds 24 h per press (cap per `policy.MAX_MONITOR_DAYS`). Re-extend or set the end date for the 17 Dec show.

### Rollback

1. Pause the Cloud Scheduler job (stops all checks in ≤1 min).
2. Bump `worker_runtime/control.epoch` (fences any in-flight request).
3. Reconcile any `sending`/`uncertain` outbox rows before any resend.
4. The monitor stays in Firestore with its ID and history. Stop it from the app if desired.
5. **Do not** re-enable the GitHub monitor workflow as a fallback. That re-creates the risk this audit blocks.

### Safest practical alternative until step 10 is done

Keep `af1711d4` stopped (no data loss; Extend restores it). Check the four theatres manually (or with BookMyShow's own
"interested" notification, where offered). The show date is 17 Dec, so a gap of hours carries little risk.

---

## 14. Evidence index

| Evidence | Location |
|---|---|
| PR #6 status | `GET /repos/manojanumolu/movie-ticket-radar/pulls/6` |
| `main` checks | Check runs on `cb9c6a1`; Tests run 45687574453 |
| Last segment (monitors stop) | Run [45549052214](https://github.com/manojanumolu/movie-ticket-radar/actions/runs/45549052214) — `segment end … stopped=nothing running` 05:25Z |
| Idle scheduled run, 53 monitors / 0 running | Run [45595460201](https://github.com/manojanumolu/movie-ticket-radar/actions/runs/45595460201) |
| First Doomsday segment (`e195392d`, AMB/PVR/Prasads/ALLU targets) | Run [45162727323](https://github.com/manojanumolu/movie-ticket-radar/actions/runs/45162727323) |
| AMB alert 24 Nov 07:18Z | Doomsday-window segment logs (54 runs since 23 Nov 10:00Z) |
| Runner-time statistics | `GET /actions/workflows/357320465/runs` (823 runs), `…/357328962/runs` (224 runs); `GET /actions/runs/45549052214/timing` (billable 0, 2,322 s) |
| Live site | Headless Chromium load, HTTP 200, login page text captured |
| Tests | Local: 2,144 / 2,180 passed; 4+4 history-dependent failures due to shallow clone |
| Code references | `.github/workflows/bookmyshow-monitor.yml`, `catalogue-sync.yml`; `app.py:893–925`; `monitor/state.py:1105`; `firestore.rules:145–172`; `worker_candidate/overlay/monitor/execution.py`; `notifications/outbox.py`; `infra/monitoring/main.tf` |

### Not verified (no access) — do not treat as checked

Firestore documents (direct), deployed Firestore Rules, GCP IAM, service-account key inventory, backups,
Auth export state, Streamlit secrets and deployed SHA, GitHub repository/environment secrets and Actions
settings, current text of GitHub's Actions terms, BookMyShow terms of use.

### Overall confidence

* **RED for legacy reactivation:** high. It rests on confirmed facts: the deployed engine and its measured usage pattern.
* **Coverage of `af1711d4`:** high (log-derived).
* **Route to GREEN:** medium-high. The code is tested; the production environment is unverified.
