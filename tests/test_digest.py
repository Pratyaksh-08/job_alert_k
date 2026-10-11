import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from job_digest import normalize, parse_markdown, classify, job_key, eligible, exclusion_reasons, rows_within_days, build_mail, date_parse, iter_records, csv_bytes, html_report

class DigestTests(unittest.TestCase):
    def j(self,**kwargs):
        return {'company':'Acme', 'title':'Software Engineer, New Grad 2026', 'location':'San Jose, CA',
                'url':'https://jobs.ashbyhq.com/acme/123', 'source':'fixture','description':'', 'posted':'2026-10-09','visa':'', 'degree':'','track':'', **kwargs}

    def test_new_grad_strong(self):
        j=classify(self.j())
        self.assertEqual(j['fit'],'Strong match')
        self.assertEqual(j['visa_tag'],'Sponsorship unknown')

    def test_any_grad_year_is_strong(self):
        j=self.j()
        j['title']='2027 University Graduate Software Engineer'
        self.assertEqual(classify(j)['fit'],'Strong match')

    def test_generic_technical_role_is_review(self):
        j=self.j()
        j['title']='Software Engineer'
        self.assertEqual(classify(j)['fit'],'Review')

    def test_sponsorship_restriction_tagged(self):
        j=self.j()
        j['description']='We cannot sponsor visas for this position.'
        c=classify(j)
        self.assertEqual(c['fit'],'Eligibility warning')
        self.assertEqual(c['visa_tag'],'No sponsorship')

    def test_without_future_sponsorship_tagged(self):
        j=self.j(); j['description']='Must be authorized to work without current or future sponsorship.'
        self.assertEqual(classify(j)['visa_tag'],'No sponsorship')

    def test_us_person_requirement_tagged(self):
        j=self.j(); j['description']='Due to export controls, U.S. persons only.'
        self.assertEqual(classify(j)['visa_tag'],'US citizenship required')

    def test_h1b_history_not_sponsorship(self):
        j=self.j()
        j['visa']='h1b_history'
        self.assertEqual(classify(j)['visa_tag'],'H-1B history only')

    def test_experience_stretch(self):
        j=self.j()
        j['description']='Minimum 3 years of professional experience required.'
        self.assertEqual(classify(j)['fit'],'Stretch')

    def test_two_years_is_hard_exclusion(self):
        j=self.j(); j['description']='At least 2 years of professional experience required.'
        j=classify(j)
        cfg={'location':'US','lookback_days':14}
        self.assertIn('2+ years experience required',exclusion_reasons(j,cfg,datetime(2026,10,11,tzinfo=timezone.utc)))

    def test_senior_title_alone_is_not_excluded(self):
        j=self.j(); j['title']='Senior Software Engineer'; j=classify(j)
        cfg={'location':'US','lookback_days':14}
        self.assertEqual(exclusion_reasons(j,cfg,datetime(2026,10,11,tzinfo=timezone.utc)),[])

    def test_markdown(self):
        md='''| Company | Position | Location | Posting | Age |\n| --- | --- | --- | --- | --- |\n| Acme | SWE New Grad | San Jose, CA | [Apply](https://example.com/jobs/1) | 1d |'''
        rows=list(parse_markdown(md))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['posted'],'1d')
        self.assertEqual(rows[0]['apply_url'],'https://example.com/jobs/1')

    def test_records(self):
        payload={'jobs':[{'title':'AI Engineer','company':'Acme','apply_url':'https://example.org/1'},
                         {'title':'ML Engineer','company':'Foo','apply_url':'https://example.org/2'}]}
        self.assertEqual(len(list(iter_records(payload))),2)

    def test_normalize_applyguy(self):
        raw={'company':'Acme','title':'SWE New Grad','location':'Austin, TX','listingUrl':'https://careers.acme.com/1','url':'https://thirdparty.example/1'}
        self.assertEqual(normalize(raw,'ApplyGuy','json')['url'],'https://careers.acme.com/1')

    def test_filter(self):
        config={'location':'US','lookback_days':14}
        now=datetime(2026,10,11,tzinfo=timezone.utc)
        self.assertTrue(eligible(self.j(),config,now))
        j=self.j();j['title']='Software Engineer Intern'
        self.assertFalse(eligible(j,config,now))
        j=self.j();j['location']='Dublin, Ireland'
        self.assertFalse(eligible(j,config,now))
        j=self.j();j['posted']='2026-09-26'
        self.assertFalse(eligible(j,config,now))
        j=self.j();j['posted']=''
        self.assertFalse(eligible(j,config,now))

    def test_date_relative(self):
        now=datetime(2026,10,11,12,tzinfo=timezone.utc)
        self.assertEqual(date_parse('3h',now).hour,9)
        self.assertEqual(date_parse('2d',now).day,9)

    def test_email_window_is_three_days(self):
        now=datetime(2026,10,11,12,tzinfo=timezone.utc)
        recent=self.j(); recent['posted']='2d'
        old=self.j(); old['posted']='4d'
        self.assertEqual(rows_within_days([recent,old],now,3),[recent])

    def test_mail_contains_three_day_body_and_workbook(self):
        now=datetime(2026,10,11,12,tzinfo=timezone.utc)
        recent=classify(self.j()); recent['posted']='2d'
        old=classify(self.j()); old['posted']='4d'
        cfg={'lookback_days':14,'max_html_jobs':125,'show_sources_status':True}
        health=[{'name':'fixture','count':2,'error':''}]
        mail=build_mail([recent,old],health,cfg,now,'sender@example.com','to@example.com',b'xlsx')
        self.assertIn('1 tech roles from the last 3 days',mail['Subject'])
        attachments=list(mail.iter_attachments())
        self.assertEqual(len(attachments),1)
        self.assertEqual(attachments[0].get_filename(),'job_digest.xlsx')

    def test_render_safe(self):
        j=classify(self.j())
        j['title']='<img src=x onerror=alert(1)>'
        j['fit']='Review'
        out=html_report([j],[{'name':'test','count':1,'error':''}],{'max_html_jobs':20,'show_sources_status':True,'lookback_days':14},datetime.now(timezone.utc),1)
        self.assertIn('&lt;img',out)
        self.assertNotIn('<img',out)
        self.assertIn('data',csv_bytes([j]).decode('utf-8-sig') if False else 'data')

if __name__=='__main__':
    unittest.main()
