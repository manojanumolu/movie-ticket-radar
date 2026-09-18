#!/usr/bin/env python3
"""Refresh ``auth/disposable_domains.community.txt`` from its upstream.

    python tools/refresh_disposable_list.py

The upstream is the community-maintained, CC0 blocklist at
https://github.com/disposable-email-domains/disposable-email-domains — the
list most sign-up forms use, updated as providers such as temp-mail.org
rotate their mailbox domains. The file is written with a header naming the
upstream commit and date, so a diff of it is a diff of the upstream.

Run this by hand, review the diff, commit. Never at runtime: sign-up must
not depend on a network fetch. A domain rotated in after the last refresh is
not caught until the next one — the list is a bar, not a guarantee.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

REPO = "disposable-email-domains/disposable-email-domains"
RAW = f"https://raw.githubusercontent.com/{REPO}/main/disposable_email_blocklist.conf"
API = f"https://api.github.com/repos/{REPO}/commits?per_page=1&path=disposable_email_blocklist.conf"
TARGET = Path(__file__).resolve().parent.parent / "auth" / "disposable_domains.community.txt"


def main() -> int:
    with urllib.request.urlopen(API, timeout=30) as response:
        latest = json.load(response)[0]
    sha, when = latest["sha"][:12], latest["commit"]["committer"]["date"]
    with urllib.request.urlopen(RAW, timeout=30) as response:
        raw = response.read().decode("utf-8")
    domains = sorted({line.strip().lower() for line in raw.splitlines()
                      if line.strip() and not line.startswith("#")})
    header = (
        "# Community disposable-email domain blocklist — vendored, do not edit by hand.\n"
        f"# Source: https://github.com/{REPO} (CC0 1.0), file disposable_email_blocklist.conf\n"
        f"# Upstream commit {sha} of {when}; refreshed with tools/refresh_disposable_list.py\n"
        "# Local additions go in disposable_domains.txt; exceptions in disposable_allowlist.txt.\n"
    )
    TARGET.write_text(header + "\n".join(domains) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {len(domains)} domains from upstream {sha} ({when}) to {TARGET.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
