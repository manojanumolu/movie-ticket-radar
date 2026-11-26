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

Per due pass: 54 Firestore reads (1 active monitor + the whole `monitor_state` collection, 53 in the fixture), 1 clock read, 1 state write, and 1 clock write per re-list (≤ every third pass). BookMyShow: 4 requests, plus 1 city listing per re-list. The same for two or five targets.

| | 1 day | 5 days | 15 days |
|---|---|---|---|
| Runs (theoretical) | 288 | 1,440 | 4,320 |
| Runs (measured delivery 1.7%) | ≈ 5 | ≈ 25 | ≈ 75 |
| Runner minutes, theoretical (≤ 1 min each) | ≤ 288 | ≤ 1,440 | ≤ 4,320 |
| Firestore reads, theoretical | 15,840 | 79,200 | 237,600 |
| Firestore writes, theoretical (+ alert/history rows) | 384 | 1,920 | 5,760 |
| Firestore deletes / storage | 0 / +1 small doc | 0 | 0 |
| BookMyShow requests, theoretical | 1,248 | 6,240 | 18,720 |
| Emails | one per real release/new showtime (Gmail limit ≈ 500/day) | | |
| Cost | **$0** (public repository, standard runners; Firebase has no billing) | **$0** | **$0** |

Worst case per pass:
- **Refusals:** a refusal costs 1 request, never retried, so ≤ 5 requests.
- **Network failures:** every read failing twice costs ≤ 10 requests and about 3.9 min. That is under the 4-min stop.
- **Recording:** failed checks are recorded as ERROR history with availability unchanged, never as successful checks.

Firestore headroom: the monitor alone would use about 32% of the 50k daily reads. **The website's own usage is not measured.** The Firebase console *Usage* tab is required.

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

A free Google Apps Script time-driven trigger (every 5 min, no card) that calls GitHub's `workflow_dispatch` API with a fine-grained token limited to *Actions: write* on this repository. Dispatched runs are not subject to scheduled-run drops. Not set up here: it needs your Google account, and it does not change conditions 7 and 9.
