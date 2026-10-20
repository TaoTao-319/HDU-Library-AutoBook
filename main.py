"""HDU seat booking. All scheduling and booking dates use Asia/Shanghai."""
import argparse
import base64
from contextlib import contextmanager
from collections import deque
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import random
import subprocess
import sys
import time
from urllib.parse import urlsplit, parse_qsl

import requests
import yaml
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

ROOT = Path(__file__).resolve().parent
LOCAL = ROOT / '.local'
SHANGHAI = timezone(timedelta(hours=8), 'Asia/Shanghai')
BASE_URL = 'https://hdu.huitu.zhishulib.com'
BOOK_PATH = '/Seat/Index/bookSeats?LAB_JSON=1'
WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
ACTIVE_STATUSES = {'0', '1', '2', '8'}
LOG = logging.getLogger('hdu')


class BookingError(RuntimeError):
    pass


class AlreadyRunning(BookingError):
    pass


def now():
    return datetime.now(SHANGHAI)


def log_time(timestamp):
    return datetime.fromtimestamp(timestamp, SHANGHAI).timetuple()


def read_yaml(path):
    with Path(path).open(encoding='utf-8-sig') as stream:
        return yaml.safe_load(stream)


def get_time_slots(config):
    if '时间段' in config:
        if '开始时间' in config or '持续小时数' in config:
            raise ValueError('时间段与开始时间/持续小时数不能同时配置')
        if not config['时间段']:
            raise ValueError('时间段不能为空')
        slots = [(x['开始时间'], x['持续小时数']) for x in config['时间段']]
    else:
        slots = [(config['开始时间'], config['持续小时数'])]
    for start, hours in slots:
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
               for x in (start, hours)):
            raise ValueError('开始时间和持续小时数必须是有限数字')
        if start < 0 or hours <= 0 or start + hours > 24:
            raise ValueError('时段必须位于同一天内且持续时间大于零')
        if start * 3600 % 1 or hours * 3600 % 1:
            raise ValueError('时间精度最多到秒')
    slots = sorted(slots)
    for (start, hours), (following, _) in zip(slots, slots[1:]):
        if following < start + hours:
            raise ValueError('时间段不能重合')
    return slots


def booking_token(data):
    # Match the official web client's ordered MD5 hex -> base64 signature.
    order = ['api_time', 'beginTime', 'duration', 'is_recommend', 'seatBookers[0]', 'seats[0]']
    canonical = 'post&' + BOOK_PATH + '&' + '&'.join(k + str(data[k]) for k in order)
    return base64.b64encode(hashlib.md5(canonical.encode()).hexdigest().encode()).decode()


def credentials():
    username, password = os.environ.get('SCHOOL_ID'), os.environ.get('PASSWORD')
    if username and password:
        return username.strip(), password
    path = LOCAL / 'credentials.xml'
    if os.name == 'nt' and path.exists():
        command = ("[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
                   "$c=Import-Clixml -LiteralPath $env:HDU_CREDENTIAL_FILE; "
                   "@{SCHOOL_ID=$c.UserName;PASSWORD=$c.GetNetworkCredential().Password} | ConvertTo-Json -Compress")
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                env=dict(os.environ, HDU_CREDENTIAL_FILE=str(path)),
                                capture_output=True, timeout=15, check=False)
        if result.returncode == 0:
            value = json.loads(result.stdout.decode('utf-8-sig'))
            return value['SCHOOL_ID'], value['PASSWORD']
        raise BookingError('无法解密本机凭据，请重新运行 Save-Credential.ps1')
    raise BookingError('请设置 SCHOOL_ID、PASSWORD 环境变量或运行 Save-Credential.ps1')


def walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def configured_seats(region, seat_config, custom):
    if region == '自定义':
        ids = list(dict.fromkeys(int(x) for x in custom))
    else:
        names = [region] if region != '不限' else [
            name for name, cfg in seat_config.items() if cfg.get('type') == '自习室' and 'ranges' in cfg]
        ids = list(dict.fromkeys(seat for name in names
                   for start, end in seat_config[name]['ranges'] for seat in range(start, end + 1)))
        random.shuffle(ids)
    if not ids:
        raise BookingError('配置内没有候选座位')
    return [{'id': seat, 'number': str(seat), 'room': region} for seat in ids]


