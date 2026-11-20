import tempfile
import unittest
from backend.holidays import HolidayService,official_url
from backend import DomainError

class HolidayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.service=HolidayService(self.temp.name)
    def tearDown(self): self.temp.cleanup()
    def days(self,country): return {d['date']:d for d in self.service.calendar(country,2026)['days']}
    def test_belarus_transfer_overrides_saturday(self):
        days=self.days('BY')
        self.assertEqual(days['2026-04-20']['kind'],'transfer')
        self.assertEqual(days['2026-04-25']['kind'],'workday')
        self.assertTrue(days['2026-04-25']['is_working'])
        self.assertEqual(days['2026-04-21']['kind'],'holiday')
    def test_georgia_and_czech_movable_feasts(self):
        self.assertEqual(self.days('GE')['2026-04-10']['kind'],'holiday')
        self.assertEqual(self.days('GE')['2026-05-17']['kind'],'holiday')
        self.assertEqual(self.days('CZ')['2026-04-03']['kind'],'holiday')
        self.assertEqual(self.days('CZ')['2026-04-06']['kind'],'holiday')
    def test_kazakhstan_current_2026_calendar(self):
        days=self.days('KZ')
        self.assertEqual(days['2026-05-27']['kind'],'holiday')
        self.assertEqual(days['2026-03-24']['kind'],'transfer')
        self.assertNotIn('2026-08-31',days)
    def test_unconfirmed_year_is_marked(self):
        self.assertEqual(self.service.calendar('BY',2027)['coverage'],'baseline')
    def test_sources_are_restricted(self):
        for url in ['https://example.com/','http://pravo.by/','https://t.me/untrusted/1']:
            with self.assertRaises(DomainError):official_url(url)
    def test_offline_refresh_retains_reviewed_dates(self):
        def unavailable(url):raise OSError('offline')
        service=HolidayService(self.temp.name,fetcher=unavailable)
        result=service.calendar('BY',2026,refresh=True)
        self.assertEqual(result['coverage'],'cached')
        self.assertTrue(any(d['date']=='2026-04-25' and d['kind']=='workday' for d in result['days']))
