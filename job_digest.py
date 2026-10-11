#!/usr/bin/env python3
"""Deterministic new-grad US tech job digest. No AI services, no paid API keys."""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import os
import re
import smtplib
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlunparse

import requests

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / 'config'
STATE = ROOT / 'state' / 'seen.json'
OUT = ROOT / 'out'
HEADERS = {'User-Agent': 'NewGradDailyDigest/1.0 (personal noncommercial job discovery)'}
TECH = re.compile(r'(?i)(software|\bswe\b|\bsde\b|backend|back.end|front.end|full.stack|developer|programmer|\bAI\b|machine learning|\bML\b|deep learning|data engineer|data scientist|applied scien|research engineer|data platform|data infrastructure|analytics engineer|devops engineer|site reliability|\bSRE\b|cloud engineer|platform engineer|inference engineer)')
BAD_TITLES = re.compile(r'(?i)\b(intern(ship)?|co.op|working student|apprentice|senior|staff|principal|director|manager|head of|vice president|vp|tech support|customer support|product designer|sales|finance|hr|recruiter)\b')
US_HINT = re.compile(r'(?i)\b(USA|U\.S\.|United States|America|Remote|California|New York|Seattle|Boston|Austin|Chicago|Atlanta|Dallas|Portland|Denver|Miami|Raleigh|Palo Alto|Sunnyvale|San Francisco|San Jose|Santa Clara|Mountain View|Redwood City|Menlo Park|San Mateo|Cupertino|Fremont|Bellevue|Redmond|Arlington|Washington|Houston|Detroit|Philadelphia|Los Angeles|San Diego|Bay Area)\b')
STATE_CODES = re.compile(r'(?i)(?:,|\s)(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)(?:\s|$|,|\/)')
NON_US = re.compile(r'(?i)\b(India|Canada|United Kingdom|UK|London|Dublin|Ireland|Berlin|Germany|Singapore|Australia|Tokyo|Japan|Netherlands|France|China|Taiwan|Israel|Poland|Spain|Switzerland|Hong Kong)\b')
BAY = re.compile(r'(?i)\b(San Francisco|San Jose|Santa Clara|Silicon Valley|Bay Area|Mountain View|Palo Alto|Sunnyvale|Redwood City|Menlo Park|San Mateo|Cupertino|Fremont|Oakland|Berkeley)\b')
COMPANY_TIER = {'google','meta','amazon','microsoft','apple','nvidia','openai','anthropic','databricks','snowflake','stripe','adobe','salesforce','roblox','figma','notion','palantir','uber','airbnb','netflix','doordash','ramp','scale ai','tiktok','linkedin','oracle','paypal','coinbase','cloudflare','mongodb','datadog','rippling'}


class Stripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
    def handle_data(self, data):
        self.parts.append(data)


def text_only(value):
    if not value:
        return ''
    st = Stripper()
    st.feed(str(value))
    return html.unescape(' '.join(st.parts))


def first(obj, *keys):
    for k in keys:
        v = obj.get(k)
        if v is not None and v != '':
            return v
    return ''


def string_location(value):
    if isinstance(value, dict):
        return str(first(value, 'name', 'location', 'city', 'text') or '')
    if isinstance(value, list):
        return ', '.join(string_location(a) for a in value if a)
    return str(value or '')


def date_parse(value, today=None):
    today = today or datetime.now(timezone.utc)
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    value = str(value).strip()
    rel = re.fullmatch(r'(\d+)\s*([dh])', value, re.I)
    if rel:
        return today - (timedelta(hours=int(rel.group(1))) if rel.group(2).lower() == 'h' else timedelta(days=int(rel.group(1))))
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def canonical_url(link):
    if not isinstance(link, str) or not link.startswith(('https://', 'http://')):
        return ''
    parsed = urlparse(link)
    query = parse_qs(parsed.query)
    preserve = []
    for k in ('gh_jid', 'job_id', 'jid'):
        if query.get(k):
            preserve.append(f'{k}={query[k][0]}')
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip('/'), '', '&'.join(preserve), ''))


