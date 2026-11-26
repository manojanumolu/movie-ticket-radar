# Doomsday two-target monitor — single-pass implementation and activation decision

**Decision: KEEP STOPPED.** `af1711d4` has not been changed, extended or activated.

## Configuration (prepared, not applied)

| | |
|---|---|
| Document | `af1711d4…` (unchanged ID, owner, history and state) |
| Keep | ALLU Cinemas · Dolby Cinema; Prasads Multiplex · PCX |
| Remove | AMB Cinemas · HDR By Barco; PVR Lakeshore PXL… · PXL; Prasads Multiplex · HDR By Barco |
| Interval | 5 minutes (unchanged; both targets every pass) |
| Timestamps | The schema stores `created_at` and `monitor_until` (there are no `start_at`/`stop_at` fields). Both are preserved exactly by the tool; values are printed by its dry run. They could not be read here (no Firestore access). |
| How | Manual workflow *Monitor maintenance* → `action=narrow`, dry run first, then `confirm=APPLY` (`tools/narrow_monitor_targets.py`). |

*Extend by 24 hours* sets `monitor_until = max(old, now) + 24h`, so it cannot be used without breaking the "do not extend" requirement. The tool's `resume` action sets ACTIVE and keeps `monitor_until` exactly; it refuses once that time has passed.

## Workflow (`.github/workflows/bookmyshow-monitor.yml`)

* `cron: */5 * * * *` plus the app's dispatch; one `python run_monitor.py` pass; exit.
* No loop, no 30-second polling, no self-dispatch, no push trigger, no repository write (`contents: read`).
* `concurrency: bookmyshow-monitor`, `cancel-in-progress: false`: one pass at a time. A waiting run is replaced by a newer one, so missed runs never burst.
* `timeout-minutes: 4`. A pass killed mid-way writes nothing (state is saved at the end) and the next pass retries.
* `RADAR_RETRY_REFUSALS=0`: a 401/403 is final, with no second browser profile. `RADAR_HTTP_ATTEMPTS=2`: one retry for a request that did not complete.
* `RADAR_DISCOVERY_CLOCK=firestore`: the 15-minute city-listing throttle survives between runs (`worker_runtime/legacy_discovery`), so the city is not re-listed every pass.
* Catalogue sync is unchanged (`0 1,7,13,19 * * *`). No other monitor scheduler exists (tested).

## Measured scheduling reliability (this repository, `*/5`, 13 Sep – 26 Nov 2026)

362 of 21,312 expected scheduled runs were delivered (1.7%). That is a median of 5 a day, a median gap of 285 min and a maximum gap of 945 min. 1–22 Nov, with nothing running: 3.8 runs a day. Idle-run wall time: median 33 s, maximum 105 s. **GitHub's scheduler cannot deliver a five-minute check on this repository.** Removing the 50-minute loop does not change the scheduler.

## Cost and quota (measured per pass on the real code with the repository's Firestore tally fixture)

**Writes per pass (measured):**

| Pass | Reads | Writes | What is written |
|---|---|---|---|
| Due pass, nothing changes | 54 | 1 | monitor state |
| Due pass that re-lists the city | 55 | 2 | state + discovery clock (≤ once per 15 min while a target is unlisted; both are today) |
| First failed check in an hour | 55 | 2 | state + one ERROR history row (deduplicated per hour) |
| Pass that finds a release | 54–55 | 2+ | state + one history row per alert |

That gives **1.33 writes/pass on average** (288 state + 96 clock = 384/day), not 1. The earlier "1 write" was the minimum. Removing three targets changes none of this: 4 BookMyShow requests and 54 reads per pass with two or five targets.

