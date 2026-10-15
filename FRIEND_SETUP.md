# Set Up Your Own Daily Job Digest

This guide creates an independent copy of the digest. You will use your own Gmail account, recipients, GitHub Actions runs, and configuration. You do not need access to the original owner's secrets.

## 1. Fork the repository

On GitHub, select **Fork**, choose your account, and keep the default branch named `main`.

If GitHub Actions are disabled in the fork, open **Actions** and select **I understand my workflows, go ahead and enable them**.

## 2. Create a Gmail app password

Use a Gmail account that you are comfortable using as the sender.

1. Enable Google 2-Step Verification for that account.
2. Create a Google app password for the digest.
3. Save the 16-character app password temporarily in a password manager.

Do not use your normal Gmail password and do not place the app password in a repository file.

## 3. Add GitHub Actions secrets

In your fork, open **Settings → Secrets and variables → Actions → New repository secret** and create:

| Secret | Value |
|---|---|
| `SMTP_USER` | Gmail address that sends the digest |
| `SMTP_PASSWORD` | Google app password from the previous step |
| `EMAIL_TO` | One recipient, or multiple comma-separated addresses |

Secrets are not copied when a repository is forked. Every fork owner must configure their own.

## 4. Test it once

1. Open **Actions → Daily Job Digest Email**.
2. Select **Run workflow**, keep the `main` branch, and run it.
3. Wait for every workflow step to turn green.
4. Check the recipient inbox, Spam, Promotions, and Updates folders.
5. Download the `job-digest-*` artifact from the workflow run and inspect both Excel sheets.

If the workflow fails, open the failed step. Common causes are a missing secret, an expired app password, Gmail blocking the sign-in, or an upstream source being unavailable.

## 5. Understand the delivery window

The scheduled run is **1:00 AM San Francisco time**. The email excludes the partial run date and contains filtered jobs from the previous three complete calendar days.

For example, an October 11 run includes October 8, 9, and 10. The attached workbook separately contains the filtered 14-day view and the unfiltered 28-day audit view.

GitHub scheduled workflows can start late, especially at the beginning of an hour. A successful manual run confirms the email configuration, but scheduled delivery is still best-effort.

## 6. Customize your copy

- `config/settings.json`: location and workbook windows.
- `config/sources.json`: enabled community feeds and employer ATS sources.
- `config/company_priority.json`: popular-technology and Fortune 500 classification.
- `.github/workflows/daily_jobs.yml`: schedule and artifact retention.

After changing Python code, run:

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python job_digest.py
```

The last command generates the workbook locally without sending email.

## Privacy rules

- Never commit a Gmail app password or `.env` file.
- Never add a resume, phone number, home address, immigration document, or application profile to this repository.
- Keep personal application automation separate from this data-collection workflow.
- Rotate the Gmail app password immediately if it is exposed.

For additional context and customization details, return to [README.md](README.md).
