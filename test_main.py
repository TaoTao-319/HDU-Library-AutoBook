"""Offline regressions: no credentials, browsers, or real bookings."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
import main
from scripts.verify_native_booking import verify_native_booking


class BookingTests(unittest.TestCase):
    def test_utf8_config_independent_of_system_encoding(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.yml'
            path.write_text('周三:\n  启用: false\n', encoding='utf-8-sig')
            self.assertFalse(main.read_yaml(path)['周三']['启用'])

    def test_slots_reject_ambiguous_invalid_overlapping_values(self):
        cases = [
            {'时间段': [], '开始时间': 7}, {'时间段': []},
            {'开始时间': 7, '持续小时数': 0}, {'开始时间': 23, '持续小时数': 2},
            {'开始时间': float('nan'), '持续小时数': 1},
            {'开始时间': True, '持续小时数': 1},
            {'时间段': [{'开始时间': 7, '持续小时数': 2}, {'开始时间': 8, '持续小时数': 1}]},
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                main.get_time_slots(case)
        self.assertEqual(main.get_time_slots({'时间段': [
            {'开始时间': 8, '持续小时数': 1}, {'开始时间': 7, '持续小时数': 1}]}), [(7, 1), (8, 1)])

    def test_timestamp_uses_china_timezone(self):
        value = datetime(2026, 10, 21, 7, tzinfo=main.SHANGHAI)
        self.assertEqual(int(value.timestamp()), 1792537200)

    def test_token_matches_javascript_client(self):
        # Independent JS fixture, captured/calculated from the official canonical string.
        data = {'api_time': 1792413500, 'beginTime': 1792537200, 'duration': 3600,
                'is_recommend': 0, 'seatBookers[0]': 338976, 'seats[0]': 61355}
        self.assertEqual(main.booking_token(data), 'ZWVhNWYxOTAwOTI4M2QzY2UyZWQ1MmQxZDAzNzQyNzQ=')

    def test_cancelled_booking_does_not_prevent_retry(self):
        cancelled = {'id': 1, 'time': 100, 'duration': 100, 'status': '4'}
        self.assertIsNone(main.SeatAutoBooker.covered_booking([cancelled], 100, 100))
        active = dict(cancelled, status='0')
        self.assertEqual(main.SeatAutoBooker.covered_booking([active], 110, 50)['id'], 1)
        with self.assertRaises(main.BookingError):
            main.SeatAutoBooker.covered_booking([active], 150, 100)

    def client(self):
        client = main.SeatAutoBooker({'max-retry': 5})
        self.addCleanup(client.close)
        client.uid = 123
        return client

    def test_http_ok_with_business_failure_is_failure(self):
        client = self.client()
        client.request = Mock(return_value={'CODE': 'ok', 'MESSAGE': '请求成功',
                                           'DATA': {'result': 'failure', 'msg': '预约时长不合法'}})
        with self.assertRaisesRegex(main.BookingError, '时长不合法'):
            client.book_slot(100, 100, [{'id': 1, 'number': '1'}])

    def test_rate_limit_stops_without_hammering(self):
        client = self.client()
        client.request = Mock(return_value={'CODE': 'error', 'MESSAGE': '操作频繁'})
        with self.assertRaisesRegex(main.BookingError, '限流'):
            client.book_slot(100, 100, [{'id': x, 'number': str(x)} for x in range(5)])
        self.assertEqual(client.request.call_count, 1)

    def test_taken_seat_switches_and_success_is_verified(self):
        client = self.client()
        client.request = Mock(side_effect=[{'CODE': 'error', 'MESSAGE': '座位已被占用'},
            {'CODE': 'ok', 'DATA': {'result': 'success', 'bookingId': '9'}}])
        client.verify = Mock(return_value={'booking_id': '9'})
        with patch('main.time.sleep'):
            self.assertEqual(client.book_slot(100, 200, [{'id': 1, 'number': '1'},
                             {'id': 2, 'number': '2'}]), {'booking_id': '9', 'submission_confirmed': True})
        client.verify.assert_called_once_with('9', 100, 200, 2)

    def test_timeout_queries_before_any_second_post(self):
        client = self.client()
        client.request = Mock(side_effect=requests.Timeout())
        client.bookings = Mock(return_value=[{'id': 9, 'time': 100, 'duration': 200, 'status': '0'}])
        client.verify = Mock(return_value={'booking_id': '9'})
        self.assertEqual(client.book_slot(100, 200, [{'id': 1}]), {'booking_id': '9'})
        self.assertEqual(client.request.call_count, 1)

    def test_native_acceptance_rejects_manual_duplicate_and_uncertain_orders(self):
        confirmed = {'github_run_id': '77', 'trigger_event': 'schedule',
                     'submission_confirmed': True, 'already_existed': False, 'seat_id': '62599'}
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / 'booking-test.json'
            report.write_text(json.dumps([confirmed]), encoding='utf-8')
            self.assertEqual(verify_native_booking(folder, 'schedule', '77'), [confirmed])
            for event, run_id in [('workflow_dispatch', '77'), ('push', '77'), ('schedule', '78')]:
                with self.subTest(event=event, run_id=run_id), self.assertRaises(ValueError):
                    verify_native_booking(folder, event, run_id)
            for changes in [{'already_existed': True}, {'submission_confirmed': False},
                            {'seat_id': 62598}, {'seat_id': 62623}, {'trigger_event': 'workflow_dispatch'}]:
                report.write_text(json.dumps([dict(confirmed, **changes)]), encoding='utf-8')
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    verify_native_booking(folder, 'schedule', '77')

    def test_search_uses_form_category_and_ignores_non_map_blocks(self):
        client = self.client()
        client.categories = {'自习室': '/Seat/Index/searchSeats?space_category%5Bcategory_id%5D=591&space_category%5Bcontent_id%5D=3'}
        client.request = Mock(return_value={'content': [{'seatMap': ''}, {'seatMap': {
            'info': {'id': '1524', 'title': '二楼西'}, 'POIs': [
                {'id': '1', 'title': '1', 'state': 0}, {'id': '2', 'title': '2', 'state': '1'},
                {'id': '3', 'title': '3', 'state': 2, 'recommend': True}]}}]})
        seats = client.available_seats('二楼西', 100, 200, {'二楼西': {'type': '自习室', 'room_id': 1524}}, [])
        self.assertEqual({seat['id'] for seat in seats}, {1, 3})
        self.assertEqual(client.request.call_args.kwargs['data']['space_category[content_id]'], '3')
        self.assertEqual(client.request.call_args.args[1], '/Seat/Index/searchSeats?LAB_JSON=1')

    def test_verification_rejects_wrong_owner_or_time(self):
        client = self.client()
        client.request = Mock(return_value={'data': {'booking': {'id': '9', 'status': '0',
                                       'user': {'id': '999'}, 'begin_time': '100'}}})
        with self.assertRaises(main.BookingError):
            client.verify(9, 100)

    def test_monotonic_wait_cannot_submit_early(self):
        elapsed = [0.0]
        sleeps = []
        def sleep(seconds):
            sleeps.append(seconds)
            elapsed[0] += max(seconds, 0.00001)
        target = datetime.fromtimestamp(1001, main.SHANGHAI)
        main.wait_until(target, wall=lambda: 1000, monotonic=lambda: elapsed[0], sleep=sleep)
        self.assertGreaterEqual(elapsed[0], 1)
        self.assertTrue(all(0 < value <= 1 for value in sleeps))

    def test_cloud_only_blocks_local_before_login(self):
        configs = [{}, {'SeatAutoBooker': {'cloud-only': True}}]
        with patch('main.read_yaml', side_effect=configs), patch('main.os.environ', {}), \
                patch('main.SeatAutoBooker') as client, self.assertLogs('hdu', level='INFO') as logs:
            self.assertEqual(main.run(Mock()), 0)
            client.assert_not_called()
        self.assertTrue(any('本机执行已停用' in line for line in logs.output))

    def test_cloud_only_allows_actions_to_read_day_configuration(self):
        args = Mock(date=datetime(2026, 10, 24).date(), at=None, check=False, test_booking=False)
        configs = [{'周六': {'启用': False}}, {'SeatAutoBooker': {'cloud-only': True}}, {}]
        with patch('main.read_yaml', side_effect=configs), patch('main.os.environ', {'GITHUB_ACTIONS': 'true'}), \
                self.assertLogs('hdu', level='INFO') as logs:
            self.assertEqual(main.run(args), 0)
        self.assertTrue(any('执行环境：GitHub Actions 云端' in line for line in logs.output))
        self.assertTrue(any('周六 预约未启用' in line for line in logs.output))

    def test_disabled_day_never_logs_in(self):
        args = Mock(date=datetime(2026, 10, 21).date(), at='21:40', check=False, test_booking=False)
        configs = [{'周三': {'启用': False}}, {'SeatAutoBooker': {}}, {}]
        with patch('main.read_yaml', side_effect=configs), patch('main.SeatAutoBooker') as client:
            self.assertEqual(main.run(args), 0)
            client.assert_not_called()

    def test_scheduled_flow_warms_connection_and_submits_at_target(self):
        target = datetime(2026, 10, 19, 21, 40, tzinfo=main.SHANGHAI)
        clock = [target - timedelta(seconds=60)]
        events = []
        args = Mock(date=datetime(2026, 10, 21).date(), at='21:40', check=False, test_booking=False)
        configs = [{'周三': {'启用': True, 'name': '二楼西', '开始时间': 7, '持续小时数': 1}},
                   {'SeatAutoBooker': {}}, {}]
        client = Mock()
        client.bookings.return_value = []
        client.covered_booking.return_value = None
        client.available_seats.return_value = [{'id': 1, 'number': '1'}]
        def wait(value):
            clock[0] = max(clock[0], value)
            events.append(('wait', value))
        client.get_user_info.side_effect = lambda: events.append(('warm', clock[0]))
        def submit(*args):
            events.append(('submit', clock[0]))
            return {'room': '二楼西', 'seat': '1', 'begin': '2026-10-21T07:00:00+08:00', 'booking_id': '9'}
        client.book_slot.side_effect = submit
        with tempfile.TemporaryDirectory() as folder, patch('main.LOCAL', Path(folder)), \
                patch('main.now', side_effect=lambda: clock[0]), patch('main.read_yaml', side_effect=configs), \
                patch('main.SeatAutoBooker', return_value=client), patch('main.wait_until', side_effect=wait):
            self.assertEqual(main.run(args), 0)
        self.assertEqual(events, [('wait', target - timedelta(seconds=2)),
                                 ('warm', target - timedelta(seconds=2)),
                                 ('wait', target), ('submit', target)])


if __name__ == '__main__':
    unittest.main()