def wait_until(target, wall=time.time, monotonic=time.perf_counter, sleep=time.sleep):
    deadline = monotonic() + target.timestamp() - wall()
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            # Recheck the wall clock too: the two initial clock reads can differ.
            if wall() >= target.timestamp():
                return
            deadline = monotonic() + target.timestamp() - wall()
            continue
        sleep(min(remaining / 2, 0.01) if remaining < 0.1 else min(remaining - 0.05, 1.0))


@contextmanager
def single_instance():
    LOCAL.mkdir(exist_ok=True)
    lock = (LOCAL / 'run.lock').open('a+b')
    try:
        # Windows byte locks also prohibit reads by the watchdog process.
        if os.fstat(lock.fileno()).st_size == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        raise AlreadyRunning('已有预约进程在运行，备用任务正常跳过') from None
    try:
        yield
    finally:
        lock.close()


class SeatAutoBooker:
    def __init__(self, config):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'application/json, text/plain, */*',
            'User-Agent': 'Mozilla/5.0 Chrome/130.0.0.0 Safari/537.36',
            'Origin': BASE_URL, 'Referer': BASE_URL + '/Space/Category/list',
        })
        self.uid = None
        self.categories = {}

    def close(self):
        self.session.close()

    def request(self, method, path, **kwargs):
        if not path.startswith('/') or path.startswith('//'):
            raise BookingError('API 路径无效')
        started = time.perf_counter()
        kwargs.setdefault('timeout', (5, 15))
        response = self.session.request(method, BASE_URL + path, **kwargs)
        elapsed = (time.perf_counter() - started) * 1000
        response.raise_for_status()
        if urlsplit(response.url).hostname != urlsplit(BASE_URL).hostname:
            raise BookingError('会话失效，API 已跳转到登录页')
        try:
            payload = response.json()
        except ValueError:
            raise BookingError('API 未返回 JSON，可能是会话失效或服务异常') from None
        if not isinstance(payload, dict):
            raise BookingError('API 响应结构不正确')
        LOG.info('%s %s 耗时 %.1f ms', method, path.split('?')[0], elapsed)
        return payload

    def login(self, load_categories=True):
        username, password = credentials()
        options = Options()
        if os.environ.get('HDU_SHOW_BROWSER') != '1':
            options.add_argument('--headless=new')
        options.add_argument('--window-size=1440,1000')
        options.add_argument('--disable-dev-shm-usage')
        options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
        if os.name != 'nt':
            options.add_argument('--no-sandbox')
        if os.environ.get('CHROME_BINARY'):
            options.binary_location = os.environ['CHROME_BINARY']
        driver_path = os.environ.get('CHROMEDRIVER_PATH')
        driver = webdriver.Chrome(options=options, service=Service(driver_path) if driver_path else Service())
        driver.set_page_load_timeout(45)
        stage = '打开图书馆页面'
        try:
            LOG.info('开始学校统一身份认证')
            driver.get(BASE_URL + '/')
            stage = '等待学校登录表单'
            LOG.info('已打开登录入口，当前域名 %s', urlsplit(driver.current_url).hostname)
            wait = WebDriverWait(driver, 60, 0.1)
            user = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR,
                            "input[name='username']:not([type='hidden'])")))
            LOG.info('学校登录表单已就绪')
            for button in driver.find_elements(By.CSS_SELECTOR, 'img.icon-close, .ant-modal-close'):
                if button.is_displayed() and button.is_enabled():
                    button.click()
            user.clear()
            user.send_keys(username)
            pwd = driver.find_element(By.CSS_SELECTOR, 'input[type=password]')
            pwd.clear()
            pwd.send_keys(password)
            pwd.send_keys(Keys.TAB)
            stage = '等待登录按钮启用'
            wait.until(lambda d: 'disabled' not in d.find_element(
                By.CSS_SELECTOR, 'button.login-button').get_attribute('class'))
            driver.find_element(By.CSS_SELECTOR, 'button.login-button').click()
            stage = '等待认证完成并返回图书馆'
            LOG.info('已提交学校登录表单，等待返回图书馆')

            def authenticated(d):
                if urlsplit(d.current_url).hostname == urlsplit(BASE_URL).hostname:
                    return True
                for message in d.find_elements(By.CSS_SELECTOR, '.ant-message-error, .error-message, .ant-alert-error'):
                    if message.is_displayed() and message.text:
                        raise BookingError('统一身份认证失败：' + message.text[:150])
                return False

            wait.until(authenticated)
            for cookie in driver.get_cookies():
                self.session.cookies.set(cookie['name'], cookie['value'],
                                         domain=cookie['domain'], path=cookie['path'])
        except TimeoutException:
            try:
                location = urlsplit(driver.current_url)
                text = driver.find_element(By.TAG_NAME, 'body').text[-1800:]
                for secret in (username, password):
                    if secret:
                        text = text.replace(secret, '[已隐藏]')
                LOG.error('登录超时阶段：%s；页面：%s%s；页面文本：%s',
                          stage, location.hostname, location.path, text)
                failures = []
                for entry in driver.get_log('performance'):
                    event = json.loads(entry['message'])['message']
                    if event['method'] == 'Network.loadingFailed':
                        failures.append(event['params'].get('errorText', 'unknown'))
                if failures:
                    LOG.error('浏览器网络错误：%s', failures[-12:])
            except Exception:
                LOG.error('登录超时阶段：%s；无法读取进一步诊断', stage)
            raise BookingError('登录等待超时，阶段：' + stage) from None
        finally:
            driver.quit()
        self.get_user_info()
        if load_categories:
            self.load_categories()
        LOG.info('登录验证成功，已复用 HTTP 会话')

    def get_user_info(self, timeout=None):
        payload = self.request('GET', '/Seat/Index/index?LAB_JSON=1',
                               **({'timeout': timeout} if timeout else {}))
        data = payload.get('DATA', {})
        if payload.get('CODE') != 'ok' or not data.get('is_login') or not data.get('uid'):
            raise BookingError('登录状态验证失败')
        self.uid = int(data['uid'])

    def load_categories(self):
        payload = self.request('GET', '/Space/Category/list?LAB_JSON=1')
        self.categories = {item['name']: item['link']['url'] for item in walk(payload)
                           if item.get('ui_type') == 'ht.space.SpaceReservationListItem'
                           and not item.get('disabled') and item.get('link', {}).get('url')}
        if not self.categories:
            raise BookingError('未找到可预约区域分类')

    def bookings(self):
        items, seen = [], set()
        path = '/Seat/Index/myBookingList?LAB_JSON=1'
        for _ in range(30):
            if path in seen:
                raise BookingError('预约记录分页循环')
            seen.add(path)
            payload = self.request('GET', path)
            for item in walk(payload):
                if item.get('ui_type') == 'ht.Seat.OrderListItem':
                    items.append(item)
            path = payload.get('content', {}).get('defaultNextUrl')
            if not path:
                return items
        raise BookingError('预约记录超过分页上限，无法可靠确认重复预约')

    @staticmethod
    def covered_booking(items, begin, duration):
        for item in items:
            if str(item.get('status')) not in ACTIVE_STATUSES:
                continue
            existing = int(item['time'])
            end = existing + int(item['duration'])
            if existing <= begin and end >= begin + duration:
                return item
            if existing < begin + duration and end > begin:
                raise BookingError('与已有预约部分重叠，请先调整配置或预约')
        return None

    def available_seats(self, region, begin, duration, seat_config, custom):
        if region == '自定义':
            allowed = [int(x) for x in custom]
            if not allowed:
                raise BookingError('自定义座位列表为空')
            # Restrict custom IDs to their configured category; retain a fallback
            # for IDs unknown to the static room mapping.
            known = {seat for seat in allowed for cfg in seat_config.values()
                     for first, last in cfg.get('ranges', []) if first <= seat <= last}
            types = {cfg['type'] for seat in allowed for cfg in seat_config.values()
                     for first, last in cfg.get('ranges', []) if first <= seat <= last}
            category_names = ([name for name in self.categories if name in types]
                              if known == set(allowed) else list(self.categories))
            room_id = None
        elif region == '不限':
            allowed, room_id = None, None
            category_names = [x for x in ('自习室', '阅览室', '生活区') if x in self.categories]
        else:
            if region not in seat_config:
                raise BookingError('未知区域：' + region)
            cfg = seat_config[region]
            category_names, room_id, allowed = [cfg['type']], int(cfg['room_id']), None
        seats = {}
        for category in category_names:
            if category not in self.categories:
                continue
            category_url = urlsplit(self.categories[category])
            data = dict(parse_qsl(category_url.query))
            data.update({'beginTime': begin, 'duration': duration, 'num': 1})
            payload = self.request('POST', category_url.path + '?LAB_JSON=1', data=data)
            if payload.get('ui_type') == 'com.Message':
                raise BookingError(payload.get('MESSAGE', '无法查询座位'))
            if payload.get('IsImgCode'):
                raise BookingError('该区域需要人工验证码，无法无人值守预约')
            for item in walk(payload):
                seat_map = item.get('seatMap', {})
                if not isinstance(seat_map, dict):
                    continue
                info = seat_map.get('info', {})
                if not info or (room_id and int(info['id']) != room_id):
                    continue
                for poi in seat_map.get('POIs', []):
                    seat_id = int(poi['id'])
                    free = str(poi.get('state')) == '0' or (str(poi.get('state')) == '2' and poi.get('recommend'))
                    if free and (allowed is None or seat_id in allowed):
                        seats[seat_id] = {'id': seat_id, 'number': poi['title'], 'room': info['title']}
            if seats and region == '不限':
                break
        if allowed:
            return [seats[x] for x in allowed if x in seats]
        result = list(seats.values())
        random.shuffle(result)
        return result

    def verify(self, booking_id, begin=None, duration=None, seat_id=None):
        payload = self.request('GET', f'/Seat/Index/bookingInfo?bookingId={int(booking_id)}&LAB_JSON=1')
        booking = payload.get('data', {}).get('booking', {})
        if (str(booking.get('id')) != str(booking_id)
                or str(booking.get('status')) not in ACTIVE_STATUSES
                or int(booking.get('user', {}).get('id', 0)) != self.uid):
            raise BookingError('订单详情验证失败')
        if begin is not None and int(booking['begin_time']) != begin:
            raise BookingError('订单开始时间不符')
        if duration is not None and int(booking['duration']) != duration:
            raise BookingError('订单持续时间不符')
        if seat_id is not None and int(booking.get('seat', {}).get('id', 0)) != seat_id:
            raise BookingError('订单座位不符')
        return {'booking_id': str(booking_id), 'room': booking['space']['title'],
                'seat': booking['seat']['title'], 'seat_id': booking['seat']['id'],
                'begin': datetime.fromtimestamp(int(booking['begin_time']), SHANGHAI).isoformat(),
                'duration': int(booking['duration']), 'status': str(booking['status'])}

    def book_slot(self, begin, duration, seats, opening_deadline=None):
        attempts = min(int(self.config.get('max-retry', 5)), len(seats))
        if attempts <= 0:
            raise BookingError('没有符合条件的空闲座位')
        candidates = deque(seats[:attempts])
        window_retries = 0
        while candidates:
            seat = candidates.popleft()
            data = {'api_time': int(time.time()), 'beginTime': begin, 'duration': duration,
                    'is_recommend': 0, 'seatBookers[0]': self.uid, 'seats[0]': seat['id']}
            try:
                payload = self.request('POST', BOOK_PATH, data=data, headers={'Api-Token': booking_token(data)})
            except requests.RequestException:
                existing = self.covered_booking(self.bookings(), begin, duration)
                if existing:
                    return self.verify(existing['id'])
                raise BookingError('预约网络异常，查单未发现订单；停止提交，等待下一轮补跑') from None
            result = payload.get('DATA', {})
            if payload.get('CODE') == 'ok' and result.get('result') == 'success' and result.get('bookingId'):
                verified = self.verify(result['bookingId'], begin, duration, seat['id'])
                verified['submission_confirmed'] = True
                LOG.info('新预约提交已获服务器确认并通过查单：订单 %s', result['bookingId'])
                return verified
            message = str(result.get('msg') or payload.get('MESSAGE') or '预约失败')
            if any(key in message for key in ('频繁', '人数过多', '稍后')):
                raise BookingError('服务限流，等待下一轮补跑：' + message)
            if (opening_deadline and now() < opening_deadline and window_retries < 3
                    and any(word in message for word in ('超出可预约座位时间范围', '未开放', '未到预约'))):
                delay = min(0.4 * (2 ** window_retries), (opening_deadline - now()).total_seconds())
                if delay > 0:
                    LOG.warning('开放边界拒绝，%.1f 秒后重试同一座位：%s', delay, message)
                    time.sleep(delay)
                    candidates.appendleft(seat)
                    window_retries += 1
                    continue
            if any(key in message for key in ('已有预约', '重复预约', '已存在预约', '请先取消')):
                existing = self.covered_booking(self.bookings(), begin, duration)
                if existing:
                    return self.verify(existing['id'])
                raise BookingError('已有其他预约，请核对预约记录：' + message)
            if any(key in message for key in ('已被', '占用', '不可用', '不存在', '冲突', '已预约')):
                LOG.warning('座位 %s 不可约：%s', seat['number'], message)
                time.sleep(0.3)
                continue
            raise BookingError('预约被拒绝：' + message)
        raise BookingError('候选座位均已不可用，等待下一轮补跑')


