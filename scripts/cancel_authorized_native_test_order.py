"""Cancel only the explicitly authorised Thursday test order, in schedule."""
import os

from scripts.native_schedule_guard import validate

ORDER_ID = 22948633
BEGIN = 1792537200  # 2026-10-22 07:00 Asia/Shanghai
SEAT_ID = 62598    # fourth floor, seat 34


def cancel_authorized(client, current):
    validate(os.environ, current)
    if os.environ.get('HDU_CANCEL_TEST_ORDER') != str(ORDER_ID):
        raise ValueError('未授权取消此测试订单')
    path = f'/Seat/Index/bookingInfo?bookingId={ORDER_ID}&LAB_JSON=1'
    payload = client.request('GET', path)
    booking = payload.get('data', {}).get('booking', {})
    if (str(booking.get('id')) != str(ORDER_ID)
            or str(booking.get('user', {}).get('id')) != str(client.uid)
            or int(booking.get('begin_time', 0)) != BEGIN
            or int(booking.get('duration', 0)) != 3600
            or int(booking.get('seat', {}).get('id', 0)) != SEAT_ID):
        raise ValueError('指定测试订单身份、时段或座位不匹配，未取消任何订单')
    status = str(booking.get('status'))
    if status == '4':
        print('授权测试订单已取消，无需再次取消。')
        return
    if status not in {'0', '1', '2', '8'}:
        raise ValueError('指定测试订单状态无法确认，停止处理')
    result = client.request('POST', f'/Seat/Index/cancelBooking?bookingId={ORDER_ID}&LAB_JSON=1')
    if result.get('CODE') != 'ok':
        raise ValueError('取消结果未获明确确认，停止新建预约')
    after = client.request('GET', path).get('data', {}).get('booking', {})
    if str(after.get('id')) != str(ORDER_ID) or str(after.get('status')) != '4':
        raise ValueError('取消后的状态未核实，停止新建预约')
    print('指定测试订单已取消并查单确认；接下来才进行本次原生 schedule 新单测试。')
