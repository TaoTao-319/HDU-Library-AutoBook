"""A write-ahead record for one local account's unresolved booking POST.

Callers hold main.single_instance(). This is not a cross-runner lease.
"""
import hashlib
import json
import os
import re
from pathlib import Path
import time
import uuid


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class SubmissionJournal:
    def __init__(self, root, uid):
        if type(uid) is not int or uid <= 0:
            raise ValueError('未验证账号，不能记录预约提交')
        self.uid = uid
        account = hashlib.sha256(str(uid).encode()).hexdigest()[:16]
        self.account = account
        self.path = Path(root) / ('pending-' + account + '.json')

    def load(self):
        if not self.path.exists():
            return None
        value = json.loads(self.path.read_text(encoding='utf-8'))
        if (not isinstance(value, dict) or value.get('version') != 1
                or value.get('uid') != self.uid
                or value.get('state') not in ('unknown', 'server_accepted')
                or not isinstance(value.get('intent_id'), str)
                or re.fullmatch(r'[0-9a-f]{32}', value['intent_id']) is None
                or any(type(value.get(key)) is not int for key in ('begin', 'duration', 'seat_id'))
                or value['duration'] <= 0 or value['seat_id'] <= 0
                or not isinstance(value.get('baseline_ids'), list)
                or any(not isinstance(item, str) for item in value['baseline_ids'])
                or (value['state'] == 'server_accepted'
                    and (type(value.get('booking_id')) is not int or value['booking_id'] <= 0))):
            raise ValueError('待确认预约记录损坏，停止新增提交，请人工核对')
        return value

    def start(self, data, baseline_ids):
        if self.load() is not None:
            raise ValueError('存在待确认预约，停止新增提交')
        value = {'version': 1, 'uid': self.uid, 'intent_id': uuid.uuid4().hex,
                 'begin': int(data['beginTime']), 'duration': int(data['duration']),
                 'seat_id': int(data['seats[0]']), 'baseline_ids': sorted(map(str, baseline_ids)),
                 'payload_hash': hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
                 'created_at': time.time(), 'state': 'unknown'}
        atomic_json(self.path, value)
        return value

    def accepted(self, intent, booking_id):
        current = self.load()
        if not current or current['intent_id'] != intent['intent_id']:
            raise ValueError('待确认预约记录发生变化，停止新增提交')
        current.update(state='server_accepted', booking_id=int(booking_id))
        if current['booking_id'] <= 0:
            raise ValueError('服务端订单编号无效')
        atomic_json(self.path, current)

    def clear(self, intent):
        current = self.load()
        if not current or current['intent_id'] != intent['intent_id']:
            raise ValueError('待确认预约记录发生变化，停止新增提交')
        self.path.unlink()

    def complete(self, intent, booking):
        # Keep a durable verified-order hint before retiring the pending record.
        # A new process still checks its details if the server's list is delayed.
        atomic_json(self.path.parent / ('verified-' + self.account + '-' + intent['intent_id'] + '.json'),
                    {'version': 1, 'uid': self.uid, 'booking': booking})
        self.clear(intent)

    def verified_bookings(self):
        result = []
        for path in self.path.parent.glob('verified-' + self.account + '-*.json'):
            value = json.loads(path.read_text(encoding='utf-8'))
            if value.get('version') != 1 or value.get('uid') != self.uid:
                raise ValueError('已验证订单记录损坏，停止新增提交')
            result.append(value['booking'])
        return result
