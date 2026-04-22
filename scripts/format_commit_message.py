#!/usr/bin/env python3
"""Format a Git commit message from a fetch_crs.py last-run-summary.json.

Usage:
    python scripts/format_commit_message.py data/last-run-summary.json

Output (to stdout) format matches the spec:

    Add N new CRS reports (YYYY-MM-DD)

    IF13131: Title of report 1
    IN12516: Title of report 2

    Failed:
      IF99999: 404 on PDF
"""

import json
import sys


def main():
    if len(sys.argv) != 2:
        print("usage: format_commit_message.py <summary.json>", file=sys.stderr)
        sys.exit(2)

    with open(sys.argv[1], encoding="utf-8") as f:
        s = json.load(f)

    succeeded = s.get("succeeded", [])
    failed = s.get("failed", [])
    n = len(succeeded)
    start, end = s.get("date_range", ["", ""])
    range_str = start if start == end else f"{start} to {end}"

    noun = "report" if n == 1 else "reports"
    lines = [f"Add {n} new CRS {noun} ({range_str})", ""]
    for r in succeeded:
        lines.append(f"{r['id']}: {r['title']}")
    if failed:
        lines.append("")
        lines.append("Failed:")
        for r in failed:
            lines.append(f"  {r['id']}: {r['reason']}")

    print("\n".join(lines))


if __name__ == "__main__":
    main()
