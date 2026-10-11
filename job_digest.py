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
import shutil
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlunparse

import requests
import xlsxwriter

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / 'config'
OUT = ROOT / 'out'
HEADERS = {'User-Agent': 'NewGradDailyDigest/1.0 (personal noncommercial job discovery)'}
TECH = re.compile(r'(?i)(software|\bswe\b|\bsde\b|backend|back.end|front.end|full.stack|developer|programmer|\bAI\b|machine learning|\bML\b|deep learning|data engineer|data scientist|applied scien|research engineer|data platform|data infrastructure|analytics engineer|devops engineer|site reliability|\bSRE\b|cloud engineer|platform engineer|inference engineer)')
BAD_TITLES = re.compile(r'(?i)\b(intern(ship)?|co.op|working student|apprentice|tech support|customer support|product designer|sales|finance|hr|recruiter|legal counsel|account executive)\b')
US_HINT = re.compile(r'(?i)\b(USA|U\.S\.|United States|America|Remote|California|New York|Seattle|Boston|Austin|Chicago|Atlanta|Dallas|Portland|Denver|Miami|Raleigh|Palo Alto|Sunnyvale|San Francisco|San Jose|Santa Clara|Mountain View|Redwood City|Menlo Park|San Mateo|Cupertino|Fremont|Bellevue|Redmond|Arlington|Washington|Houston|Detroit|Philadelphia|Los Angeles|San Diego|Bay Area)\b')
STATE_CODES = re.compile(r'(?i)(?:,|\s)(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)(?:\s|$|,|\/)')
NON_US = re.compile(r'(?i)\b(India|Canada|United Kingdom|UK|London|Dublin|Ireland|Berlin|Germany|Singapore|Australia|Tokyo|Japan|Netherlands|France|China|Taiwan|Israel|Poland|Spain|Switzerland|Hong Kong)\b')
BAY = re.compile(r'(?i)\b(San Francisco|San Jose|Santa Clara|Silicon Valley|Bay Area|Mountain View|Palo Alto|Sunnyvale|Redwood City|Menlo Park|San Mateo|Cupertino|Fremont|Oakland|Berkeley)\b')
COMPANY_PRIORITY_DATA = json.loads((CONFIG/'company_priority.json').read_text())


def normalized_company(value):
    value=re.sub(r'[^a-z0-9]+',' ',str(value or '').casefold()).strip()
    return re.sub(r'\s+(incorporated|inc|corporation|corp|company|co|llc|ltd|holdings|technologies|technology)$','',value).strip()


COMPANY_ALIASES={normalized_company(k):normalized_company(v) for k,v in COMPANY_PRIORITY_DATA.get('aliases',{}).items()}
POPULAR_COMPANIES={normalized_company(x) for x in COMPANY_PRIORITY_DATA['popular_tech']}
FORTUNE_500_COMPANIES={normalized_company(x) for x in COMPANY_PRIORITY_DATA['fortune_500']}
COMPANY_TIER=POPULAR_COMPANIES


