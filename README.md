# Daily US Early-Career Tech Jobs Digest

A deterministic GitHub Actions workflow for discovering **US SWE/SDE, AI/ML, Data Engineering and Data Science** openings suitable for early-career and graduate candidates. Each daily run emails filtered roles from the previous calendar day only and attaches a two-sheet Excel workbook.

## Quick setup

1. Push this folder to the repository's default branch, including `.github/workflows/daily_jobs.yml`.
2. Add the three Actions secrets documented under **Enable daily email** below.
3. Open **Actions → Daily Job Digest Email → Run workflow** and confirm that the email and workbook arrive.
4. The workflow then runs automatically at **8:00 AM San Francisco time every day**, following US daylight-saving changes. GitHub schedule jobs can be delayed and are not hard real-time.

## What it does

- Pulls from **8 public GitHub feeds** and **4 official employer ATS boards** (Greenhouse and Ashby), as listed in `config/sources.json`.
- Extracts structured postings (JSON) and postings from Markdown tables; keeps only SWE/SDE, AI/ML, data engineering, data science, plus related technical engineering titles.
- Defaults to US or location-unknown jobs and ranks Bay Area roles slightly higher. Non-US jobs with an explicit location are generally excluded.
- **Strong match** = title evidence of early career/new grad with no explicit mismatch; **Review** = plausible but unclear / targeting 2027 / 2 years; **Stretch** = 3–4 YOE; **Eligibility warning** = 5+ YOE or explicit citizenship/no-sponsorship concern. A strong title match does **not** mean you meet all requirements.
- Uses visa/sponsorship signals internally for hard exclusions. The compact filtered sheet and email do not display a Visa column; raw source values remain available in the unfiltered audit sheet.
- Skips internships/co-ops requiring enrollment and unrelated functions. Seniority words alone do not exclude a role. Company/role/location dedup is approximate. Always confirm employer job descriptions, graduation window, enrollment, and visa/OPT eligibility.
- The `Filtered 14 Days` sheet is deduplicated and applies the approved hard exclusions: nontechnical/student roles, explicit non-US locations, no sponsorship or citizenship restrictions, 2+ years required experience, PhD-only roles, active-clearance requirements, and invalid/out-of-window dates. Seniority words alone do not exclude a role.
- The `All Ingested 28 Days` sheet keeps every valid dated source record from the last 28 days without business filtering or deduplication. It records whether and why each row was excluded from the filtered sheet.
- Both sheets include source provenance, including a GitHub repository link when the source is hosted on GitHub.
- Puts filtered jobs from the previous calendar day only in a compact email table with separate company and job-title columns, posting date, short notes, and a right-aligned Apply button. Listings from the run date are excluded from the email. Rows sort by popular-tech/Fortune-500 company priority, then early-career match.
- Attaches `job_digest.xlsx` and also uploads it as a GitHub Actions artifact retained for 14 days.

## Enable daily email

This workflow uses Gmail SMTP. In the GitHub repository, open **Settings → Secrets and variables → Actions → New repository secret** and add:

- `SMTP_USER`: the Gmail address that sends the digest.
- `SMTP_PASSWORD`: a 16-character Google app password, not the normal account password. Google 2-Step Verification must be enabled before creating an app password.
- `EMAIL_TO`: one recipient email address, or multiple addresses separated by commas.

The workflow only needs read permission to repository contents. After adding the secrets, run it manually once from the **Actions** tab and verify the received email before relying on the daily schedule.

## Customize

Edit `config/settings.json`:

| Parameter | Meaning |
|---|---|
| `lookback_days` | Rolling posting-date window (14 days) |
| `all_jobs_lookback_days` | Unfiltered audit-sheet window (28 days) |
| `max_html_jobs` | Maximum number of last-three-day jobs shown in the HTML email body |

Edit `config/sources.json` to add or remove feeds.

Edit `config/company_priority.json` to maintain the popular-technology employer tier, company aliases, and the Fortune 500 reference catalog used for within-day email ordering. The catalog records its reference year and sources so annual updates are auditable.

Supported types:
- `json`: use a public raw GitHub JSON URL with nested lists of job postings.
- `markdown`: parse GitHub Markdown job listing tables (best-effort).
- `greenhouse`: specify `board` slug (`GET boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true`).
- `ashby`: specify `board` slug (`GET api.ashbyhq.com/posting-api/job-board/{board}`).
- `lever`: specify `board` slug (`GET api.lever.co/v0/postings/{board}?mode=json`).

**Limitations and caveats**
- The repository and its Markdown/JSON fields can change, especially community-driven trackers. Some sources may produce zero parsed jobs; inspect `sources_health.json` and the logs. A source marked successful is not necessarily delivering *complete* coverage. Graduation and degree requirements can be buried in descriptions; title-only filtering is not fully reliable.
- No automatic application submission and no resume/PII should be stored in this repository.
- GitHub's free private-runner allowance is currently 2,000 minutes/month on Free accounts; review current GitHub billing. GitHub-hosted runner time is charged by minutes, not number of postings. External email/ATS rate limits are separate.
- The sources and workbook have been validated with live crawls, but upstream schemas can still change. Run the workflow manually and inspect the Actions log before relying on the schedule.

## Local testing

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python job_digest.py
```

The command generates the workbook without sending email. To test delivery locally, set `SMTP_USER`, `SMTP_PASSWORD`, and `EMAIL_TO`, then run `python job_digest.py --send-email`. Never commit passwords or `.env` files.
