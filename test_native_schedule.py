"""Offline guard/acceptance checks; never trigger GitHub or school actions."""
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest

from scripts.native_schedule_guard import validate, SHANGHAI, DEADLINE
from scripts.verify_native_booking import verify_native_booking


class NativeScheduleTests(unittest.TestCase):
    def test_guard_accepts_only_first_native_run_in_the_fixed_window(self):
        env = {'GITHUB_ACTIONS': 'true', 'GITHUB_EVENT_NAME': 'schedule',
               'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_REPOSITORY': 'TaoTao-319/HDU-Library-AutoBook',
               'GITHUB_SHA': 'a' * 40}
        current = datetime(2026, 10, 21, 21, 20, tzinfo=SHANGHAI)
        validate(env, current)
        for change in ({'GITHUB_EVENT_NAME': 'workflow_dispatch'}, {'GITHUB_EVENT_NAME': 'push'},
                       {'GITHUB_ACTIONS': 'false'}, {'GITHUB_RUN_ATTEMPT': '2'},
                       {'GITHUB_REPOSITORY': 'other/repo'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate(dict(env, **change), current)
        with self.assertRaises(ValueError):
            validate(env, DEADLINE)

    def test_new_order_and_exact_target_must_match_the_same_record(self):
        begin = int(datetime(2026, 10, 22, 7, tzinfo=SHANGHAI).timestamp())
        record = {'github_run_id': '77', 'trigger_event': 'schedule', 'submission_confirmed': True,
                  'already_existed': False, 'seat_id': '62581',
                  'begin': '2026-10-22T07:00:00+08:00', 'duration': 3600}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'booking-test.json'
            path.write_text(json.dumps([record]), encoding='utf-8')
            self.assertEqual(verify_native_booking(folder, 'schedule', '77', begin, 3600), [record])
            path.write_text(json.dumps([dict(record, begin='2026-10-22T08:00:00+08:00'),
                                        dict(record, already_existed=True)]), encoding='utf-8')
            with self.assertRaises(ValueError):
                verify_native_booking(folder, 'schedule', '77', begin, 3600)


if __name__ == '__main__':
    unittest.main()