def company_priority(value):
    """0=popular tech, 1=Fortune 500, 2=other."""
    name=normalized_company(value)
    name=COMPANY_ALIASES.get(name,name)
    if name in POPULAR_COMPANIES:
        return 0
    if name in FORTUNE_500_COMPANIES:
        return 1
    return 2


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
        pass
    # Some community tables publish month/day without a year. Infer the most
    # recent non-future occurrence and retain the inference in date_basis.
    for fmt in ('%b %d', '%B %d'):
        try:
            parsed = datetime.strptime(value, fmt).replace(year=today.year, tzinfo=timezone.utc)
            if parsed > today + timedelta(days=1):
                parsed = parsed.replace(year=today.year - 1)
            return parsed
        except ValueError:
            continue
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
    posted_raw=first(raw,'posted_at','posted','published_at','created_at','publishedAt','first_seen','date_added','date_seen','updated_at','age')
    return {
        'company':company, 'title':title, 'location':location or 'Location not stated',
        'url':url, 'source':source, 'description':description,
        'posted':posted_raw, 'posted_raw':str(posted_raw or ''),
        'source_updated_at':str(first(raw,'source_updated_at','updated_at','last_seen','last_verified') or ''),
        'source_record_id':str(first(raw,'job_id','uid','role_id','source_id','id','internal_job_id','requisition_id') or ''),
        'date_basis':str(raw.get('_date_basis') or 'source_reported_posted'),
        'date_confidence':str(raw.get('_date_confidence') or 'exact'),
        'visa':str(first(raw,'sponsorship','visa','visa_status') or ''),
        'degree':str(first(raw,'degree','education') or ''),
        'track':string_location(first(raw,'track','roles','discipline','disciplines','department','team')),
        'seniority':str(raw.get('seniority') or ''),
        'employment_type':str(first(raw,'employmentType','role_type','type') or ''),
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
    def decorate(rows):
        for row in rows:
            row['source_type']=typ
            row['source_url']=item.get('source_page') or item.get('url') or url
            row['github_source']=item.get('github_repo','')
        return rows
    if typ == 'markdown':
        basis='month_day_inferred' if item['name'].startswith('Vansh') else 'relative_age_derived'
        confidence='inferred' if basis == 'month_day_inferred' else 'derived'
        parsed=[]
        for row in parse_markdown(resp.text):
            row['_date_basis']=basis; row['_date_confidence']=confidence
            normalized=normalize(row,item['name'],typ)
            if normalized: parsed.append(normalized)
        return decorate(parsed)
    data = resp.json()
    if typ == 'ypatodkar':
        companies=data.get('companies',[]); fields=data.get('companyFields',[]); parsed=[]
        for job in data.get('jobs',[]):
            company=companies[job.get('c',-1)] if isinstance(job.get('c'),int) and 0 <= job.get('c') < len(companies) else {}
            raw={**job,'company':company.get('n',''),'location':job.get('locations') or job.get('city',''),
                 '_date_basis':'employer_published','_date_confidence':'exact','id':job.get('job_id','')}
            normalized=normalize(raw,item['name'],typ)
            if normalized: parsed.append(normalized)
        return decorate(parsed)
    if typ == 'greenhouse':
        company=item['name'].replace(' Official ATS','')
        rows=[]
        for job in data.get('jobs',[]):
            raw={**job,'company':company,'url':job.get('absolute_url'),'posted':job.get('first_published'),
                 'source_updated_at':job.get('updated_at'),'_date_basis':'employer_published','_date_confidence':'exact'}
            normalized=normalize(raw,item['name'],typ)
            if normalized: rows.append(normalized)
        return decorate(rows)
    if typ == 'ashby':
        company=item['name'].replace(' Official ATS','')
        rows=[]
        for job in data.get('jobs',[]):
            raw={**job,'company':company,'url':job.get('jobUrl') or job.get('applyUrl'),
                 '_date_basis':'employer_published','_date_confidence':'exact'}
            normalized=normalize(raw,item['name'],typ)
            if normalized: rows.append(normalized)
        return decorate(rows)
    if typ == 'lever':
        return decorate([x for x in (normalize({**job,'company':item['name'].replace(' Official ATS',''),'url':job.get('hostedUrl')},item['name'],typ) for job in data) if x])
    rows=[]
    for raw in iter_records(data):
        if item['name'].startswith('Visa-Aware') and not raw.get('posted'):
            raw={**raw,'_date_basis':'source_first_seen','_date_confidence':'discovery_only'}
        elif item['name'].startswith('2027 Tech'):
            raw={**raw,'_date_basis':'source_date_added','_date_confidence':'discovery_only'}
        else:
            raw={**raw,'_date_basis':'source_reported_posted','_date_confidence':'exact'}
        normalized=normalize(raw,item['name'],typ)
        if normalized: rows.append(normalized)
    return decorate(rows)


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


def classify(job):
    title, desc = job['title'], job['description']
    full = f'{title} {desc[:8000]}'
    visa_code = job['visa'].lower().replace('_','-')
    if visa_code in ('no-sponsorship','no sponsorship','no','does not sponsor') or re.search(r'(?i)(no visa sponsorship|unable to sponsor|do not sponsor|cannot sponsor|without (?:current or future )?(?:visa )?sponsorship|will not (?:provide|offer) sponsorship)',full):
        visa = 'No sponsorship'
    elif visa_code in ('citizenship','us-citizenship','citizenship-required') or re.search(r'(?i)(u\.s\. citizenship required|must be a u\.s\. citizen|us citizens only|u\.s\. persons? only|must be (?:a )?(?:u\.s\. )?permanent resident|green card holders? only)',full):
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
    early = bool(re.search(r'(?i)(new.grad|new graduate|university grad|college grad|early career|entry.level|associate software|engineer i\b|engineer 1\b|20(?:2[4-9]) grad|\bAMTS\b|graduate program)', title))
    reason = []
    if visa in ('No sponsorship','US citizenship required'):
        reason.append(visa)
    if re.search(r'(?i)\b(only current students|currently enrolled|enrolled in a degree program)\b',full):
        reason.append('Requires current enrollment')
    if yoe is not None and yoe >= 3:
        reason.append(f'{yoe}+ YOE mentioned (check requirements)')
    # Only a clear title-level mismatch or explicit visa restriction is an eligibility warning.
    if any(x in visa for x in ('No sponsorship','US citizenship')) or (yoe is not None and yoe >= 5) or 'Requires current enrollment' in reason:
        fit = 'Eligibility warning'
    elif yoe is not None and yoe >= 3:
        fit = 'Stretch'
    elif (yoe is not None and yoe == 2):
        fit = 'Review'
    elif early:
        fit = 'Strong match'
    else:
        fit = 'Review'
        reason.append('Graduate eligibility and required experience unclear')
    # Generic "junior" roles count as review unless eligibility known.
    score = {'Strong match':100,'Review':65,'Stretch':35,'Eligibility warning':20}[fit]
    if BAY.search(job['location']):
        score += 8
    if company_priority(job['company']) == 0:
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
    date = date_parse(job.get('posted'), today)
    # A rolling lookback artifact must have a usable posting date. Undated jobs
    # cannot be proven to belong to the requested window.
    if not date:
        return False
    if date < today-timedelta(days=cfg['lookback_days']):
        return False
    if date > today+timedelta(days=1):
        return False
    return True


def exclusion_reasons(job, cfg, today):
    """Return only approved hard-exclusion reasons for the filtered sheet."""
    reasons=[]
    published=date_parse(job.get('posted'),today)
    if not published:
        reasons.append('Posting date unavailable')
    elif published < today-timedelta(days=cfg['lookback_days']):
        reasons.append(f'Older than {cfg["lookback_days"]} days')
    elif published > today+timedelta(days=1):
        reasons.append('Posting date is in the future')
    if not TECH.search(job['title']):
        reasons.append('Outside selected technical job families')
    if BAD_TITLES.search(job['title']):
        reasons.append('Internship/student or irrelevant function')
    if cfg['location']=='US' and not in_us(job['location']):
        reasons.append('Explicitly outside the US')
    if job.get('visa_tag') in ('No sponsorship','US citizenship required'):
        reasons.append(job['visa_tag'])
    if (job.get('yoe_detected') or 0) >= 2:
        reasons.append(f'{job["yoe_detected"]}+ years experience required')
    full=f'{job["title"]} {job.get("description","")}'
    if re.search(r'(?i)\b(ph\.?d|doctorate)\b',job['title']) or re.search(r'(?i)(ph\.?d|doctorate)\s+(?:is\s+)?required',full):
        reasons.append('PhD/doctorate required')
    if re.search(r'(?i)(active (?:top secret|secret) clearance|required security clearance|must possess.{0,30}clearance)',full):
        reasons.append('Active security clearance required')
    return list(dict.fromkeys(reasons))


def csv_bytes(rows):
    cols = ['company','title','location','fit','visa_tag','yoe_detected','reasons','posted','url','source']
    target = io.StringIO()
    writer = csv.DictWriter(target,fieldnames=cols, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)
    return target.getvalue().encode('utf-8-sig')


def job_family(job):
    title=job['title']
    for label,pattern in (
        ('AI/ML',r'(?i)(artificial intelligence|machine learning|deep learning|\bAI\b|\bML\b|inference)'),
        ('Data',r'(?i)(data engineer|data scientist|analytics engineer|data platform)'),
        ('Infrastructure',r'(?i)(devops|site reliability|\bSRE\b|cloud|platform|infrastructure)'),
        ('Software Engineering',r'(?i)(software|\bswe\b|\bsde\b|developer|backend|front.end|full.stack|programmer)'),
        ('Research',r'(?i)(research engineer|applied scien)'),
    ):
        if re.search(pattern,title): return label
    return job.get('track') or 'Other technical'


def compact_note(job):
    """Short, scan-friendly context for presentation surfaces."""
    if job.get('fit') == 'Strong match':
        return 'Early-career match'
    if job.get('yoe_detected') == 1:
        return '1 year mentioned'
    if job.get('fit') == 'Stretch':
        return 'Check experience'
    return 'Check eligibility'


def sort_by_posted(rows, today):
    """Date, recognized employer, then early-career match; all deterministic."""
    fallback=datetime(1970,1,1,tzinfo=timezone.utc)
    return sorted(rows,key=lambda row: (
        -(date_parse(row.get('posted'),today) or fallback).date().toordinal(),
        company_priority(row.get('company','')),
        0 if row.get('fit') == 'Strong match' else 1,
        row.get('company','').casefold(),
        row.get('title','').casefold(),
    ))


def write_workbook(filtered, all_jobs, source_status, cfg, today):
    path=OUT/'job_digest.xlsx'
    temp_handle=tempfile.NamedTemporaryFile(prefix='job_digest_',suffix='.xlsx',delete=False)
    temp_path=Path(temp_handle.name); temp_handle.close()
    wb=xlsxwriter.Workbook(temp_path)
    wb.set_properties({'title':'US Early-Career Job Digest','subject':'Filtered 14-day jobs and all ingested jobs from the last 28 days'})
    header=wb.add_format({'bold':True,'font_color':'#FFFFFF','bg_color':'#17365D','align':'center','valign':'vcenter','border':0})
    title=wb.add_format({'bold':True,'font_size':14,'font_color':'#17365D'})
    note=wb.add_format({'italic':True,'font_color':'#595959'})
    datefmt=wb.add_format({'num_format':'mm/dd/yy','align':'right'})
    linkfmt=wb.add_format({'font_color':'#0563C1','underline':True})
    wrap=wb.add_format({'text_wrap':True,'valign':'top'})
    body=wb.add_format({'valign':'top'})
    warning=wb.add_format({'bg_color':'#FFF2CC','font_color':'#7F6000'})

    filtered_cols=['Company','Job Title','Location','Posted Date','Age Days','Date Basis','Date Confidence','Job Family','Seniority','Experience Required','Degree','Fit','Notes','Source','GitHub Source','Source Page','Source Type','Source Record ID','Duplicate Group ID','Apply']
    all_cols=['Source','Source Type','GitHub Source','Source Page','Source Record ID','Company','Role','Location Raw','Posted Raw','Effective Date','Age Days','Date Basis','Date Confidence','Source Updated At','Application URL','Description','Visa Raw','Degree Raw','Track Raw','Seniority','Employment Type','Duplicate Group ID','Duplicate Flag','Collected At','Filter Status','Exclusion Reasons']

    def excel_date(value):
        parsed=date_parse(value,today)
        return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed else None
    def add_sheet(name,cols,rows,filtered_sheet):
        ws=wb.add_worksheet(name); ws.hide_gridlines(2); ws.set_tab_color('#17365D' if filtered_sheet else '#5B9BD5')
        ws.write(1,0,name,title)
        scope=(f'{len(rows):,} deduplicated jobs after approved hard exclusions; rolling {cfg["lookback_days"]}-day window.' if filtered_sheet
               else f'{len(rows):,} valid source records; no business filters or deduplication; rolling {cfg["all_jobs_lookback_days"]}-day window.')
        ws.write(2,0,scope,note)
        start=4
        for c,label in enumerate(cols): ws.write(start,c,label,header)
        for r_idx,row in enumerate(rows,start+1):
            vals=[]
            if filtered_sheet:
                posted=excel_date(row.get('posted'))
                vals=[row['company'],row['title'],row['location'],posted,(today-date_parse(row['posted'],today)).days if posted else None,row.get('date_basis',''),row.get('date_confidence',''),job_family(row),row.get('seniority',''),row.get('yoe_detected'),row.get('degree',''),row.get('fit',''),compact_note(row),row['source'],row.get('github_source',''),row.get('source_url',''),row.get('source_type',''),row.get('source_record_id',''),row.get('id',''),row['url']]
            else:
                posted=excel_date(row.get('posted'))
                vals=[row['source'],row.get('source_type',''),row.get('github_source',''),row.get('source_url',''),row.get('source_record_id',''),row['company'],row['title'],row['location'],row.get('posted_raw',''),posted,(today-date_parse(row['posted'],today)).days if posted else None,row.get('date_basis',''),row.get('date_confidence',''),row.get('source_updated_at',''),row['url'],row.get('description',''),row.get('visa',''),row.get('degree',''),row.get('track',''),row.get('seniority',''),row.get('employment_type',''),row.get('id',''),row.get('duplicate_flag',''),today.astimezone(timezone.utc).replace(tzinfo=None),row.get('filter_status',''),row.get('exclusion_reasons','')]
            for c,val in enumerate(vals):
                fmt=body
                if isinstance(val,datetime): fmt=datefmt
                elif cols[c] in ('Application URL','Apply','GitHub Source','Source Page') and val:
                    ws.write_url(r_idx,c,val,linkfmt,string='Apply' if cols[c]=='Apply' else 'Open'); continue
                elif cols[c] in ('Role','Job Title','Location','Notes','Description','Exclusion Reasons'): fmt=wrap
                ws.write(r_idx,c,val,fmt)
        last=start+len(rows)
        if rows:
            table_name='FilteredJobsTable' if filtered_sheet else 'AllIngestedJobsTable'
            ws.add_table(start,0,last,len(cols)-1,{'name':table_name,'columns':[{'header':x} for x in cols],'style':'Table Style Medium 2'})
            if not filtered_sheet:
                status_col=cols.index('Filter Status')
                ws.conditional_format(start+1,status_col,last,status_col,{'type':'text','criteria':'containing','value':'Excluded','format':warning})
        ws.freeze_panes(start+1,3 if filtered_sheet else 2)
        widths={'Company':22,'Role':38,'Job Title':38,'Location':24,'Location Raw':24,'Description':55,'Notes':20,'Exclusion Reasons':38,'Application URL':13,'Apply':11,'GitHub Source':13,'Source Page':13,'Source':25,'Posted Raw':23,'Date Basis':22,'Source Record ID':24,'Duplicate Group ID':24}
        for c,label in enumerate(cols): ws.set_column(c,c,widths.get(label,16))
        ws.set_row(start,28)
        ws.autofilter(start,0,last,len(cols)-1) if not rows else None
    add_sheet('Filtered 14 Days',filtered_cols,filtered,True)
    add_sheet('All Ingested 28 Days',all_cols,all_jobs,False)
    try:
        wb.close()
        shutil.copyfile(temp_path,path)
    finally:
        temp_path.unlink(missing_ok=True)
    return path


def html_report(rows, source_status, cfg, today, all_count):
    visible = sort_by_posted(rows,today)[:cfg['max_html_jobs']]
    counts = Counter(row['fit'] for row in rows)
    s = ['<!DOCTYPE html><html><body style="font-family:Arial,Helvetica,sans-serif;color:#1f2937;max-width:1000px;margin:auto;padding:10px">',
         f'<h2 style="margin:0 0 4px;color:#17365d">US Early-Career Tech Jobs — {html.escape(today.strftime("%d %b %Y"))}</h2>',
         f'<p style="margin:0 0 8px;font-size:12px;color:#555"><b>{all_count}</b> openings from {html.escape(cfg.get("window_label",f"the last {cfg["lookback_days"]} days"))} · {counts["Strong match"]} strong · {counts["Review"]} review · Full 14/28-day workbook attached.</p>']
    if visible:
        s.append('<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;font-size:12px"><thead><tr style="background:#17365d;color:#fff"><th align="center" style="padding:6px;text-align:center">Company</th><th align="center" style="padding:6px;text-align:center">Job Title</th><th align="center" style="padding:6px;text-align:center">Location</th><th align="center" style="padding:6px;text-align:center;white-space:nowrap">Posted</th><th align="center" style="padding:6px;text-align:center">Notes</th><th align="center" style="padding:6px;text-align:center">Apply</th></tr></thead><tbody>')
        for r in visible:
            company=html.escape(r['company']); title=html.escape(r['title']); url=html.escape(r['url'],quote=True)
            posted=date_parse(r.get('posted'),today)
            posted_text=posted.strftime('%d %b') if posted else 'Unknown'
            s.append(f'<tr style="border-bottom:1px solid #e5e7eb"><td style="padding:5px"><b>{company}</b></td><td style="padding:5px">{title}</td><td style="padding:5px">{html.escape(r["location"])}</td><td style="padding:5px;white-space:nowrap">{html.escape(posted_text)}</td><td style="padding:5px">{html.escape(compact_note(r))}</td><td align="right" style="padding:5px"><a href="{url}" style="display:inline-block;background:#2563eb;color:#fff;text-decoration:none;padding:5px 9px;border-radius:4px;font-weight:bold">Apply</a></td></tr>')
        s.append('</tbody></table>')
    if not rows:
        s.append(f'<p>No matching openings with a usable posting date were found in the last {cfg["lookback_days"]} days.</p>')
    if len(rows)>len(visible):
        s.append(f'<p style="font-size:12px"><b>{len(rows)-len(visible)} more openings</b> are in the attached workbook.</p>')
    if cfg.get('show_sources_status'):
        failed=[x for x in source_status if x['error']]
        ok=[x for x in source_status if not x['error']]
        s.append(f'<p style="color:#555;font-size:12px">Source health: {len(ok)} succeeded, {len(failed)} failed. Total source rows before filtering/dedup: {sum(x["count"] for x in ok):,}.</p>')
        if failed:
            s.append('<p style="font-size:12px;color:#a32929">Unavailable feeds today: '+html.escape(', '.join(x['name'] for x in failed))+'</p>')
    s.append('</body></html>')
    return ''.join(s)


def rows_within_days(rows, today, days):
    """Include today and `days` complete preceding calendar dates."""
    cutoff=(today-timedelta(days=days)).date()
    end=today.date()
    selected=[]
    for row in rows:
        posted=date_parse(row.get('posted'),today)
        if posted and cutoff <= posted.date() <= end:
            selected.append(row)
    return selected


def rows_from_previous_day(rows, today):
    """Return postings from yesterday's calendar date only."""
    target=(today-timedelta(days=1)).date()
    return [row for row in rows if (posted:=date_parse(row.get('posted'),today)) and posted.date() == target]


def build_mail(rows, sources, cfg, today, sender, recipient, workbook_data):
    recent=sort_by_posted(rows_from_previous_day(rows,today),today)
    recent_cfg={**cfg,'lookback_days':1,'window_label':'the previous calendar day'}
    mail=EmailMessage()
    mail['From']=sender
    mail['To']=recipient
    mail['Subject']=f'Daily Job Digest | {len(recent)} US tech openings | {today.strftime("%d %b")}'
    mail.set_content("Daily early-career tech jobs digest. The message contains filtered roles posted on the previous calendar day only. Today's partial listings are excluded. The attached Excel workbook contains the filtered 14-day list and all ingested records from the last 28 days.\n\n"+ '\n'.join(f'{r["company"]} - {r["title"]} - {r["fit"]}: {r["url"]}' for r in recent[:30]))
    mail.add_alternative(html_report(recent,sources,recent_cfg,today,len(recent)),subtype='html')
    mail.add_attachment(workbook_data,maintype='application',subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet',filename='job_digest.xlsx')
    return mail


def send_mail(rows, sources, cfg, today):
    sender,password,recipient=(os.getenv('SMTP_USER',''),os.getenv('SMTP_PASSWORD',''),os.getenv('EMAIL_TO',''))
    if not sender or not password or not recipient:
        raise RuntimeError('SMTP_USER, SMTP_PASSWORD and EMAIL_TO must all be defined as GitHub Actions secrets.')
    mail=build_mail(rows,sources,cfg,today,sender,recipient,(OUT/'job_digest.xlsx').read_bytes())
    with smtplib.SMTP_SSL('smtp.gmail.com',465,timeout=25) as smtp:
        smtp.login(sender,password)
        smtp.send_message(mail)


def run(send_email=False):
    cfg=json.loads((CONFIG/'settings.json').read_text())
    sources=json.loads((CONFIG/'sources.json').read_text())['sources']
    now=datetime.now(timezone.utc)
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
    all_jobs=[]
    for job in fetched:
        published=date_parse(job.get('posted'),now)
        if not published or published < now-timedelta(days=cfg['all_jobs_lookback_days']) or published > now+timedelta(days=1):
            continue
        j=classify(job)
        j['id']=job_key(j)
        reasons=exclusion_reasons(j,cfg,now)
        j['exclusion_reasons']=' | '.join(reasons)
        j['filter_status']='Excluded' if reasons else 'Included'
        all_jobs.append(j)
    duplicate_counts=Counter(j['id'] for j in all_jobs)
    for j in all_jobs:
        j['duplicate_flag']='Yes' if duplicate_counts[j['id']]>1 else 'No'
    all_jobs.sort(key=lambda j:date_parse(j['posted'],now) or datetime(1970,1,1,tzinfo=timezone.utc),reverse=True)

    selected={}
    for j in all_jobs:
        if j['filter_status']=='Excluded':
            continue
        key=j['id']
        existing=selected.get(key)
        # Prefer descriptions and explicit sponsorship evidence when the same job
        # appears in several community feeds.
        quality=len(j['description']) + (200 if j['visa_tag'] not in ('Sponsorship unknown','H-1B history only') else 0)
        if not existing or quality>existing['_quality']:
            j['_quality']=quality
            selected[key]=j
    candidates=list(selected.values())
    candidates.sort(key=lambda j:(j['score'],date_parse(j['posted'],now) or datetime(1970,1,1,tzinfo=timezone.utc)),reverse=True)
    output=candidates
    OUT.mkdir(parents=True,exist_ok=True)
    csv_data=csv_bytes(output)
    (OUT/'jobs.csv').write_bytes(csv_data)
    (OUT/'digest.html').write_text(html_report(output,health,cfg,now,len(output)),encoding='utf-8')
    (OUT/'sources_health.json').write_text(json.dumps(health,indent=2),encoding='utf-8')
    write_workbook(output,all_jobs,health,cfg,now)
    print(f'Filtered 14-day roles={len(output)} all ingested 28-day rows={len(all_jobs)} strong={sum(j["fit"]=="Strong match" for j in output)}')
    if send_email:
        send_mail(output,health,cfg,now)
        print('Email sent successfully.')
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--send-email', action='store_true', help='Send the generated artifact by email (disabled by default)')
    args=parser.parse_args()
    run(send_email=args.send_email)
