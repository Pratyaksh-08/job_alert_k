# Daily US New-Grad Tech Jobs Digest — MSCS 2026

A deterministic GitHub Actions workflow for a 2026 Santa Clara University MSCS graduate targeting **US SWE/SDE, AI/ML, Data Engineering and Data Science** jobs. Broad discovery, transparent fit tags, and one daily email with a complete CSV attachment.

## Quick setup

1. Create a **private** GitHub repository, e.g. `newgrad-job-digest`, and upload the contents of this folder **including** `.github/workflows/daily_jobs.yml`.
2. In the repository, open **Settings → Secrets and variables → Actions → New repository secret** and add:
   - `SMTP_USER`: Gmail account that will send the digest.
   - `SMTP_PASSWORD`: the **16-character Gmail app password**, *not* your main Gmail password. Requires Google 2-Step Verification; app passwords may be unavailable for managed/Advanced Protection accounts.
   - `EMAIL_TO`: the recipient address (can be same Gmail address).
3. Gmail app-password guidance: https://support.google.com/accounts/answer/185833 . Safer production alternatives include Gmail OAuth2 or a transactional mail service; this starter uses SMTP for simplicity.
4. Under **Settings → Actions → General → Workflow permissions**, select **Read and write permissions** so the script can commit `state/seen.json`. Do not grant this repository to untrusted contributors.
5. Open **Actions → Daily New-Grad Tech Jobs Email → Run workflow** to test. Check the run logs; confirm email arrived, including the CSV. Inspect the `jobs-report` artifact in Actions if needed.
6. Workflow then runs **8:17 AM Pacific every day**, following US DST. Change the `cron` and `timezone` in `.github/workflows/daily_jobs.yml` if preferred. GitHub schedule jobs can be delayed and are not hard real-time.

## What it does

- Pulls from **10 public GitHub feeds** and **6 employer ATS boards** (Greenhouse and Ashby), as listed in `config/sources.json`. The source list is editable and individual feeds might become unavailable or change format; failures are shown in the email.
- Extracts structured postings (JSON) and postings from Markdown tables; keeps only SWE/SDE, AI/ML, data engineering, data science, plus related technical engineering titles.
- Defaults to US or location-unknown jobs, and ranks Bay Area roles slightly higher. Non-US jobs with an explicit location are generally excluded.
- **Strong match** = title evidence of early career/new grad with no explicit mismatch; **Review** = plausible but unclear / targeting 2027 / 2 years; **Stretch** = 3–4 YOE; **Eligibility warning** = 5+ YOE or explicit citizenship/no-sponsorship concern. A strong title match does **not** mean you meet all requirements.
- Separately tags visa status from explicit information or source metadata. `H-1B history only` is **not** a current-role sponsor guarantee. Don't infer sponsorship from a company's name.
- Skips internships/co-ops requiring enrollment, obvious senior/staff positions, and unrelated functions. Company/role/location dedup is approximate. Always confirm employer job descriptions, graduation window, enrollment, and visa/OPT eligibility.
- Sends **one HTML email daily**. It shows up to `max_html_jobs` postings, ranked and grouped. **All new candidates** are included as a CSV attachment.
- `state/seen.json` prevents old jobs repeating in future emails. The first run shows postings discovered/posted within 21 days (plus up to 75 undated entries); subsequent emails contain jobs newly seen by *your* digest (not necessarily posted that day).
- Writes `out/jobs.csv`, `out/digest.html` and source-health details as 14-day GitHub Actions artifacts. The email sends regardless of whether there are new matches, unless every source fails.

## Customize

Edit `config/settings.json`:

| Parameter | Meaning |
|---|---|
| `graduate_year` | 2026 |
| `max_post_age_days` | Don't include listings older than 120 days when dated |
| `first_run_lookback_days` | First-email lookback (21 days) |
| `max_html_jobs` | HTML email preview limit (125); CSV still includes all new candidates |
| `max_very_senior_yoe` | Hard exclusion for 6+ YOE minimum; 3–5 YOE are flagged |
| `always_email_even_when_zero_new` | Send a zero-new digest too |

Edit `config/sources.json` to add or remove feeds.

Supported types:
- `json`: use a public raw GitHub JSON URL with nested lists of job postings.
- `markdown`: parse GitHub Markdown job listing tables (best-effort).
- `greenhouse`: specify `board` slug (`GET boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true`).
- `ashby`: specify `board` slug (`GET api.ashbyhq.com/posting-api/job-board/{board}`).
- `lever`: specify `board` slug (`GET api.lever.co/v0/postings/{board}?mode=json`).

**Limitations and caveats**
- The repository and its Markdown/JSON fields can change, especially community-driven trackers. Some sources may produce zero parsed jobs; inspect `sources_health.json` and the logs. A source marked successful is not necessarily delivering *complete* coverage. 2026-specific graduation requirements can be buried in job descriptions; title-only filtering is not fully reliable.
- No automatic application submission and no resume/PII should be stored in this repository.
- GitHub's free private-runner allowance is currently 2,000 minutes/month on Free accounts; review current GitHub billing. GitHub-hosted runner time is charged by minutes, not number of postings. External email/ATS rate limits are separate.
- With no internet access during construction, the adapters were validated using representative source-shaped unit tests, not an end-to-end live crawl. Run the workflow manually and inspect source health before relying on it.

## Local testing

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python job_digest.py --dry-run
```

For local email sending, set `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO` in the environment and run without `--dry-run`. Never commit passwords or `.env` files.
