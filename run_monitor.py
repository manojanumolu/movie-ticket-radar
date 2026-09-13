#!/usr/bin/env python3
"""Worker entry point — what the GitHub Actions schedule runs.

    python run_monitor.py                 # check everything that is due
    python run_monitor.py --force         # ignore the interval (manual run)
    python run_monitor.py --monitor <id>  # one monitor only
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
from monitor.checker import run_once


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Movie Ticket Radar — monitoring worker")
    parser.add_argument("--force", action="store_true",
                        help="check even if the interval hasn't elapsed")
    parser.add_argument("--monitor", default="", help="restrict to one monitor id")
    parser.add_argument("--dry-run", action="store_true",
                        help="evaluate and report, but send no email")
    args = parser.parse_args(argv)

    started = now_ist()
    print(f"Movie Ticket Radar — worker run at {fmt_datetime(started)} IST")

    notifier = None
    if args.dry_run:
        def notifier(monitor, change):  # noqa: ARG001 - signature must match
            print(f"    [dry-run] would email: {change.headline} ({change.venue_name})")

    try:
        report = run_once(
            at=started,
            force=args.force,
            monitor_id=args.monitor,
            mirror=False,  # the workflow commits data/ once, at the end
            notifier=notifier,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"::error::worker crashed: {type(exc).__name__}: {exc}")
        return 1

    for line in (
        f"checked:  {report.checked or '—'}",
        f"skipped:  {report.skipped or '—'}",
        f"expired:  {report.expired or '—'}",
        f"failed:   {report.failed or '—'}",
        f"changes:  {len(report.changes)}",
        f"emails:   {report.emails_sent}",
    ):
        print(f"  {line}")

    for err in report.email_errors:
        print(f"::warning::email not sent ({err}) — it will be retried on the next check")
    if report.failed:
        print("::notice::one or more checks could not reach the platform; "
              "state was left unchanged and will retry")

    return 0


if __name__ == "__main__":
    sys.exit(main())