def iter_records(data, depth=0):
    if depth > 6:
        return
    if isinstance(data, list):
        for item in data:
            yield from iter_records(item, depth+1)
    elif isinstance(data, dict):
        if any(k in data for k in ('title', 'job_title', 'position', 'role')) and any(k in data for k in ('url', 'apply_url', 'listingUrl', 'jobUrl', 'absolute_url', 'application_url', 'job_url', 'link')):
            yield data
        else:
            for key, value in data.items():
                if key in ('metadata', 'schema', 'coverage', 'source_discovery', 'stats', 'config'):
                    continue
                if isinstance(value, (dict,list)):
                    yield from iter_records(value, depth+1)


def parse_markdown(markdown):
    headers = None
    aliases = {'company': ('company','employer'), 'title': ('role','position','job title','title'), 'location': ('location','city'), 'url': ('posting','apply','application','link','actions'), 'posted': ('age','posted','date posted','date added')}
    def index(cols, options):
        return next((i for i,c in enumerate(cols) if any(opt in c for opt in options)), None)
    for row in markdown.splitlines():
        if not row.lstrip().startswith('|'):
            continue
        cols = [x.strip() for x in row.strip().strip('|').split('|')]
        normalized = [re.sub(r'[^a-z ]','',text_only(x).lower()).strip() for x in cols]
        if ('company' in normalized or 'employer' in normalized) and any(n in normalized for n in ('role','position','title','job title')):
            headers = {k:index(normalized,opts) for k,opts in aliases.items()}
            continue
        if not headers or all(re.fullmatch(r':?-{3,}:?',c or '') for c in cols):
            continue
        needed = (headers['company'], headers['title'], headers['url'])
        if any(x is None or x >= len(cols) for x in needed):
            continue
        def cell(k):
            ix = headers.get(k)
            return cols[ix] if ix is not None and ix < len(cols) else ''
        urlcell = cell('url')
        matches = re.findall(r'\]\((https?://[^\s)]+)', urlcell)
        if not matches:
            matches = re.findall(r'href=[\'\"](https?://[^\'\"]+)',urlcell)
        if not matches:
            matches = re.findall(r'https?://[^\s<>\)]+',urlcell)
        if not matches:
            continue
        # Remove markdown link labels and inline HTML from cells.
        def clean(value):
            value = re.sub(r'!?(?:\[[^\]]*\])\([^)]*\)', '', value)
            value = re.sub(r'<[^>]+>', ' ', value)
            return re.sub(r'\s+', ' ', html.unescape(value)).strip().strip('* ')
        company, title = clean(cell('company')),clean(cell('title'))
        if not company or not title or title.lower() in ('position','role'):
            continue
        yield {'company':company,'title':title,'location':clean(cell('location')),'apply_url':matches[-1].replace('&amp;','&'),'posted':clean(cell('posted'))}


def normalize(raw, source, source_type):
    if not isinstance(raw, dict):
        return None
    company = str(first(raw,'company_name','company','employer','organization') or '').strip()
    if isinstance(raw.get('company'),dict):
        company = str(first(raw['company'],'name','title') or '')
    title = str(first(raw,'title','job_title','position','role','name') or '').strip()
    url = first(raw,'listingUrl','apply_url','absolute_url','jobUrl','job_url','application_url','url','link')
    if isinstance(url,dict):
        url = first(url,'url','href')
    if not company and source_type in ('greenhouse','ashby','lever'):
        company = source.replace(' Direct','')
    location = string_location(first(raw,'locations','location','locationName','city','workplace_location'))
    if not location and (raw.get('state') or raw.get('city')):
        location = ', '.join(str(x) for x in [raw.get('city'),raw.get('state')] if x)
    if isinstance(raw.get('categories'),dict):
        location = location or string_location(raw['categories'].get('location'))
    if not (company and title and isinstance(url,str) and url.startswith(('http://','https://'))):
        return None
    description = text_only(first(raw,'descriptionPlain','content','description','descriptionHtml','descriptionBody','text','summary'))[:9000]
    if raw.get('active') is False or raw.get('isListed') is False or raw.get('closed_at'):
        return None
    return {
        'company':company, 'title':title, 'location':location or 'Location not stated',
        'url':url, 'source':source, 'description':description,
        'posted':first(raw,'posted_at','posted','published_at','created_at','publishedAt','first_seen','updated_at','age'),
        'visa':str(first(raw,'sponsorship','visa','visa_status') or ''),
        'degree':str(raw.get('degree') or ''), 'track':str(raw.get('track') or ''),
    }


