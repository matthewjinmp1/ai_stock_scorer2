import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from ibkr_schedule import next_basket_schedule

class ScheduleTests(unittest.TestCase):
    def test_next_session_holidays_and_dst(self):
        cases = [
            ('2026-09-26T12:00:00', '2026-09-28T10:30:00-04:00'),
            ('2026-09-28T08:00:00', '2026-09-29T10:30:00-04:00'),
            ('2026-04-02T12:00:00', '2026-04-06T10:30:00-04:00'),
            ('2026-12-24T12:00:00', '2026-12-28T10:30:00-05:00'),
            ('2026-03-06T12:00:00', '2026-03-09T10:30:00-04:00'),
            ('2026-10-30T12:00:00', '2026-11-02T10:30:00-05:00'),
            ('2026-12-31T12:00:00', '2027-01-04T10:30:00-05:00'),
        ]
        for now, expected in cases:
            with self.subTest(now=now):
                result = next_basket_schedule(datetime.fromisoformat(now).replace(tzinfo=ZoneInfo('America/New_York')))
                self.assertEqual(result['scheduledAt'], expected)
                self.assertTrue(result['goodAfter'].endswith('10:30:00 US/Eastern'))