def parse_args():
    parser = argparse.ArgumentParser(description='杭电图书馆预约，时区固定为北京时间')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='只验证登录和座位，不提交')
    mode.add_argument('--test-booking', action='store_true', help='忽略星期开关，真实预约一个测试时段')
    parser.add_argument('--date', type=lambda s: datetime.strptime(s, '%Y-%m-%d').date())
    parser.add_argument('--start', type=float, default=7)
    parser.add_argument('--hours', type=float, default=1)
    parser.add_argument('--region', default='二楼西')
    parser.add_argument('--at', choices=['20:00', '20:02'], help='提前登录，等到今天的目标时刻提交')
    parser.add_argument('--backup-at', choices=['20:02'], help='失败后复用当前会话等待补跑')
    return parser.parse_args()


def reserve_day(client, args, day, slots, target_date, target_time, seat_config, user_config):
    existing = client.bookings()
    prepared = []
    midnight = datetime.combine(target_date, datetime.min.time(), SHANGHAI)
    for start, hours in slots:
        begin = int((midnight + timedelta(hours=start)).timestamp())
        duration = int(hours * 3600)
        duplicate = client.covered_booking(existing, begin, duration)
        if duplicate:
            LOG.info('时段已有预约，核验订单 %s', duplicate['id'])
            prepared.append((begin, duration, [], client.verify(duplicate['id'])))
            continue
        if target_time and not args.check:
            # Candidate IDs are sufficient for submission; the server decides
            # availability. Avoid slow maps and stale pre-release snapshots.
            seats = configured_seats(day['name'], seat_config, user_config.get('自定义', []))
        else:
            seats = client.available_seats(day['name'], begin, duration, seat_config, user_config.get('自定义', []))
        LOG.info('%s %g:00 起 %g 小时，%s 候选座位 %d 个', target_date, start, hours, day['name'], len(seats))
        prepared.append((begin, duration, seats, None))
    if args.check:
        LOG.info('只读检查完成，未提交预约')
        return 0
    if target_time and any(duplicate is None for _, _, _, duplicate in prepared):
        if now() > target_time + timedelta(seconds=30):
            raise BookingError('准备完成时已超过目标时间 30 秒，本轮退出')
        LOG.info('登录、查单及候选座位准备已完成；等待 %s', target_time.isoformat())
        wait_until(target_time - timedelta(seconds=10))
        try:
            client.get_user_info(timeout=(2, 3))
        except requests.RequestException as error:
            LOG.warning('连接预热失败（%s），仍按目标时间尝试提交', type(error).__name__)
        if now() > target_time + timedelta(seconds=30):
            raise BookingError('连接预热超时，本轮退出')
        wait_until(target_time)
        LOG.info('触发偏差 %.3f ms', (now() - target_time).total_seconds() * 1000)
    results = []
    for begin, duration, seats, duplicate in prepared:
        opening_deadline = (target_time + timedelta(seconds=4)
                            if target_time and target_time.strftime('%H:%M') == '20:00' else None)
        result = duplicate or client.book_slot(begin, duration, seats, opening_deadline=opening_deadline)
        result['already_existed'] = duplicate is not None
        result.setdefault('submission_confirmed', False)
        result['trigger_event'] = os.environ.get('GITHUB_EVENT_NAME', 'local')
        result['github_run_id'] = os.environ.get('GITHUB_RUN_ID', '')
        LOG.info('预约已核实：%s %s号座，%s，订单 %s', result['room'], result['seat'], result['begin'], result['booking_id'])
        results.append(result)
        LOCAL.mkdir(exist_ok=True)
        report = LOCAL / ('booking-' + now().strftime('%Y%m%d-%H%M%S-%f') + '.json')
        report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0