def source_items(session, item):
    typ = item['type']
    if typ in ('greenhouse','lever','ashby'):
        board = item['board']
        if typ == 'greenhouse':
            url = f'https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true'
        elif typ == 'lever':
            url = f'https://api.lever.co/v0/postings/{board}?mode=json'
        else:
            url = f'https://api.ashbyhq.com/posting-api/job-board/{board}'
    else:
        url = item['url']
    resp = session.get(url, timeout=(8,40), headers=HEADERS)
    resp.raise_for_status()
    if typ == 'markdown':
        return [x for x in (normalize(row,item['name'],typ) for row in parse_markdown(resp.text)) if x]
    data = resp.json()
    if typ == 'greenhouse':
        return [x for x in (normalize({**job,'company':item['name'].replace(' Direct',''),'url':job.get('absolute_url')},item['name'],typ) for job in data.get('jobs',[])) if x]
    if typ == 'ashby':
        return [x for x in (normalize({**job,'company':item['name'].replace(' Direct',''),'url':job.get('jobUrl') or job.get('applyUrl')},item['name'],typ) for job in data.get('jobs',[])) if x]
    if typ == 'lever':
        return [x for x in (normalize({**job,'company':item['name'].replace(' Direct',''),'url':job.get('hostedUrl')},item['name'],typ) for job in data) if x]
    return [x for x in (normalize(row,item['name'],typ) for row in iter_records(data)) if x]


def in_us(location):
    location = location.strip()
    if not location or location.lower() == 'location not stated':
        return True
    if NON_US.search(location) and not (STATE_CODES.search(location) or re.search(r'(?i)\b(USA|U\.S\.|United States)\b',location)):
        return False
    if US_HINT.search(location) or STATE_CODES.search(location):
        return True
    return True  # Unknown location is retained, not silently excluded.


def job_key(j):
    # Primarily company+title+location; avoids re-emailing across community feeds.
    norm = lambda s: re.sub(r'[^a-z0-9]+','',s.lower())
    company = norm(j['company'])
    title = norm(j['title'])
    loc = norm(j['location'])
    return hashlib.sha256(f'{company}|{title}|{loc}'.encode('utf-8')).hexdigest()[:24]


