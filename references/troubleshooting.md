# Troubleshooting

Common failure modes and how to work around them.

## Cloudflare blocks the API

**Symptom:** `error: API listing failed:` followed by an HTTP 403, or API calls returning HTTP 403 with Cloudflare's error code 1010 in the body.

**Diagnosis:** Cloudflare's bot-detection in front of `api.congress.gov` is flagging the request based on TLS fingerprint (JA3), missing/suspicious headers, or request timing. Plain `urllib` / `requests` is reliably blocked.

**Fix:** Install `curl_cffi`, which impersonates a real Chrome TLS handshake. This passes Cloudflare's JA3 check.

```bash
pip install curl_cffi
```

Re-run the script. It auto-detects `curl_cffi` and uses it. `requirements.txt` pins it; the GitHub Actions workflow installs it automatically.

> **Note on the search page:** Earlier versions of this script scraped the `/quick-search/crs-products` HTML page to get the report ID list. That endpoint is now JS-challenged from cloud/AWS IPs (GitHub Actions runners hit it immediately), so the current script uses the API's `updateDate` window instead — this works from GitHub Actions. If the API ever gets as aggressively challenged, the fallback is Playwright; leaving a sketch here for that future:
>
> ```bash
> pip install playwright
> playwright install chromium
> ```
> ```python
> from playwright.sync_api import sync_playwright
> with sync_playwright() as p:
>     browser = p.chromium.launch(headless=True)
>     page = browser.new_page()
>     page.goto(search_url)
>     page.wait_for_load_state("networkidle")
>     html = page.content()
>     browser.close()
> ```

## API returns 429 "Too Many Requests"

**Symptom:** `[warn] API returned 429 for IF12345`.

**Diagnosis:** Hit api.data.gov's rate limit. Free keys get 1,000 requests/hour.

**Fix:**
- Wait an hour and retry.
- If running across many dates at once, increase `--sleep` to 2-4 seconds.
- For heavy usage, email api@data.gov to request a higher limit.

## A specific PDF returns 403 or 404

**Symptom:** `[warn] PDF download failed (403): https://www.congress.gov/crs_external_products/...`

**Diagnosis:** One of:
1. CRS updated the report's version number between the API call and download — the version in the URL is stale.
2. The report was pulled or moved.
3. Cloudflare is rate-limiting PDF downloads specifically.

**Fix:** Re-run the script the next day. The API's `formats` URL will have the updated version. If it persistently fails on the same report for a week, check `https://www.congress.gov/crs-product/{id}` in a browser to see if the report still exists.

## API response schema changed

**Symptom:** Script reports `0 reports in updateDate window` for a busy weekday, or `no details from API` for reports that clearly exist, or crashes with a `KeyError` in `get_report_details()`.

**Diagnosis:** Congress.gov changed field names or response shape. The script depends on the list endpoint returning `{"CRSReports": [{"id": ..., "updateDate": ...}, ...]}` and the detail endpoint returning `{"CRSReport": {..., "formats": [{"format": "PDF", "url": ...}]}}`.

**Fix:** Open the latest archived listing JSON in `archive/` and compare against the field accesses in `fetch_api_listing()` (for list shape) and `get_report_details()` (for detail shape). Update accordingly.

## "No reports found in date range"

**Expected behavior, not a bug.** CRS doesn't publish on weekends or federal holidays. A run targeting Saturday/Sunday/holiday will legitimately return zero.

To suppress these "nothing to do" runs, restrict the scheduler:

```bash
# Tuesday through Saturday (covering Mon-Fri publications).
# Day 0 = Sunday, so this skips Sun (0) and Mon (1).
0 6 * * 2-6 /path/to/python /path/to/fetch_crs.py
```

## Duplicate downloads

**Symptom:** Same report appears in multiple runs, downloaded each time.

**Diagnosis:** The SQLite DB got reset, `--force` is set, or the report ID is genuinely re-issued (rare — CRS renumbering).

**Fix:** Check that `crs-tracker-data/crs.db` exists and survives between runs. If running in a container or ephemeral environment, make sure the output directory is a mounted volume.

## Running in GitHub Actions

See [`.github/workflows/daily.yml`](../.github/workflows/daily.yml) in this repo for the complete, up-to-date workflow: daily cron, manual dispatch with optional date inputs, PDF artifact upload, and commit-back of the dedup state.