| | 1 day | 5 days | 15 days |
|---|---|---|---|
| Runs, theoretical / at measured 1.7% delivery | 288 / ≈5 | 1,440 / ≈25 | 4,320 / ≈75 |
| Runner minutes, typical (passes take < 1 min; each job rounds to 1 min) | 288 | 1,440 | 4,320 |
| Runner minutes, **upper bound** (4-min timeout × every run) | 1,152 | 5,760 | 17,280 |
| Firestore reads (55/pass) | 15,840 | 79,200 | 237,600 |
| Firestore writes, normal (state + clock) | 384 | 1,920 | 5,760 |
| Firestore writes, worst (re-list retried every pass + hourly ERROR) | 600 | 3,000 | 9,000 |
| Firestore deletes / new storage | 0 / 1 small doc | 0 | 0 |
| BookMyShow requests, normal (4/pass + 1 per re-list) | 1,248 | 6,240 | 18,720 |
| BookMyShow requests, worst (refusal = 1, no retry; network error = 2 attempts) | ≤ 2,880 | ≤ 14,400 | ≤ 43,200 |
| Cost | **$0** (public repository, standard runners; Firebase has no billing) | **$0** | **$0** |

Firestore headroom: the monitor alone needs about 32% of the 50k daily reads and about 2–3% of the 20k writes. **The website's usage is not measured.** It can only be read in the Firebase console (Firestore → Usage), or in Cloud Monitoring with `monitoring.timeSeries.list`. This session has neither access, and the dashboard is an estimate ([docs](https://firebase.google.com/docs/firestore/monitor-usage)).

## Activation conditions

| # | Condition | Status |
|---|---|---|
| 1 | Two-target configuration verified | Prepared and tested; **not applied** (no Firestore access) |
| 2 | Timestamps and automatic stop preserved | Tool proves it; *Extend* would violate it |
| 3 | Five-minute single-pass workflow | Implemented and tested; GitHub cannot keep the cadence (measured) |
| 4 | No overlapping workers or duplicate schedulers | Met (one concurrency group, one scheduler, tested) |
| 5 | Firebase headroom measured | **Not met** (website usage unmeasured) |
| 6 | No costs or billing | Met |
| 7 | BookMyShow authorization | **Not met.** No written permission; the client impersonates a browser to pass the bot check |
| 8 | Rollback / immediate stop | Ready: disable the workflow, or stop the monitor in the app |
| 9 | Job Tracker evidence | **Not met** (repository disabled; notice inaccessible) |

## Best zero-cost hosted alternative for the cadence

Evaluated and **not recommended**: a Google Apps Script trigger calling `workflow_dispatch`.
- **Free limits:** consumer accounts get 90 min of trigger runtime and 20k URL fetches per day.
- **Timing:** `everyMinutes(5)` is allowed, but Google documents that trigger times may be randomized.
- **Token:** dispatch requires a fine-grained token with Actions: write. That scope also allows enabling and disabling workflows and cancelling or re-running runs.
- **Enforcement risk:** it would raise this repository's monitoring runs from ≈5 to ≈288 a day on GitHub-hosted runners. That increases, rather than reduces, the GitHub enforcement exposure while the Job Tracker cause is unknown.

## Final status (this revision)

| Item | State |
|---|---|
| Single-pass guard | A missing or revoked Firestore credential fails the run (exit 1). It never falls back to the empty JSON store (`monitor.worker.require_worker_backend`). |
| Honest client (`RADAR_CLIENT=honest`) | No TLS fingerprint replay. A truthful `TicketRadar/1.0` User-Agent, with no browser headers. 401/403/429 are final: never retried, never routed via another endpoint. Network errors get 2 attempts of 10 s per read. Catalogue sync and tools are unchanged. |
| PR #7 compatibility | Every historical document shape was reconstructed (`tests/fixtures/historical_monitor_shapes.json`). Monitors stored 16–21 Sep lack `categories`/`show_time`, and the Rules now treat an absent key as its empty default. Verified in the emulator (the previous rule fails that case). |
| Old workflow | `disabled_manually` on GitHub (verified). PR #8 replaces its file. |

**Achievable frequency:**
- **Successful checks: 0.** `platforms/http.py` records that BookMyShow refuses non-imitating clients with 403 (two CI runs). Each honest check is therefore recorded as a refused failure, until BookMyShow authorises access.
- **Attempts:** GitHub's scheduler delivered 4.9 runs a day on this repository (measured).
- **Five-minute checks:** after authorisation, the zero-cost host that can genuinely reach five minutes is a Google Apps Script time trigger (no card; 5-minute triggers; within quota). GitHub Actions is not: the schedule delivers about 4.9 runs a day, and its terms exclude activity unrelated to the repository's software.
