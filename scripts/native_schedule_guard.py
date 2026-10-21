"""Claim one native-schedule test before any school login or POST."""
from datetime import datetime, timedelta, timezone
import os
import re
import subprocess

SHANGHAI = timezone(timedelta(hours=8))
DEADLINE = datetime(2026, 10, 22, 6, tzinfo=SHANGHAI)
CLAIM = 'refs/tags/native-test-20261022-0700-20261021a'


def validate(environment, current):
    if (environment.get('GITHUB_ACTIONS') != 'true'
            or environment.get('GITHUB_EVENT_NAME') != 'schedule'
            or environment.get('GITHUB_RUN_ATTEMPT') != '1'):
        raise ValueError('只接受首次 GitHub 原生 schedule 运行；本地、手动和重跑均不执行预约')
    if current >= DEADLINE or current < datetime(2026, 10, 21, 21, tzinfo=SHANGHAI):
        raise ValueError('本次测试已过期或尚未进入测试窗口，不登录、不预约')
    if (environment.get('GITHUB_REPOSITORY') != 'TaoTao-319/HDU-Library-AutoBook'
            or not re.fullmatch(r'[0-9a-f]{40}', environment.get('GITHUB_SHA', ''))):
        raise ValueError('测试仓库或提交编号不正确')


def main():
    validate(os.environ, datetime.now(SHANGHAI))
    # Atomic ref creation also excludes queued deliveries on fresh runners.
    subprocess.run(['gh', 'api', '--method', 'POST',
                    'repos/TaoTao-319/HDU-Library-AutoBook/git/refs',
                    '-f', 'ref=' + CLAIM, '-f', 'sha=' + os.environ['GITHUB_SHA']],
                   check=True, stdout=subprocess.DEVNULL)
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as stream:
            stream.write('原生 schedule 单次测试已领取，目标：2026-10-22 07:00–08:00，四楼。\n'
                         '领取成功不代表预约成功；仅本次新建且查单确认的订单可通过验收。\n')


if __name__ == '__main__':
    main()
