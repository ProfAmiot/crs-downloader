#!/usr/bin/env python3
"""
CRS Tracker — fetch newly published Congressional Research Service reports.

Queries the Congress.gov API for reports updated in a given date window,
compares each report's current PDF URL against the local SQLite DB, and
downloads any reports that are new or have a new PDF version. Version
changes are detected by comparing PDF URL strings (e.g. IF13131.5.pdf vs
IF13131.6.pdf) — a republished report has a different URL, a same-version
re-index does not.

Usage:
    python fetch_crs.py                                 # yesterday, ./crs-tracker-data
    python fetch_crs.py --start-date 2026-04-17 --end-date 2026-04-18
    python fetch_crs.py --dry-run                       # preview without downloading PDFs
    python fetch_crs.py --api-key YOUR_KEY              # override env var

Requires:
    - curl_cffi (preferred) or requests as fallback
    - A Congress.gov API key (https://api.congress.gov/sign-up/)
"""

import argparse
import csv
import datetime as dt
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

# Try curl_cffi first for Cloudflare bypass; fall back to requests with a
# browser UA if it's not installed. curl_cffi impersonates a real Chrome TLS
# handshake, which is what gets us past Cloudflare's JA3 fingerprinting.
try:
    from curl_cffi import requests as http  # type: ignore
    USING_CURL_CFFI = True
except ImportError:
    import requests as http  # type: ignore
    USING_CURL_CFFI = False


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

API_LIST_URL = "https://api.congress.gov/v3/crsreport"
API_DETAIL_URL = "https://api.congress.gov/v3/crsreport/{report_id}"
API_PAGE_SIZE = 250
API_MAX_PAGES = 20  # 250 * 20 = 5000 reports — well above any single-day expectation

# Headers that make our request look like a normal browser. Cloudflare is
# mostly checking TLS fingerprint (handled by curl_cffi) but a plausible UA
# never hurts.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/html,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    title TEXT,
    url TEXT,
    publication_date TEXT,
    authors TEXT,
    topics TEXT,
    pdf_path TEXT,
    pdf_url TEXT,
    last_update_date TEXT,
    downloaded_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_pubdate ON reports(publication_date);
CREATE INDEX IF NOT EXISTS idx_updated ON reports(last_update_date);
"""


def init_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def get_stored_state(conn: sqlite3.Connection, report_id: str) -> tuple | None:
    """Return (last_update_date, pdf_url) for a stored report, or None."""
    row = conn.execute(
        "SELECT last_update_date, pdf_url FROM reports WHERE id = ?",
        (report_id,),
    ).fetchone()
    return row


def record_report(conn: sqlite3.Connection, meta: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO reports
        (id, title, url, publication_date, authors, topics,
         pdf_path, pdf_url, last_update_date, downloaded_at)
        VALUES (:id, :title, :url, :publication_date, :authors, :topics,
                :pdf_path, :pdf_url, :last_update_date, :downloaded_at)
        """,
        meta,
    )
    conn.commit()