def run(args):
    user_config = read_yaml(ROOT / 'user_config.yml')
    basic = read_yaml(ROOT / 'config/basic_config.yml')['SeatAutoBooker']
    if basic.get('cloud-only', False) and os.environ.get('GITHUB_ACTIONS') != 'true':
        LOG.info('本机执行已停用：当前仅允许 GitHub Actions 云端运行，未登录、未提交预约')
        return 0
    LOG.info('执行环境：%s', 'GitHub Actions 云端' if os.environ.get('GITHUB_ACTIONS') == 'true' else '本机')
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        LOG.info('GitHub 触发事件=%s；运行编号=%s', os.environ.get('GITHUB_EVENT_NAME', 'unknown'),
                 os.environ.get('GITHUB_RUN_ID', 'unknown'))
    seat_config = read_yaml(ROOT / 'config/seat_config.yml')
    target_date = args.date or (now() + timedelta(days=2)).date()
    target_time = None
    if args.check or args.test_booking:
        day = {'name': args.region, '开始时间': args.start, '持续小时数': args.hours}
    else:
        weekday = WEEKDAYS[target_date.weekday()]
        day = user_config[weekday]
        if day.get('启用') is not True:
            LOG.info('%s %s 预约未启用；请在 user_config.yml 设置日常规则', target_date, weekday)
            return 0
    if args.at:
        hour, minute = map(int, args.at.split(':'))
        target_time = now().replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now() > target_time + timedelta(seconds=30):
            raise BookingError('定时目标已过期超过 30 秒，本轮退出')
    slots = get_time_slots(day)
    client = SeatAutoBooker(basic)
    try:
        for attempt in range(2):
            try:
                client.login(load_categories=target_time is None or args.check)
                break
            except (BookingError, requests.RequestException) as error:
                transient = isinstance(error, requests.RequestException) or '登录等待超时' in str(error)
                if attempt or not transient or not target_time or now() >= target_time - timedelta(seconds=60):
                    raise
                LOG.warning('提前登录暂时失败，2 秒后重试一次：%s', type(error).__name__)
                time.sleep(2)
        targets = [target_time]
        backup = getattr(args, 'backup_at', None)
        if isinstance(backup, str):
            if args.at != '20:00' or args.check:
                raise BookingError('--backup-at 仅用于 --at 20:00 的真实预约')
            targets.append(target_time.replace(hour=20, minute=2))
        for index, target in enumerate(targets):
            try:
                return reserve_day(client, args, day, slots, target_date, target, seat_config, user_config)
            except (BookingError, requests.RequestException) as error:
                if index == len(targets) - 1:
                    raise
                LOG.warning('本轮失败：%s；保留登录会话，等待 20:02 查单后补跑', error)
                wait_until(targets[index + 1] - timedelta(seconds=20))
    finally:
        client.close()


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    (ROOT / 'logs').mkdir(exist_ok=True)
    logging.Formatter.converter = staticmethod(log_time)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s.%(msecs)03d %(levelname)s %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S', handlers=[logging.StreamHandler(),
                        logging.FileHandler(ROOT / 'logs' / (now().strftime('%Y-%m-%d') + '.log'), encoding='utf-8')])
    args = parse_args()
    try:
        with single_instance():
            return run(args)
    except AlreadyRunning as error:
        LOG.info('%s', error)
        return 0
    except (BookingError, ValueError, KeyError, requests.RequestException) as error:
        LOG.error('%s', error)
        return 1
    except Exception as error:
        LOG.error('运行异常：%s（不输出可能包含凭据的浏览器堆栈）', type(error).__name__)
        return 1


if __name__ == '__main__':
    sys.exit(main())
