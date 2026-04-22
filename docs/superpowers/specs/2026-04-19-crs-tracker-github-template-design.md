# CRS Tracker — GitHub Template Repo Design

**Date:** 2026-04-19
**Author:** via brainstorming session with Rebecca Fordon
**Status:** design approved, pending spec review

## Goal

Package the existing `crs-tracker` skill (Python script that fetches newly published Congressional Research Service reports) into a **GitHub template repository** that a non-developer Claude Code user can adopt with minimal setup. The repo should:

1. Run a daily scheduled job on GitHub-hosted runners to fetch the previous day's CRS reports.
2. Keep the git repo small by storing PDFs as GitHub Actions artifacts, not in git.
3. Commit the SQLite dedup DB and metadata CSV back to the repo so there's a permanent, queryable record of what was published.
4. Also support running locally via CLI, and optionally as a Claude Code skill.

## Target user

A non-developer who has Claude Code but is not deeply familiar with it, and wants a "set and forget" daily ingestion of CRS reports. Comfort level:

- Can follow README steps.
- Can click "Use this template" on GitHub.
- Can paste a secret into repo settings.
- Should NOT be expected to write Python, edit YAML, or debug cron syntax.

## Non-goals

- Live notifications (Slack/email/Discord). v1 surfaces activity via the Actions tab and commit feed only. Can be added later.
- Long-term PDF archival. Artifacts expire at 15 days — the repo owner is expected to download what they want to keep.
- Full-text search over PDFs, or extraction of report content. Out of scope.
- A web UI. Out of scope.
- Preserving the existing Claude.ai–sandbox limitations doc — keep it, but don't elaborate.
- Handling CRS web sites other than Congress.gov.

## User flow

**One-time setup (target: <5 minutes):**

1. Click "Use this template" on the GitHub repo page → create new repo in her account (public recommended; private also fine).
2. Get a free Congress.gov API key from `https://api.congress.gov/sign-up/`.
3. In the new repo, go to Settings → Secrets and variables → Actions → New repository secret. Name: `CONGRESS_API_KEY`. Value: her key.
4. (Optional) Go to Actions tab and trigger the workflow once manually (`workflow_dispatch`) to confirm setup works.

**Ongoing:** nothing. Workflow fires daily at 07:00 UTC.

**When she wants PDFs:** Actions tab → click a successful run → download the `crs-pdfs-YYYY-MM-DD` artifact.

**When she wants to query metadata:** clone the repo or browse `data/metadata.csv` on GitHub directly.

## Repo layout

```
crs-tracker/
├── .github/
│   └── workflows/
│       └── daily.yml
├── scripts/
│   └── fetch_crs.py         # existing script, minor edits (see below)
├── data/                    # committed; small files only
│   ├── crs.db               # SQLite dedup state
│   └── metadata.csv         # append-only audit log
├── references/
│   └── troubleshooting.md   # inherited from existing skill
├── SKILL.md                 # for users who want the Claude Code skill path
├── README.md                # primary user-facing docs
├── requirements.txt         # pins curl_cffi
├── .gitignore               # excludes pdfs/, csv-archive/
└── LICENSE                  # MIT or similar (TBD with user)
```

**Why `data/` is committed:**
`crs.db` and `metadata.csv` are both tiny (KB-scale per report, so MB-scale even after years of daily runs). Committing them:
- Solves cross-run persistence of the dedup state for free — no artifact round-trip needed.
- Gives the user a permanent, grep-able, GitHub-browseable record of publication history.
- Keeps the workflow simple (no "restore state from last artifact" step).

## GitHub Actions workflow (`.github/workflows/daily.yml`)

**Schedule:** `cron: "0 7 * * *"` — 07:00 UTC daily. This lands at 03:00 EST in winter and 04:00 EDT in summer. GitHub cron does not observe DST; document this clearly in the README. User stated "around 3am ET" is the intent and a 1-hour seasonal drift is acceptable.

**Triggers:**
- `schedule:` for the daily run
- `workflow_dispatch:` for manual triggers with an optional date-range input

**Permissions:** `contents: write` — needed to commit the DB back.

**Job steps:**

1. `actions/checkout@v4` — get the current `data/crs.db`.
2. `actions/setup-python@v5` with `python-version: "3.11"`.
3. `pip install -r requirements.txt` (just `curl_cffi`).
4. Run `python scripts/fetch_crs.py --db-path data/crs.db --metadata-path data/metadata.csv --pdf-dir ./pdfs --csv-archive-dir ./csv-archive`. Target date defaults to yesterday.
5. `actions/upload-artifact@v4`:
   - name: `crs-pdfs-$(date -u +%Y-%m-%d)`
   - path: `./pdfs`
   - retention-days: `15`
   - if-no-files-found: `ignore` (weekends/holidays produce no PDFs)
6. Commit + push `data/crs.db` and `data/metadata.csv` if changed. Commit message format:
   ```
   Add N new CRS reports (YYYY-MM-DD)

   IF13131: Title of report 1
   IN12516: Title of report 2
   ...
   ```
   Skip the commit step entirely if no files changed. Use `github-actions[bot]` as the author.

## Script changes

Minimal edits to `scripts/fetch_crs.py`:

1. **Split the four output paths into separate flags** so the workflow can point them at different places (`data/` for committed files, `./pdfs` for the artifact):
   - `--db-path` (default: `./crs-tracker-data/crs.db`)
   - `--metadata-path` (default: `./crs-tracker-data/metadata.csv`)
   - `--pdf-dir` (default: `./crs-tracker-data/pdfs`)
   - `--csv-archive-dir` (default: `./crs-tracker-data/csv-archive`)

   Keep the existing `--output-dir` flag as a shortcut that sets all four to sensible defaults underneath it. This preserves the current local-CLI UX.

2. **Emit a structured summary at the end of a run** so the workflow can build a commit message without re-parsing logs. Write `data/last-run-summary.json` (gitignored) with:
   ```json
   {
     "date_range": ["2026-04-18", "2026-04-18"],
     "in_csv": 12,
     "new": 5,
     "succeeded": [{"id": "IF13131", "title": "..."}],
     "failed": [{"id": "IN12516", "reason": "404 on PDF"}]
   }
   ```
   The workflow reads this file, formats the commit message, and commits `crs.db` + `metadata.csv`.

3. **Exit code discipline:** exit 0 for "ran fine, including zero-result weekends." Non-zero only for genuine errors (API key missing, CSV fetch totally failed, schema broken). This keeps the Actions tab green on weekends.

No other changes to existing logic. The core fetch / diff / download path is unchanged.

## README structure

Order matters — put the most-common path first.

1. **What this does** — 2-sentence hook.
2. **Get started in 5 minutes** — the GitHub Actions path, numbered steps matching the user flow above. This is the 90% case.
3. **How to get PDFs** — artifact download instructions with a screenshot/describe flow.
4. **Run locally** — `pip install -r requirements.txt`, set env var, `python scripts/fetch_crs.py`. For occasional "grab the last two weeks right now" use.
5. **Use as a Claude Code skill** — one paragraph: "copy `SKILL.md` and `scripts/` to `~/.claude/skills/crs-tracker/`." For users who want the conversational path.
6. **Schedule and timezones** — explain the 07:00 UTC cron and the DST drift. How to change the schedule.
7. **Data layout** — what's in `data/`, what's in artifacts, what's in `pdfs/` locally.
8. **Troubleshooting** — short version; link to `references/troubleshooting.md` for the long version.
9. **Quota & cost notes** — public repo = unlimited Actions minutes; private = 2,000/month (this job uses ~60); artifacts cap at 500 MB free, this job uses ~75 MB steady-state at 15-day retention.

## Error handling & edge cases

- **Weekends / federal holidays:** CSV legitimately returns 0 rows. Script prints a message, exits 0, workflow commits nothing, no artifact uploaded.
- **API key missing in secrets:** workflow fails at step 4 with a clear error. Document this in README troubleshooting.
- **Cloudflare blocks CSV fetch:** `curl_cffi` should handle this; if it escalates, document the Playwright fallback in `references/troubleshooting.md` (inherits from existing skill docs).
- **A single PDF fails (403/404):** script records it in the `failed` list and continues. Other PDFs in the same run still succeed and still get committed to the DB. Failed IDs appear in the commit message and the summary JSON.
- **DB merge conflicts:** shouldn't happen in the scheduled path (only one writer), but if the user manually commits to `main` between runs, the workflow's push could conflict. Mitigation: workflow does a `git pull --rebase` before pushing, with a single retry on conflict. If still conflicting after one retry, fail loudly.
- **Forked repo not receiving upstream fixes:** acceptable. Template repos intentionally don't track upstream. If there's a bug in the script later, the user re-copies `scripts/fetch_crs.py` or does a manual pull.

## Testing plan

This is not a tested codebase today. Don't over-invest — the v1 testing story is:

1. **Smoke test the script locally** with a known date range (e.g., `--start-date 2026-04-17 --end-date 2026-04-17`) and confirm PDFs download + DB updates.
2. **Dry-run test** with `--dry-run` to confirm CSV parsing works end-to-end without downloads.
3. **Workflow test** by manually dispatching the GitHub Actions workflow once the template is set up, with a backfill date.
4. **Empty-day test** by running against a known Saturday; expect exit 0 with no commit.

No unit tests in v1. If the tool matures, add pytest coverage for `parse_csv`, `get_pdf_url`, and the summary-JSON emitter.

## Open questions (to resolve during implementation)

1. **License:** MIT, Apache-2, or Unlicense? Default to MIT unless user objects.
2. **Commit author identity:** `github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>` is standard, but the user may prefer a named bot account. Default to the standard bot.
3. **Artifact retention days:** 15 (confirmed).

## Working-directory notes

The current `C:\Users\Rebecca Fordon\Projects\crs-downloader\` directory holds:
- `crs-tracker.skill` — the original packaged skill (ZIP archive)
- `extracted/` — unzipped contents, already installed to `~/.claude/skills/crs-tracker/`

Both are scratch. The new repo will be built fresh from a clean subdirectory (or separate path, TBD during planning), copying in `fetch_crs.py` and `troubleshooting.md` from the extracted skill but not carrying over the `.skill` archive or the extraction artifacts.