def classify(job, graduate_year=2026):
    title, desc = job['title'], job['description']
    full = f'{title} {desc[:8000]}'
    visa_code = job['visa'].lower().replace('_','-')
    if visa_code in ('no-sponsorship','no sponsorship','no','does not sponsor') or re.search(r'(?i)(no visa sponsorship|unable to sponsor|do not sponsor|cannot sponsor)',full):
        visa = 'No sponsorship'
    elif visa_code in ('citizenship','us-citizenship','citizenship-required') or re.search(r'(?i)(u\.s\. citizenship required|must be a u\.s\. citizen|us citizens only)',full):
        visa = 'US citizenship required'
    elif visa_code in ('sponsors','sponsorship available','yes') or re.search(r'(?i)(visa sponsorship (is |will be )?available|we (do )?offer (visa )?sponsorship)',full):
        visa = 'Sponsorship stated'
    elif visa_code in ('h1b-history','h1b-history-strong','h1b-history-weak','h1b-history-sparse'):
        visa = 'H-1B history only'
    else:
        visa = 'Sponsorship unknown'
    # Experience can be estimated conservatively from explicit minimum requirements.
    evidence = []
    exp_values = []
    for match in re.finditer(r'(?i)(?:minimum (?:of )?|at least |requires? |with )?(\d{1,2})\s*(?:\+|to\s*\d{1,2}|-\s*\d{1,2})?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:relevant |professional |industry |full.time |software |engineering |work )?(?:experience|in\b)',full):
        y = int(match.group(1))
        if y <= 20:
            exp_values.append(y)
    yoe = max(exp_values) if exp_values else None
    grad_years = {int(n) for n in re.findall(r'(?<!\d)20(2[4-9])(?!\d)',title)}
    # The capture group contains 24,25,...29; convert to 2024,...2029.
    grad_years = {2000+y for y in grad_years}
    early = bool(re.search(r'(?i)(new.grad|new graduate|university grad|college grad|early career|entry.level|associate software|engineer i\b|engineer 1\b|2026 grad|\bAMTS\b|graduate program)', title))
    reason = []
    if visa in ('No sponsorship','US citizenship required'):
        reason.append(visa)
    if re.search(r'(?i)\b(only current students|currently enrolled|enrolled in a degree program)\b',full):
        reason.append('Requires current enrollment')
    if yoe is not None and yoe >= 3:
        reason.append(f'{yoe}+ YOE mentioned (check requirements)')
    if grad_years and graduate_year not in grad_years:
        reason.append(f'Title targets {", ".join(map(str,sorted(grad_years)))} graduates')
    # Only a clear title-level mismatch or explicit visa restriction is an eligibility warning.
    if any(x in visa for x in ('No sponsorship','US citizenship')) or (yoe is not None and yoe >= 5) or 'Requires current enrollment' in reason:
        fit = 'Eligibility warning'
    elif yoe is not None and yoe >= 3:
        fit = 'Stretch'
    elif grad_years and graduate_year not in grad_years:
        fit = 'Review'
    elif (yoe is not None and yoe == 2):
        fit = 'Review'
    elif early or graduate_year in grad_years:
        fit = 'Strong match'
    else:
        fit = 'Review'
        reason.append('Graduate eligibility and required experience unclear')
    # Generic "junior" roles count as review unless eligibility known.
    score = {'Strong match':100,'Review':65,'Stretch':35,'Eligibility warning':20}[fit]
    if graduate_year in grad_years:
        score += 18
    if BAY.search(job['location']):
        score += 8
    if job['company'].strip().lower() in COMPANY_TIER:
        score += 5
    if visa == 'Sponsorship stated':
        score += 8
    if visa in ('No sponsorship','US citizenship required'):
        score -= 12
    job.update({'fit':fit,'visa_tag':visa,'reasons':' | '.join(reason) or 'Appears to be an early-career opening; verify description', 'score':score, 'yoe_detected':yoe})
    return job


def eligible(job, cfg, today):
    if BAD_TITLES.search(job['title']) or not TECH.search(job['title']):
        return False
    if cfg['location'] == 'US' and not in_us(job['location']):
        return False
    date = date_parse(job.get('posted'),today)
    if date and date < today-timedelta(days=cfg['max_post_age_days']):
        return False
    return True


def csv_bytes(rows):
    cols = ['company','title','location','fit','visa_tag','yoe_detected','reasons','posted','url','source']
    target = io.StringIO()
    writer = csv.DictWriter(target,fieldnames=cols, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)
    return target.getvalue().encode('utf-8-sig')


