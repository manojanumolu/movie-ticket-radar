# Why a five-minute monitor got one check: GitHub schedule delivery

**Finding:** the worker, website and workflow are working. GitHub creates very
few `schedule` runs for this repository, at random times, whatever the cron
expression says. No change to the repository's code or cron makes GitHub
deliver more. Measured on 26 Nov 2026 from GitHub's public Actions API.

## The incident

| Event (26 Nov 2026) | UTC | IST | Evidence |
|---|---|---|---|
| Monitor workflow state last changed (enabled) | 14:05:40 | 19:35:40 | workflow `updated_at` |
| Monitor `87b9d9d6` checked on creation | 14:42:22 | 20:12 | run `45841494073` (`workflow_dispatch`) log |
| Scheduled runs of any workflow after 09:19:38 UTC | none as of 17:50 | — | `actions/runs?event=schedule` |

The 13:00 UTC catalogue sync is also missing, and its workflow file has not
changed. So the problem affects the whole repository; it is not this workflow.

## History (all scheduled runs since the repository was created)

| Workflow | Cron | Expected per day | Delivered | Per day |
|---|---|---|---|---|
| BookMyShow monitor | `*/5 * * * *` | 288 | 362 runs, 13 Sep – 26 Nov | about 3–4 |
| Catalogue sync | `0 1,7,13,19 * * *` | 4 | 219 runs over 74 days | 2.96 |

* Catalogue runs start a median of 229 min after their slot (90th percentile
  342 min). This is a lower bound, because a run may belong to an earlier
  slot that was dropped.
* Monitor runs are spread evenly across `minute mod 5` (63/76/71/66/86), so
  they do not start on the cron minutes. GitHub delivers a few runs a day at
  arbitrary times.
* A `*/5` workflow and a four-times-a-day workflow get about the same number
  of runs a day. So the cron frequency does not control delivery.

What GitHub documents: scheduled runs "can be delayed during periods of high
loads" and "some queued jobs may be dropped". The reason this repository
gets this little is not visible from outside GitHub.

## What was ruled out

* Workflow configuration: `active`, `*/5 * * * *` on `main` (`7d895c7`),
  default branch `main`, not a fork, not archived, recently active (the
  60-day inactivity rule does not apply). Manual dispatches of the same file
  run and succeed.
* Worker: every run GitHub started checked, saved and finished
  (`45838842519`, `45841091751`, `45841494073`). The due logic is covered by
  `tests/test_repeated_scheduled_checks.py`. Those tests use a simulated clock
  and Firestore; they show the worker, not the scheduler.
* Website: the check count it shows (1) matches the one run that checked
  the monitor.

## How the original version achieved five minutes

The production baseline (`1f46501`) ran a 50-minute loop that re-dispatched
itself (`workflow_dispatch`). On 23–25 Nov it made 512 checking passes with a
median gap of 4.99 min. It did not depend on `schedule` events. That loop is
excluded by the owner's instructions.

## Options (each needs the owner's decision; none is implemented)

| Option | Cadence | Cost | Risk |
|---|---|---|---|
| Keep GitHub's schedule only | 3–4 runs a day, hours late | $0 | A release can be missed by hours |
| Start the workflow by hand when it matters (Actions → BookMyShow monitor → Run workflow) | When run | $0 | Manual |
| Restore the baseline self-dispatch loop | About 5 min (measured) | $0 | Excluded by the owner; long-running Actions use |
| Free outside scheduler calling `workflow_dispatch` (e.g. Google Apps Script) | About 5 min, subject to that service's own timing | $0 | Needs a GitHub token with Actions write access stored outside GitHub; about 288 runs a day |

## Re-checking delivery

    curl -s "https://api.github.com/repos/manojanumolu/movie-ticket-radar/actions/runs?event=schedule&per_page=20" \
      | jq -r '.workflow_runs[] | "\(.created_at) \(.name) \(.conclusion)"'
