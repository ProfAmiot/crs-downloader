# CRS Tracker

A daily, automated pull of newly published [Congressional Research Service](https://crsreports.congress.gov/) (CRS) reports from Congress.gov, packaged as a GitHub template repo. Click "Use this template," paste one secret, and you get a daily commit log of new CRS reports plus downloadable PDF bundles.

## Get started in 5 minutes

1. **Create your repo from this template.** Click the green **Use this template** button at the top of the GitHub page → **Create a new repository**. Public is recommended (free unlimited Actions minutes); private also works.
2. **Get a free Congress.gov API key.** Sign up at https://api.congress.gov/sign-up/. The key arrives by email in a minute or two.
3. **Add the key to your new repo.** Go to **Settings → Secrets and variables → Actions → New repository secret**.
   - **Name:** `CONGRESS_API_KEY`
   - **Value:** the key from step 2
4. **(Optional but recommended) Run the workflow once manually.** Go to **Actions → Daily CRS Pull → Run workflow → Run workflow**. This confirms your secret is set up correctly. The first run takes 1–2 minutes.

That's it. Every morning at 07:00 UTC (~3 AM ET) the workflow fires, fetches yesterday's new CRS reports, and commits the metadata. PDFs are uploaded as an artifact attached to the run.

## How to get the PDFs

PDFs are stored as **GitHub Actions artifacts** (15-day retention) rather than committed to git, which keeps the repo small.

1. Go to the **Actions** tab in your repo.
2. Click on a successful run (look for the green checkmark).
3. Scroll to the **Artifacts** section at the bottom.
4. Click `crs-pdfs-<UTC-date>` to download a ZIP of all PDFs from that run.

If you want to keep PDFs longer than 15 days, download them and store them yourself (e.g., in cloud storage or a separate archive repo).

## Run locally

For ad-hoc backfills or quick testing, you can run the script directly:

```bash
pip install -r requirements.txt
export CONGRESS_API_KEY=your-key-here
python scripts/fetch_crs.py                                  # yesterday
python scripts/fetch_crs.py --start-date 2026-04-01 --end-date 2026-04-17
python scripts/fetch_crs.py --dry-run                        # preview, no downloads
```

By default this writes to `./crs-tracker-data/` (gitignored). All output paths can be overridden individually — see `python scripts/fetch_crs.py --help`.

## Use as a Claude Code skill

If you want to invoke this conversationally from Claude Code instead of (or in addition to) running it on a schedule, copy `SKILL.md` and `scripts/` into a folder under your skills directory:

```bash
mkdir -p ~/.claude/skills/crs-tracker
cp SKILL.md ~/.claude/skills/crs-tracker/
cp -r scripts ~/.claude/skills/crs-tracker/
cp -r references ~/.claude/skills/crs-tracker/
```

Then in Claude Code: "download today's new CRS reports" or "what came out yesterday from CRS." See `SKILL.md` for the full skill metadata.

## Schedule and timezones

The workflow runs at **07:00 UTC daily**. That's:

- **03:00 EST** in winter (Nov–Mar)
- **04:00 EDT** in summer (Mar–Nov)

GitHub Actions cron does **not** observe Daylight Saving Time — the schedule drifts by one hour seasonally. This is acceptable for a "around 3 AM" job. If you need a fixed local time, update the `cron:` line in `.github/workflows/daily.yml` (and remember to change it twice a year).

To change the schedule, edit:
```yaml
on:
  schedule:
    - cron: "0 7 * * *"   # min hour day-of-month month day-of-week
```

## Data layout

| Location | What | Committed? |
|----------|------|------------|
| `data/crs.db` | SQLite dedup state — every report ID we've ever seen | yes |
| `data/metadata.csv` | Append-only log of all downloads (id, title, url, pubdate, authors, topics, pdf_path, last_update_date, downloaded_at) | yes |
| `data/last-run-summary.json` | Per-run summary used to build commit messages | no (gitignored) |
| Artifact `crs-pdfs-<UTC-date>` | All PDFs from a single run | no (15-day artifact) |
| `./pdfs/` (local only) | PDFs when running locally | no (gitignored) |
| `./archive/` (local only) | Raw Congress.gov API listing responses (JSON), for audit | no (gitignored) |

The `data/` directory stays small (KB per report → MB after years), so committing it is cheap and gives you a permanent, grep-able publication history right in GitHub.

## Troubleshooting

Most common issues:

- **Workflow fails on "Verify API key secret":** you didn't add `CONGRESS_API_KEY`. See setup step 3.
- **Workflow runs but commits nothing:** Friday/weekend/holiday — CRS doesn't publish. Check the run logs; you'll see `[done] No reports found ... This is normal for weekends/holidays.`
- **A PDF download failed (404):** CRS sometimes updates a report's version between when the API returned the URL and when we fetched it. The next day's run usually picks it up.
- **API blocked by Cloudflare locally:** install `curl_cffi` (it's already in `requirements.txt`); the workflow has it preinstalled.

For more, see [`references/troubleshooting.md`](references/troubleshooting.md).

## Quota & cost notes

- **GitHub Actions minutes:** Public repos = unlimited. Private repos = 2,000 free minutes/month; this workflow uses ~60/month (1–2 minutes per run × ~30 runs).
- **GitHub artifact storage:** 500 MB free across all artifacts. At ~75 MB steady-state for 15-day PDF retention, well under the cap.
- **Congress.gov API:** Free key allows 1,000 requests/hour. Each workflow run makes 1 listing call plus 1 detail call per changed report — typically 5–15 requests/day at steady state. The first run after setup may pull ~40-50 reports as it catches up on recent activity, then the daily count drops to whatever CRS actually publishes/republishes.

## License

MIT — see [`LICENSE`](LICENSE).