def html_report(rows, source_status, cfg, today, all_count):
    visible = rows[:cfg['max_html_jobs']]
    counts = Counter(row['fit'] for row in rows)
    s = ['<!DOCTYPE html><html><body style="font-family:Arial,Helvetica,sans-serif;color:#1f2937;line-height:1.55;max-width:950px;margin:auto;padding:16px">',
         f'<h1 style="margin-bottom:4px">Daily Tech Jobs — {html.escape(today.strftime("%d %b %Y"))}</h1>',
         f'<p>US-based SWE/SDE, AI/ML, Data Engineering and Data Science • Santa Clara University MSCS 2026</p>',
         f'<p><b>{all_count}</b> new discovered roles &nbsp; | &nbsp; <b>{counts["Strong match"]}</b> strong matches &nbsp; | &nbsp; <b>{counts["Review"]}</b> review &nbsp; | &nbsp; <b>{counts["Eligibility warning"]}</b> warnings</p>',
         '<p style="color:#555;font-size:13px">“New” means newly seen by your digest, not necessarily posted today. Eligibility tags are heuristics. Confirm every role and sponsorship detail on the employer posting. CSV attachment contains every new candidate.</p>']
    for tag, color in [('Strong match','#16703a'),('Review','#9a6500'),('Stretch','#5a5a5a'),('Eligibility warning','#a32929')]:
        group = [r for r in visible if r['fit']==tag]
        if not group:
            continue
        s.append(f'<h2 style="border-bottom:2px solid {color};padding-bottom:4px">{html.escape(tag)} ({counts[tag]})</h2>')
        s.append('<table cellpadding="8" cellspacing="0" style="width:100%;border-collapse:collapse;font-size:13px"><thead><tr style="background:#f3f4f6"><th align="left">Role</th><th align="left">Location</th><th align="left">Visa</th><th align="left">Notes</th></tr></thead><tbody>')
        for r in group:
            company=html.escape(r['company']); title=html.escape(r['title']);url=html.escape(r['url'],quote=True)
            s.append(f'<tr style="border-bottom:1px solid #ddd"><td><a href="{url}"><b>{company}</b> — {title}</a><br><span style="color:#737373">{html.escape(r["source"])}</span></td><td>{html.escape(r["location"])}</td><td>{html.escape(r["visa_tag"])}</td><td>{html.escape(r["reasons"])}</td></tr>')
        s.append('</tbody></table>')
    if not rows:
        s.append('<p>No newly discovered matching openings today. Previous openings have not been repeated.</p>')
    if len(rows)>len(visible):
        s.append(f'<p><b>{len(rows)-len(visible)} more openings</b> are included in the attached CSV.</p>')
    if cfg.get('show_sources_status'):
        failed=[x for x in source_status if x['error']]
        ok=[x for x in source_status if not x['error']]
        s.append(f'<p style="color:#555;font-size:12px">Source health: {len(ok)} succeeded, {len(failed)} failed. Total source rows before filtering/dedup: {sum(x["count"] for x in ok):,}.</p>')
        if failed:
            s.append('<p style="font-size:12px;color:#a32929">Unavailable feeds today: '+html.escape(', '.join(x['name'] for x in failed))+'</p>')
    s.append('</body></html>')
    return ''.join(s)


def send_mail(rows, csv_data, sources, cfg, today, all_count):
    sender,password,recipient=(os.getenv('SMTP_USER',''),os.getenv('SMTP_PASSWORD',''),os.getenv('EMAIL_TO',''))
    if not sender or not password or not recipient:
        raise RuntimeError('SMTP_USER, SMTP_PASSWORD and EMAIL_TO must all be defined as GitHub Actions secrets.')
    counts=Counter(x['fit'] for x in rows)
    mail=EmailMessage()
    mail['From']=sender
    mail['To']=recipient
    mail['Subject']=f'[{today.strftime("%d %b")}] {len(rows)} new tech roles | {counts["Strong match"]} strong | {counts["Review"]} review'
    mail.set_content('Daily new grad jobs digest. Open the HTML version for categorised, clickable jobs. All results are attached as CSV.\n\n'+ '\n'.join(f'{r["company"]} - {r["title"]} - {r["fit"]}: {r["url"]}' for r in rows[:30]))
    mail.add_alternative(html_report(rows,sources,cfg,today,all_count),subtype='html')
    mail.add_attachment(csv_data,maintype='text',subtype='csv',filename='new_tech_jobs.csv')
    with smtplib.SMTP_SSL('smtp.gmail.com',465,timeout=25) as smtp:
        smtp.login(sender,password)
        smtp.send_message(mail)


