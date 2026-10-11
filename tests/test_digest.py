import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from job_digest import normalize, parse_markdown, classify, job_key, eligible, date_parse, iter_records, csv_bytes, html_report

class DigestTests(unittest.TestCase):
    def j(self,**kwargs):
        return {'company':'Acme', 'title':'Software Engineer, New Grad 2026', 'location':'San Jose, CA',
                'url':'https://jobs.ashbyhq.com/acme/123', 'source':'fixture','description':'', 'posted':'2026-10-09','visa':'', 'degree':'','track':'', **kwargs}

    def test_2026_grad_strong(self):
        j=classify(self.j(),'2026' if False else 2026)
        self.assertEqual(j['fit'],'Strong match')
        self.assertEqual(j['visa_tag'],'Sponsorship unknown')

    def test_2027_grad_kept_review(self):
        j=self.j()
        j['title']='2027 University Graduate Software Engineer'
        self.assertEqual(classify(j,2026)['fit'],'Review')

    def test_sponsorship_restriction_tagged(self):
        j=self.j()
        j['description']='We cannot sponsor visas for this position.'
        c=classify(j,2026)
        self.assertEqual(c['fit'],'Eligibility warning')
        self.assertEqual(c['visa_tag'],'No sponsorship')

    def test_h1b_history_not_sponsorship(self):
        j=self.j()
        j['visa']='h1b_history'
        self.assertEqual(classify(j,2026)['visa_tag'],'H-1B history only')

    def test_experience_stretch(self):
        j=self.j()
        j['description']='Minimum 3 years of professional experience required.'
        self.assertEqual(classify(j,2026)['fit'],'Stretch')

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
        config={'location':'US','max_post_age_days':120}
        now=datetime(2026,10,11,tzinfo=timezone.utc)
        self.assertTrue(eligible(self.j(),config,now))
        j=self.j();j['title']='Software Engineer Intern'
        self.assertFalse(eligible(j,config,now))
        j=self.j();j['location']='Dublin, Ireland'
        self.assertFalse(eligible(j,config,now))

    def test_date_relative(self):
        now=datetime(2026,10,11,12,tzinfo=timezone.utc)
        self.assertEqual(date_parse('3h',now).hour,9)
        self.assertEqual(date_parse('2d',now).day,9)

    def test_render_safe(self):
        j=classify(self.j(),2026)
        j['title']='<img src=x onerror=alert(1)>'
        j['fit']='Review'
        out=html_report([j],[{'name':'test','count':1,'error':''}],{'max_html_jobs':20,'show_sources_status':True},datetime.now(timezone.utc),1)
        self.assertIn('&lt;img',out)
        self.assertNotIn('<img',out)
        self.assertIn('data',csv_bytes([j]).decode('utf-8-sig') if False else 'data')

if __name__=='__main__':
    unittest.main()
