#!/usr/bin/env python3
"""Worker entry point — what the GitHub Actions schedule runs.

    python run_monitor.py                 # one pass: check everything that is due
    python run_monitor.py --loop          # stay alive and keep checking (what Actions runs)
    python run_monitor.py --force         # ignore the interval (manual run / first pass)
    python run_monitor.py --monitor <id>  # one monitor only (first pass, in --loop)
    python run_monitor.py --dry-run       # check, report, send nothing

Exits 0 whenever the run itself completed. A monitor that couldn't be checked
is reported in the log but is not a workflow failure — BookMyShow being
briefly unreachable is expected, and a red X every time it happens would train
you to ignore the one that matters. Exit 1 is reserved for the run breaking.
"""

from __future__ import annotations

import argparse
import sys

from config.timezone import fmt_datetime, now_ist
from monitor.checker import run_once, short_id
from monitor.worker import DEFAULT_MAX_MINUTES, DEFAULT_POLL_SECONDS, run_loop


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Movie Ticket Radar — monitoring worker")
    parser.add_argument("--force", action="store_true",
                        help="check even if the interval hasn't elapsed")
    parser.add_argument("--monitor", default="", help="restrict to one monitor id")
    parser.add_argument("--dry-run", action="store_true",
                        help="evaluate and report, but send no email")
    parser.add_argument("--loop", action="store_true",
                        help="keep ticking for --max-minutes instead of a single pass")
    parser.add_argument("--max-minutes", type=int, default=DEFAULT_MAX_MINUTES,
                        help="how long a --loop segment stays alive")
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS,
                        help="how often a --loop segment looks for due monitors")
    parser.add_argument("--no-chain", action="store_true",
                        help="don't dispatch the next segment when this one ends")
    parser.add_argument("--no-git", action="store_true",
                        help="don't pull/commit/push between ticks")
    args = parser.parse_args(argv)

    started = now_ist()
    print(f"Movie Ticket Radar — worker run at {fmt_datetime(started)} IST")

    # A dry run is not a different notifier: it is a different tick, one that
    # evaluates everything and writes and sends nothing (``run_once`` /
    # ``run_loop`` with ``dry_run``). Swapping only the notifier used to leave
    # the rest of the tick running for real — an alert "delivered" to a print
    # statement was then marked as sent, and the real alert never went out.
    if args.dry_run:
        print("DRY RUN — every monitor is evaluated; nothing is emailed and nothing is written.")

    def report_dry_run(changes) -> None:
        if not args.dry_run:
            return
        print(f"  would email: {len(changes)}")
        for change in changes:
            print(f"    [dry-run] would email: {change.headline} ({change.venue_name} · {change.fmt})")

    try:
        if args.loop:
            loop = run_loop(
                max_minutes=max(1, args.max_minutes),
                poll_seconds=max(5, args.poll_seconds),
                force=args.force,
                monitor_id=args.monitor,
                use_git=False if args.no_git else None,
                chain=False if args.no_chain else None,
                dry_run=args.dry_run,
            )
            for line in (
                f"ticks:    {loop.ticks}",
                f"checks:   {loop.checks}",
                f"emails:   {loop.emails_sent}",
                f"commits:  {loop.commits}",
                f"handover: {'yes' if loop.handed_over else 'no'}",
            ):
                print(f"  {line}")
            report_dry_run(loop.would_send)
            for rep in loop.reports:
                for err in rep.email_errors:
                    print(f"::warning::email not sent ({err}) — retried on the next check")
            if loop.push_failures:
                print("::warning::some state commits could not be pushed during the "
                      "segment; the final commit step retries them")
            if loop.failed_ticks:
                print(f"::warning::{loop.failed_ticks} tick(s) failed during the segment; "
                      "it carried on and the checks were retried")
            # The run breaking is the one thing exit 1 is for: a store that
            # refuses the worker outright is that, and must show as a red run.
            return 1 if loop.stopped_reason == "store refused the worker" else 0

        # The single pass gets the loop's mandatory guard: inside Actions a
        # missing or unusable credential is a failed run, never a silent
        # fall-back to the (empty) JSON store.
        from config.store import running_in_actions
        from monitor.worker import require_worker_backend

        require_worker_backend(in_actions=running_in_actions())
        report = run_once(
            at=started,
            force=args.force,
            monitor_id=args.monitor,
            mirror=False,  # the workflow commits data/ once, at the end
            dry_run=args.dry_run,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"::error::worker crashed: {type(exc).__name__}: {exc}")
        return 1

    def ids(items: list[str]) -> str:
        # Public log: enough of each id to read a run, never the whole thing.
        return ", ".join(short_id(i) for i in items) if items else "—"

    for line in (
        f"checked:  {ids(report.checked)}",
        f"skipped:  {', '.join(report.skipped) if report.skipped else '—'}",
        f"expired:  {ids(report.expired)}",
        f"failed:   {ids(report.failed)}",
        f"changes:  {len(report.changes)}",
        f"emails:   {report.emails_sent}",
        # One listing read can serve several people watching the same film,
        # city and dates (monitor.sharing); say how many it saved.
        f"fetches:  {report.fetches}"
        + (f" (one read for {report.sharing.monitors} monitors; "
           f"{report.sharing.saved} avoided)" if report.sharing.saved else ""),
    ):
        print(f"  {line}")

    report_dry_run(report.would_send)
    for err in report.email_errors:
        print(f"::warning::email not sent ({err}) — it will be retried on the next check")
    if report.failed:
        print("::notice::one or more checks could not reach the platform; "
              "state was left unchanged and will retry")

    return 0


if __name__ == "__main__":
    sys.exit(main())
