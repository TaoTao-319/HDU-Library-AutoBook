"""School CAS HTTP login, adapted from the user's DeepSeek implementation."""
import base64
from html.parser import HTMLParser
from urllib.parse import urlsplit

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

SSO = 'https://sso.hdu.edu.cn'
LIBRARY = 'https://hdu.huitu.zhishulib.com'


class Fields(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.values, self.current = {}, None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if tag == 'p':
            self.current = dict(attrs).get('id')

    def handle_data(self, text):
        if self.current:
            self.values[self.current] = self.values.get(self.current, '') + text

    def handle_endtag(self, tag):
        if tag == 'p':
            self.current = None

    def get(self, name):
        return self.values.get(name, '').strip()


def encrypt_password(password, key):
    raw = base64.b64decode(key, validate=True)
    if len(raw) not in (16, 24, 32):
        raise ValueError('统一身份认证加密参数长度异常')
    return base64.b64encode(AES.new(raw, AES.MODE_ECB).encrypt(pad(password.encode(), 16))).decode()


def http_cas_login(session, username, password):
    response = session.get(LIBRARY + '/User/Index/hduCASLogin', timeout=(5, 20))
    response.raise_for_status()
    if urlsplit(response.url).hostname != 'sso.hdu.edu.cn':
        raise ValueError('未进入学校统一身份认证页面')
    fields = Fields(response.text)
    key, execution = fields.get('login-croypto'), fields.get('login-page-flowkey')
    if not key or not execution:
        raise ValueError('未获取到统一身份认证参数，页面可能已改版')
    data = {'username': username, 'type': fields.get('current-login-type') or 'UsernamePassword',
            '_eventId': 'submit', 'geolocation': '', 'execution': execution, 'captcha_code': '',
            'croypto': key, 'password': encrypt_password(password, key), 'captcha_payload': ''}
    result = session.post(SSO + '/login', data=data, timeout=(5, 20), allow_redirects=True,
                          headers={'Content-Type': 'application/x-www-form-urlencoded',
                                   'Origin': SSO, 'Referer': response.url})
    result.raise_for_status()
    if urlsplit(result.url).hostname != 'hdu.huitu.zhishulib.com':
        # No response body is logged: it may contain account or form contents.
        raise ValueError('统一身份认证未返回图书馆，需核对密码、验证码或学校页面状态')