def touch_update_date(conn: sqlite3.Connection, report_id: str, update_date: str) -> None:
    """Update just the last_update_date when the report was touched server-side
    but its PDF URL didn't change (no new version)."""
    conn.execute(
        "UPDATE reports SET last_update_date = ? WHERE id = ?",
        (update_date, report_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# API listing fetch
# ---------------------------------------------------------------------------


def fetch_api_listing(session, api_key: str, start_date: dt.date, end_date: dt.date) -> list[dict]:
    """Return all CRS reports whose updateDate falls in [start_date, end_date+1d).

    Pages through the API in API_PAGE_SIZE chunks, sorted by updateDate desc.
    Each returned dict has at minimum 'id', 'title', 'updateDate', 'publishDate'.
    """
    from_dt = f"{start_date.isoformat()}T00:00:00Z"
    to_dt = f"{(end_date + dt.timedelta(days=1)).isoformat()}T00:00:00Z"
    print(f"[fetch] API listing: updateDate in [{from_dt}, {to_dt})", file=sys.stderr)

    items: list[dict] = []
    for page in range(API_MAX_PAGES):
        params = {
            "api_key": api_key,
            "format": "json",
            "fromDateTime": from_dt,
            "toDateTime": to_dt,
            "limit": API_PAGE_SIZE,
            "offset": page * API_PAGE_SIZE,
            "sort": "updateDate desc",
        }
        kwargs = {"params": params, "headers": BROWSER_HEADERS, "timeout": 30}
        if USING_CURL_CFFI:
            kwargs["impersonate"] = "chrome120"
        resp = session.get(API_LIST_URL, **kwargs)
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("CRSReports", [])
        items.extend(batch)
        if not data.get("pagination", {}).get("next"):
            break
    else:
        print(
            f"[warn] hit API_MAX_PAGES={API_MAX_PAGES} pages; more results may exist",
            file=sys.stderr,
        )
    return items


# ---------------------------------------------------------------------------
# API detail lookup
# ---------------------------------------------------------------------------


def get_report_details(session, report_id: str, api_key: str) -> dict | None:
    """Fetch full report metadata from the Congress.gov API.

    Returns a dict with id, title, url, authors (joined), topics (joined),
    pdf_url. Returns None if the API call failed or the report has no
    PDF URL. PDF URL comes from the `formats` array, which gives the
    current versioned URL (e.g. IF13131.5.pdf) — avoids version guessing.
    """
    url = API_DETAIL_URL.format(report_id=report_id)
    params = {"api_key": api_key, "format": "json"}

    kwargs = {"params": params, "timeout": 30}
    if USING_CURL_CFFI:
        kwargs["impersonate"] = "chrome120"

    resp = session.get(url, **kwargs)
    if resp.status_code != 200:
        print(
            f"[warn] API returned {resp.status_code} for {report_id}",
            file=sys.stderr,
        )
        return None

    data = resp.json()
    report = data.get("CRSReport") or data.get("crsReport") or {}

    pdf_url = None
    for fmt in report.get("formats", []):
        if fmt.get("format", "").upper() == "PDF":
            pdf_url = fmt.get("url")
            break
    if not pdf_url:
        return None

    return {
        "id": report_id,
        "title": report.get("title", ""),
        "url": report.get("url", ""),
        "authors": "; ".join(a.get("author", "") for a in report.get("authors", [])),
        "topics": "; ".join(t.get("topic", "") for t in report.get("topics", [])),
        "pdf_url": pdf_url,
    }


# ---------------------------------------------------------------------------
# PDF download
# ---------------------------------------------------------------------------


def download_pdf(session, pdf_url: str, dest_path: Path) -> bool:
    """Download a PDF to dest_path. Returns True on success."""
    kwargs = {"headers": BROWSER_HEADERS, "timeout": 60}
    if USING_CURL_CFFI:
        kwargs["impersonate"] = "chrome120"

    resp = session.get(pdf_url, **kwargs)
    if resp.status_code != 200:
        print(
            f"[warn] PDF download failed ({resp.status_code}): {pdf_url}",
            file=sys.stderr,
        )
        return False

    # Sanity check: PDFs start with %PDF-
    if not resp.content.startswith(b"%PDF-"):
        print(
            f"[warn] Response doesn't look like a PDF: {pdf_url} "
            f"(first bytes: {resp.content[:20]!r})",
            file=sys.stderr,
        )
        return False

    dest_path.write_bytes(resp.content)
    return True


# ---------------------------------------------------------------------------
# Metadata CSV (append-only audit log)
# ---------------------------------------------------------------------------


METADATA_HEADERS = [
    "id", "title", "url", "publication_date",
    "authors", "topics", "pdf_path", "last_update_date", "downloaded_at",
]


def append_metadata(metadata_csv: Path, meta: dict) -> None:
    """Append a row to the user-facing metadata.csv."""
    is_new = not metadata_csv.exists()
    with metadata_csv.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=METADATA_HEADERS)
        if is_new:
            writer.writeheader()
        writer.writerow({k: meta.get(k, "") for k in METADATA_HEADERS})


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------


def parse_date(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y-%m-%d").date()


def main():
    parser = argparse.ArgumentParser(
        description="Fetch newly published CRS reports from Congress.gov."
    )
    parser.add_argument(
        "--start-date",
        type=parse_date,
        help="Start of publication date range (YYYY-MM-DD). Default: yesterday.",
    )
    parser.add_argument(
        "--end-date",
        type=parse_date,
        help="End of publication date range (YYYY-MM-DD). Default: same as start.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./crs-tracker-data"),
        help=(
            "Shortcut: sets db/metadata/pdf/archive/summary paths to "
            "subpaths of this dir, unless individually overridden below."
        ),
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="SQLite dedup DB. Default: <output-dir>/crs.db",
    )
    parser.add_argument(
        "--metadata-path",
        type=Path,
        default=None,
        help="Append-only metadata CSV. Default: <output-dir>/metadata.csv",
    )
    parser.add_argument(
        "--pdf-dir",
        type=Path,
        default=None,
        help="Directory for downloaded PDFs. Default: <output-dir>/pdfs",
    )
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=None,
        help="Directory for raw API-response JSON archives. Default: <output-dir>/archive",
    )
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=None,
        help=(
            "JSON summary of this run (used by CI to build commit messages). "
            "Default: <output-dir>/last-run-summary.json"
        ),
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("CONGRESS_API_KEY"),
        help="Congress.gov API key. Defaults to $CONGRESS_API_KEY.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Query the API and show what would be downloaded, but don't fetch PDFs.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download PDFs even if already in the DB.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.5,
        help="Seconds to sleep between API/PDF requests (default 0.5).",
    )
    args = parser.parse_args()

    # Default to yesterday if no dates given
    yesterday = dt.date.today() - dt.timedelta(days=1)
    start = args.start_date or yesterday
    end = args.end_date or start

    if end < start:
        print("error: --end-date is before --start-date", file=sys.stderr)
        sys.exit(2)

    if not args.api_key:
        print(
            "error: no API key. Set $CONGRESS_API_KEY or pass --api-key. "
            "Get a free key at https://api.congress.gov/sign-up/",
            file=sys.stderr,
        )
        sys.exit(2)

    # Resolve output paths: per-flag overrides win, else <output-dir>/<default>.
    db_path = args.db_path or (args.output_dir / "crs.db")
    metadata_csv = args.metadata_path or (args.output_dir / "metadata.csv")
    pdfs_dir = args.pdf_dir or (args.output_dir / "pdfs")
    archive_dir = args.archive_dir or (args.output_dir / "archive")
    summary_path = args.summary_path or (args.output_dir / "last-run-summary.json")

    for d in (db_path.parent, metadata_csv.parent, pdfs_dir,
              archive_dir, summary_path.parent):
        d.mkdir(parents=True, exist_ok=True)

    summary = {
        "date_range": [start.isoformat(), end.isoformat()],
        "found": 0,
        "new": 0,
        "succeeded": [],
        "failed": [],
    }

    def write_summary():
        summary_path.write_text(json.dumps(summary, indent=2))

    if not USING_CURL_CFFI:
        print(
            "[note] curl_cffi not installed — falling back to requests. "
            "Cloudflare may block api.congress.gov calls without it: "
            "pip install curl_cffi",
            file=sys.stderr,
        )

    # Query the API for reports whose updateDate falls in the window
    session = http.Session()
    try:
        listing = fetch_api_listing(session, args.api_key, start, end)
    except Exception as e:
        print(f"error: API listing failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Archive the raw listing response for audit
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    archive_path = archive_dir / f"{stamp}_{start}_{end}_listing.json"
    archive_path.write_text(json.dumps(listing, indent=2), encoding="utf-8")
    print(f"[ok] archived API listing ({len(listing)} items) to {archive_path}", file=sys.stderr)

    summary["found"] = len(listing)
    print(f"[ok] {len(listing)} reports in updateDate window", file=sys.stderr)

    if not listing:
        print(
            f"[done] No activity for {start} to {end}. "
            "This is normal for weekends/holidays.",
            file=sys.stderr,
        )
        write_summary()
        return

    # Decide which reports need a detail call: those unseen OR whose
    # updateDate moved since we last recorded them. Reports with the same
    # updateDate as stored are skipped entirely (no detail call, no download).
    conn = init_db(db_path)
    to_check = []
    for item in listing:
        rid = item.get("id")
        if not rid:
            continue
        upd = item.get("updateDate", "") or ""
        stored = get_stored_state(conn, rid)
        if args.force or stored is None or (stored[0] or "") != upd:
            to_check.append(item)
    summary["new"] = len(to_check)
    print(
        f"[ok] {len(to_check)} reports changed since last run (of {len(listing)} found)",
        file=sys.stderr,
    )

    if args.dry_run:
        print("\n--- DRY RUN: would check/download these reports ---")
        for item in to_check:
            print(f"  {item.get('id')} (updateDate: {item.get('updateDate', '?')})")
        write_summary()
        return

    # Per-report detail + PDF download
    succeeded = []
    failed = []

    for item in to_check:
        rid = item.get("id")
        upd = item.get("updateDate", "") or ""
        print(f"[fetch] {rid}: detail lookup...", file=sys.stderr)

        details = get_report_details(session, rid, args.api_key)
        if not details:
            failed.append({"id": rid, "reason": "no details from API"})
            time.sleep(args.sleep)
            continue

        # If we've already stored this exact PDF URL, the update was just a
        # re-index (no new version). Bump last_update_date and skip download.
        stored = get_stored_state(conn, rid)
        if stored and stored[1] == details["pdf_url"] and not args.force:
            touch_update_date(conn, rid, upd)
            print(f"[skip] {rid}: same PDF version ({details['pdf_url'].rsplit('/', 1)[-1]})", file=sys.stderr)
            time.sleep(args.sleep)
            continue

        pdf_path = pdfs_dir / f"{rid}.pdf"
        print(f"[fetch] {rid}: downloading PDF...", file=sys.stderr)
        ok = download_pdf(session, details["pdf_url"], pdf_path)
        if not ok:
            failed.append({"id": rid, "reason": f"download failed: {details['pdf_url']}"})
            time.sleep(args.sleep)
            continue

        # publication_date: the target window's start date, which matches the
        # "we observed this as new on day X" semantics the skill commits to.
        meta = {
            "id": rid,
            "title": details["title"],
            "url": details["url"],
            "publication_date": start.isoformat(),
            "authors": details["authors"],
            "topics": details["topics"],
            "pdf_path": str(pdf_path),
            "pdf_url": details["pdf_url"],
            "last_update_date": upd,
            "downloaded_at": dt.datetime.now().isoformat(),
        }
        record_report(conn, meta)
        append_metadata(metadata_csv, meta)
        succeeded.append({"id": rid, "title": details["title"]})
        print(f"[ok] {rid} -> {pdf_path}", file=sys.stderr)
        time.sleep(args.sleep)

    # Summary
    print()
    print(f"=== Summary ===")
    print(f"  date range: {start} to {end}")
    print(f"  reports in window: {len(listing)}")
    print(f"  changed since last run: {len(to_check)}")
    print(f"  new/republished downloaded: {len(succeeded)}")
    if failed:
        print(f"  failed: {len(failed)}")
        for f in failed:
            print(f"    - {f['id']}: {f['reason']}")
    print(f"  db: {db_path.resolve()}")
    print(f"  pdfs: {pdfs_dir.resolve()}")
    summary["succeeded"] = succeeded
    summary["failed"] = failed
    write_summary()
    print(f"  summary: {summary_path.resolve()}")


if __name__ == "__main__":
    main()
