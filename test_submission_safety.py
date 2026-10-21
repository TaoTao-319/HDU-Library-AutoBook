"""Fault injection for ambiguous writes: no credentials or school traffic."""
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
import main
from submission_state import SubmissionJournal


class SubmissionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.local = Path(self.temp.name)
        patcher = patch('main.LOCAL', self.local)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = self.client_for_account()
        self.journal = SubmissionJournal(self.local, 123)
        self.data = {'api_time': 1, 'beginTime': 100, 'duration': 200,
                     'is_recommend': 0, 'seatBookers[0]': 123, 'seats[0]': 62581}

    def client_for_account(self):
        client = main.SeatAutoBooker({'max-retry': 5})
        client.uid = 123
        client.allowed_seat_ids = {62581, 62582}
        self.addCleanup(client.close)
        return client

    def detail(self, *, seat=62581, begin=100, duration=200, uid=123, status='0'):
        return {'data': {'booking': {'id': '9', 'status': status, 'user': {'id': uid},
                'begin_time': begin, 'duration': duration, 'space': {'title': '四楼'},
                'seat': {'id': seat, 'title': '17'}}}}

    def order_list(self):
        return [{'id': 9, 'time': 100, 'duration': 200, 'status': '0'}]

    def test_empty_list_after_timeout_blocks_next_post_and_survives_restart(self):
        self.client.request = Mock(side_effect=requests.Timeout())
        self.client.bookings = Mock(return_value=[])
        for _ in range(2):
            with self.assertRaises(main.SubmissionUncertain):
                self.client.book_slot(100, 200, [{'id': 62581}])
        self.assertEqual(self.client.request.call_count, 1)
        restarted = self.client_for_account()
        restarted.bookings = Mock(return_value=[])
        restarted.request = Mock()
        with self.assertRaises(main.SubmissionUncertain):
            restarted.book_slot(100, 200, [{'id': 62582}])
        restarted.request.assert_not_called()
        self.assertEqual(self.journal.load()['state'], 'unknown')

    def test_post_non_json_or_redirect_error_remains_pending(self):
        self.client.request = Mock(side_effect=main.BookingError('API 未返回 JSON'))
        self.client.bookings = Mock(return_value=[])
        with self.assertRaises(main.SubmissionUncertain):
            self.client.book_slot(100, 200, [{'id': 62581}])
        self.assertIsNotNone(self.journal.load())

    def test_server_id_survives_verification_failure_and_restart_only_reads(self):
        self.client.request = Mock(side_effect=[
            {'CODE': 'ok', 'DATA': {'result': 'success', 'bookingId': '9'}}, requests.Timeout()])
        with self.assertRaises(main.SubmissionUncertain):
            self.client.book_slot(100, 200, [{'id': 62581}])
        self.assertEqual(self.journal.load()['booking_id'], 9)
        restarted = self.client_for_account()
        restarted.request = Mock(return_value=self.detail())
        result = restarted.book_slot(100, 200, [{'id': 62582}])
        self.assertEqual(result['outcome'], 'recovered')
        self.assertFalse(result['submission_confirmed'])
        self.assertTrue(all(call.args[0] == 'GET' for call in restarted.request.call_args_list))
        self.assertIsNone(self.journal.load())
        report = json.loads(next(self.local.glob('booking-recovery-*.json')).read_text())
        self.assertIsInstance(report, list)

    def test_recovery_rejects_wrong_floor_owner_status_and_coverage(self):
        for values in ({'seat': 60710}, {'uid': 999}, {'status': '4'},
                       {'begin': 101}, {'duration': 199}):
            with self.subTest(values=values):
                if not self.journal.load():
                    self.journal.start(self.data, [])
                self.client.bookings = Mock(return_value=self.order_list())
                self.client.request = Mock(return_value=self.detail(**values))
                with self.assertRaises(main.SubmissionUncertain):
                    self.client.recover_pending()
                self.assertIsNotNone(self.journal.load())

    def test_covering_detail_can_be_longer_and_baseline_order_is_not_new(self):
        self.journal.start(self.data, ['9'])
        self.client.bookings = Mock(return_value=self.order_list())
        self.client.request = Mock(return_value=self.detail(begin=50, duration=300))
        result = self.client.recover_pending()
        self.assertTrue(result['already_existed'])
        self.assertFalse(result['submission_confirmed'])
        self.assertEqual(result['duration'], 300)
        self.assertIsNone(self.journal.load())

    def test_write_ahead_failure_prevents_post(self):
        self.client.request = Mock()
        with patch('submission_state.atomic_json', side_effect=PermissionError()), \
                self.assertRaises(main.SubmissionUncertain):
            self.client.book_slot(100, 200, [{'id': 62581}])
        self.client.request.assert_not_called()

    def test_corrupt_journal_is_preserved_and_prevents_post(self):
        self.journal.path.write_text('{broken', encoding='utf-8')
        self.client.request = Mock()
        with self.assertRaises(main.SubmissionUncertain):
            self.client.book_slot(100, 200, [{'id': 62581}])
        self.client.request.assert_not_called()
        self.assertEqual(self.journal.path.read_text(), '{broken')

    def test_success_without_id_or_malformed_data_stays_unknown(self):
        for payload in ({'CODE': 'ok', 'DATA': {'result': 'success'}},
                        {'CODE': 'ok', 'DATA': []},
                        {'CODE': 'ok', 'MESSAGE': '稍后刷新查看预约'},
                        {'CODE': 'error', 'MESSAGE': '服务异常，请稍后确认预约结果'}):
            with self.subTest(payload=payload):
                if self.journal.path.exists():
                    self.journal.path.unlink()
                self.client.request = Mock(return_value=payload)
                with self.assertRaises(main.SubmissionUncertain):
                    self.client.book_slot(100, 200, [{'id': 62581}])
                self.assertIsNotNone(self.journal.load())

    def test_pending_recovery_precedes_finished_day_skip(self):
        self.journal.start(self.data, [])
        day = datetime(2026, 10, 21, 20, 16, tzinfo=main.SHANGHAI)
        (self.local / 'daily-state-2026-10-21.json').write_text(
            json.dumps({'attempted': [], 'finished': 'old completion'}))
        self.client.bookings = Mock(return_value=[])
        self.client.login = Mock()
        config = {'自动预约': {'运行时间': ['20:00:00', '20:00:30', '20:01:30', '20:04:00', '20:10:00']}}
        with patch('main.now', return_value=day), self.assertRaises(main.SubmissionUncertain):
            main.run_daily(self.client, Mock(), {}, [], day.date(), {}, config)
        self.client.login.assert_called_once()
        self.client.bookings.assert_called_once()

    def test_normal_success_requires_exact_time_and_allowed_seat(self):
        self.client.request = Mock(side_effect=[
            {'CODE': 'ok', 'DATA': {'result': 'success', 'bookingId': '9'}},
            self.detail(seat=60710)])
        with self.assertRaises(main.SubmissionUncertain):
            self.client.book_slot(100, 200, [{'id': 62581}])
        self.assertEqual(self.journal.load()['state'], 'server_accepted')

    def test_verified_hint_survives_clear_and_restart_when_list_is_delayed(self):
        intent = self.journal.start(self.data, [])
        self.journal.accepted(intent, 9)
        self.client.request = Mock(return_value=self.detail())
        self.client.recover_pending()
        self.assertIsNone(self.journal.load())
        restarted = self.client_for_account()
        restarted.request = Mock(return_value=self.detail())
        restarted.bookings = Mock(return_value=[])
        restarted.book_fourth_floor = Mock()
        target = datetime.fromtimestamp(100, main.SHANGHAI)
        midnight = target.replace(hour=0, minute=0, second=0, microsecond=0)
        start = (target - midnight).total_seconds() / 3600
        config = {'自定义': {'房间': '四楼', '优先级': [{'名称': '测试', '区间': [[62581, 62582]]}]}}
        main.reserve_day(restarted, Mock(check=False), {'name': '自定义'}, [(start, 200 / 3600)],
                         target.date(), None, {}, config)
        restarted.book_fourth_floor.assert_not_called()
        self.assertTrue(all(call.args[0] == 'GET' for call in restarted.request.call_args_list))

    def test_archive_failure_preserves_pending_and_blocks_later_post(self):
        self.client.request = Mock(side_effect=[
            {'CODE': 'ok', 'DATA': {'result': 'success', 'bookingId': '9'}}, self.detail()])
        original = main.atomic_json
        def fail_verified(path, value):
            if Path(path).name.startswith('verified-'):
                raise PermissionError()
            original(path, value)
        with patch('submission_state.atomic_json', side_effect=fail_verified), \
                self.assertRaises(main.SubmissionUncertain):
            self.client.book_slot(100, 200, [{'id': 62581}])
        self.assertEqual(self.journal.load()['booking_id'], 9)


if __name__ == '__main__':
    unittest.main()