def run(dry_run=False):
    cfg=json.loads((CONFIG/'settings.json').read_text())
    sources=json.loads((CONFIG/'sources.json').read_text())['sources']
    now=datetime.now(timezone.utc)
    had_state=STATE.exists() and STATE.read_text().strip() not in ('','{}')
    seen=json.loads(STATE.read_text()) if STATE.exists() else {}
    fetched=[]; health=[]
    session=requests.Session()
    for entry in sources:
        try:
            items=source_items(session,entry)
            fetched.extend(items)
            health.append({'name':entry['name'],'count':len(items),'error':''})
            print(f'[ok] {entry["name"]}: {len(items)} records')
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            health.append({'name':entry['name'],'count':0,'error':str(exc)[:180]})
            print(f'[warning] {entry["name"]}: {type(exc).__name__}: {str(exc)[:130]}')
    if not any(not item['error'] for item in health):
        raise RuntimeError('Every source failed; declining to send a misleading empty report.')
    selected={}
    for job in fetched:
        if not eligible(job,cfg,now):
            continue
        j=classify(job,cfg['graduate_year'])
        if (j.get('yoe_detected') or 0) >= cfg['max_very_senior_yoe']:
            continue
        key=job_key(j)
        j['id']=key
        existing=selected.get(key)
        # Prefer job with informative description, explicit sponsorship info, and canonical employer link.
        quality=len(j['description']) + (200 if j['visa_tag'] not in ('Sponsorship unknown','H-1B history only') else 0)
        if not existing or quality>existing['_quality']:
            j['_quality']=quality
            selected[key]=j
    candidates=list(selected.values())
    candidates.sort(key=lambda j:(j['score'],date_parse(j['posted'],now) or datetime(1970,1,1,tzinfo=timezone.utc)),reverse=True)
    output=[]; undated=0
    for j in candidates:
        key=j['id']
        if key in seen:
            continue
        published=date_parse(j['posted'],now)
        if not had_state:
            if published and published < now-timedelta(days=cfg['first_run_lookback_days']):
                continue
            if not published:
                if undated >= cfg['first_run_undated_limit']:
                    continue
                undated += 1
        output.append(j)
    # Initial run records all currently discovered jobs so earlier postings don't return as "new" tomorrow.
    todaystamp=now.isoformat(timespec='seconds')
    new_seen=dict(seen)
    for key in selected:
        if key not in new_seen:
            new_seen[key]=todaystamp
    # Prune very old state entries; a moved/reopened job will be rediscovered later.
    cutoff=now-timedelta(days=400)
    new_seen={k:v for k,v in new_seen.items() if (date_parse(v,now) or now)>=cutoff}
    OUT.mkdir(parents=True,exist_ok=True)
    csv_data=csv_bytes(output)
    (OUT/'jobs.csv').write_bytes(csv_data)
    (OUT/'digest.html').write_text(html_report(output,health,cfg,now,len(output)),encoding='utf-8')
    (OUT/'sources_health.json').write_text(json.dumps(health,indent=2),encoding='utf-8')
    print(f'Candidates={len(candidates)} new={len(output)} strong={sum(j["fit"]=="Strong match" for j in output)}')
    if not dry_run and (output or cfg['always_email_even_when_zero_new']):
        send_mail(output,csv_data,health,cfg,now,len(output))
        print('Email sent successfully.')
    if not dry_run:
        STATE.parent.mkdir(parents=True,exist_ok=True)
        STATE.write_text(json.dumps(new_seen,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--dry-run',action='store_true',help='Generate reports without sending email or altering state')
    args=parser.parse_args()
    run(dry_run=args.dry_run)
