"""Offline guard/acceptance checks; never trigger GitHub or school actions."""
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
import copy
from unittest.mock import Mock, patch

from scripts.native_schedule_guard import validate, SHANGHAI, DEADLINE
from scripts.verify_native_booking import verify_native_booking
from scripts.cancel_authorized_native_test_order import cancel_authorized, BEGIN, ORDER_ID, SEAT_ID


class NativeScheduleTests(unittest.TestCase):
    def setUp(self):
        self.env = {'GITHUB_ACTIONS': 'true', 'GITHUB_EVENT_NAME': 'schedule',
                    'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_REPOSITORY': 'TaoTao-319/HDU-Library-AutoBook',
                    'GITHUB_SHA': 'a' * 40, 'HDU_CANCEL_TEST_ORDER': str(ORDER_ID)}
        self.current = datetime(2026, 10, 21, 21, 20, tzinfo=SHANGHAI)
        self.booking = {'id': ORDER_ID, 'user': {'id': 123}, 'begin_time': BEGIN,
                        'duration': 3600, 'seat': {'id': SEAT_ID}, 'status': '0'}

    def test_cancel_matches_only_the_authorised_order_and_confirms_inactive_status(self):
        client = Mock(uid=123)
        client.request.side_effect = [{'data': {'booking': self.booking}}, {'CODE': 'ok'},
                                     {'data': {'booking': dict(self.booking, status='4')}}]
        with patch.dict('os.environ', self.env, clear=True):
            cancel_authorized(client, self.current)
        self.assertEqual([call.args[0] for call in client.request.call_args_list], ['GET', 'POST', 'GET'])
        self.assertIn('bookingId=22948633', client.request.call_args_list[1].args[1])

    def test_cancel_never_posts_if_owner_time_or_seat_is_wrong(self):
        for change in ({'user': {'id': 999}}, {'begin_time': BEGIN + 3600},
                       {'duration': 7200}, {'seat': {'id': SEAT_ID + 1}}, {'id': ORDER_ID + 1}):
            client = Mock(uid=123)
            client.request.return_value = {'data': {'booking': dict(copy.deepcopy(self.booking), **change)}}
            with patch.dict('os.environ', self.env, clear=True), self.assertRaises(ValueError):
                cancel_authorized(client, self.current)
            self.assertEqual(client.request.call_count, 1)

    def test_cancel_skips_an_already_cancelled_order(self):
        client = Mock(uid=123)
        client.request.return_value = {'data': {'booking': dict(self.booking, status='4')}}
        with patch.dict('os.environ', self.env, clear=True):
            cancel_authorized(client, self.current)
        self.assertEqual(client.request.call_count, 1)

    def test_cancel_ambiguous_response_stops_before_rebooking(self):
        client = Mock(uid=123)
        client.request.side_effect = [{'data': {'booking': self.booking}}, {'CODE': 'error'}]
        with patch.dict('os.environ', self.env, clear=True), self.assertRaises(ValueError):
            cancel_authorized(client, self.current)
        self.assertEqual(client.request.call_count, 2)

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
