---
name: crs-tracker
description: Download newly published Congressional Research Service (CRS) reports from Congress.gov by publication date. Use this skill whenever the user wants to pull, monitor, download, sync, archive, or fetch CRS reports — including requests like "what CRS reports came out yesterday", "download today's new CRS reports", "check for new CRS publications", "get CRS reports from [date]", or anything about ad-hoc CRS report collection. Also trigger this if the user mentions Congressional Research Service publications by name. This skill runs a Python script that queries the Congress.gov API for reports updated in a target date window, compares each report's current PDF URL against a local SQLite database, and downloads any reports that are new or have a newer PDF version. Requires network access to api.congress.gov (works in Claude Code, local machines, cron jobs, and GitHub Actions — does NOT work inside Claude.ai's web sandbox, which blocks that domain).
---

# CRS Tracker

Pulls CRS reports published on a given date (or date range) from Congress.gov, diffs against a local store of already-seen reports, and downloads the PDFs of any new ones along with their metadata.

> **Note:** This skill is the conversational counterpart to the [crs-tracker template repo](https://github.com/) — same script, same data layout. If you want unattended daily pulls, set up the template repo's GitHub Actions workflow instead (see its README). Use this skill for ad-hoc and exploratory work.

## Why this exists

The Congress.gov API's `publishDate` field is the original v1 publication date — it doesn't move when a report is republished. So an API query filtered on `publishDate` misses republished reports, which are a meaningful slice of daily CRS activity.

The web search page filters correctly but is now behind a Cloudflare JS challenge from cloud/automated IPs (GitHub Actions, etc.), so it's not reliable for unattended use.

This skill uses the API's `updateDate` filter instead — it returns every report that was touched server-side in the window — then de-duplicates against a local DB by comparing PDF URLs. If the stored URL (e.g. `IF12826.3.pdf`) differs from the current one (`IF12826.4.pdf`), it's a republication and we download; if they match, it was just a re-index and we skip. This catches both new reports and republications without needing a browser or HTML scraping.

## Where this runs

Needs network access to `api.congress.gov`. Runs in:

- **Claude Code** on a developer machine
- **Cron jobs / systemd timers / launchd** on a server or laptop
- **GitHub Actions** (use the template repo for a turnkey setup)

Does **not** run inside Claude.ai's web sandbox — that domain is blocked.

## Inputs

1. **A Congress.gov API key.** Free from https://api.congress.gov/sign-up/. Provide via `CONGRESS_API_KEY` env var or `--api-key` flag.
2. **A target date (or date range).** Defaults to yesterday. Format: `YYYY-MM-DD`.
3. **A workspace directory.** Defaults to `./crs-tracker-data/`. Override with `--output-dir`, or set individual paths with `--db-path`, `--metadata-path`, `--pdf-dir`, `--archive-dir`, `--summary-path`.

## Workflow

### Step 1: Confirm date and output directory

If the user specified dates or paths, use those. Otherwise default to yesterday and `./crs-tracker-data/`.

### Step 2: Check for the API key

Look for `CONGRESS_API_KEY` in the env. If missing, ask the user to set it or pass `--api-key`. Mention the free signup URL.

### Step 3: Ensure `curl_cffi` is installed

Bypasses Cloudflare's TLS fingerprinting. If not installed:
```bash
pip install curl_cffi
```
The script auto-detects it; falls back to `requests` with a browser UA otherwise (Cloudflare may block).

### Step 4: Run the script

```bash
python scripts/fetch_crs.py \
  --start-date 2026-04-17 \
  --end-date 2026-04-18 \
  --output-dir ./crs-tracker-data \
  --api-key "$CONGRESS_API_KEY"
```

Flags:
- `--start-date` / `--end-date` — inclusive bounds; omit for yesterday.
- `--output-dir` — shortcut for setting db/metadata/pdf/archive/summary paths under one directory.
- `--db-path`, `--metadata-path`, `--pdf-dir`, `--archive-dir`, `--summary-path` — per-output overrides.
- `--api-key` — overrides `CONGRESS_API_KEY`.
- `--dry-run` — query the API and show what would be checked/downloaded, but skip detail lookups and PDF downloads.
- `--force` — re-download PDFs already in the DB.
- `--sleep` — seconds between API/PDF requests (default 0.5).

### Step 5: Summarize results

After the script finishes:
- Reports found on the search page for the target range
- New (not previously in the DB)
- File locations
- Any failures (with IDs and reasons; check `last-run-summary.json` for the structured record)

## Output layout

```
crs-tracker-data/
├── crs.db                          # SQLite store of all seen reports
├── metadata.csv                    # Append-only download log
├── last-run-summary.json           # Structured summary of the most recent run
├── archive/
│   └── 2026-04-19_143015_..._listing.json   # Raw API listing response
└── pdfs/
    ├── IF13131.pdf
    ├── IN12516.pdf
    └── ...
```

SQLite schema: single `reports` table with columns `id`, `title`, `url`, `publication_date`, `authors`, `topics`, `pdf_path`, `pdf_url`, `last_update_date`, `downloaded_at`. Queryable directly with `sqlite3 crs.db`.

## Scheduling

For unattended daily runs, prefer the [crs-tracker GitHub template repo](https://github.com/) — it bundles a daily Actions workflow, secret management, and PDF-as-artifact storage. For local cron, see `references/troubleshooting.md`.

## Troubleshooting

See `references/troubleshooting.md`. Most common:
1. **Cloudflare blocks the API** → install `curl_cffi`
2. **API returns 429** → wait an hour or increase `--sleep`
3. **A specific PDF returns 403/404** → usually self-resolves on next day's run
4. **API schema changed** → inspect the archived listing JSON in `archive/`; update field accesses in `fetch_api_listing()` / `get_report_details()`
