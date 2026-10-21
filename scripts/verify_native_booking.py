"""Accept only a newly submitted, verified order from this native schedule run."""
import json
import os
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path


def verify_native_booking(report_dir, event, run_id, begin=None, duration=None):
    if event != 'schedule' or not run_id:
        raise ValueError('验收失败：必须是 GitHub 原生 schedule 运行')
    reports = []
    for path in Path(report_dir).glob('booking-*.json'):
        reports.extend(json.loads(path.read_text(encoding='utf-8')))
    allowed_seats = {str(seat_id) for seat_id in range(62565, 62897)}
    confirmed = [order for order in reports
                 if order.get('github_run_id') == run_id
                 and order.get('trigger_event') == 'schedule'
                 and order.get('submission_confirmed') is True
                 and order.get('already_existed') is False
                 and str(order.get('seat_id')) in allowed_seats
                 and (begin is None or int(datetime.fromisoformat(order['begin']).timestamp()) == begin)
                 and (duration is None or int(order['duration']) == duration)]
    if not confirmed:
        raise ValueError('验收失败：本次没有确认新建目标座位订单；已有预约或提交结果不确定均不算通过')
    return confirmed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', type=lambda value: datetime.strptime(value, '%Y-%m-%d').date())
    parser.add_argument('--start', type=int, default=7)
    parser.add_argument('--hours', type=int, default=1)
    args = parser.parse_args()
    expected_begin = (int(datetime.combine(args.date, datetime.min.time(),
                     timezone(timedelta(hours=8))).timestamp()) + args.start * 3600
                     if args.date else None)
    orders = verify_native_booking(
        Path(__file__).resolve().parents[1] / '.local',
        os.environ.get('GITHUB_EVENT_NAME'), os.environ.get('GITHUB_RUN_ID'),
        expected_begin, args.hours * 3600 if args.date else None,
    )
    print('原生定时新建预约验收通过：' + json.dumps(orders, ensure_ascii=False))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as stream:
            stream.write('原生 schedule 新建预约并查单确认成功。\n\n')
            for order in orders:
                stream.write(f"- 订单 {order['booking_id']}；{order['room']} {order['seat']}号；{order['begin']}\n")
