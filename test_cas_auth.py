"""HTTP CAS protocol fixtures only: never contact the school."""
import base64
import unittest
from unittest.mock import Mock, patch

from cas_auth import Fields, encrypt_password, http_cas_login


class CasAuthTests(unittest.TestCase):
    def test_fields_handle_attribute_order_quotes_and_html_entities(self):
        fields = Fields("<p class='x' id='login-page-flowkey'> abc&amp;d </p>")
        self.assertEqual(fields.get('login-page-flowkey'), 'abc&d')
        self.assertEqual(fields.get('missing'), '')

    def test_password_matches_known_client_ciphertext(self):
        key = base64.b64encode(b'0123456789abcdef').decode()
        # Independent Node.js crypto/OpenSSL AES-ECB/PKCS7 fixture.
        self.assertEqual(encrypt_password('abc', key), 'HSWCHD4xHuotTdhjOiXBtQ==')

    def test_invalid_key_is_rejected(self):
        for key in ('not-base64!', base64.b64encode(bytes(8)).decode()):
            with self.subTest(key=key), self.assertRaises(ValueError):
                encrypt_password('fixture-password', key)

    def test_missing_parameters_or_unexpected_host_never_send_password(self):
        for url, text in [('https://other.invalid/login', ''), ('https://sso.hdu.edu.cn/login', '')]:
            session = Mock()
            session.get.return_value = Mock(url=url, text=text)
            with self.assertRaises(ValueError):
                http_cas_login(session, 'fixture-user', 'fixture-password')
            session.post.assert_not_called()

    def test_success_reuses_session_and_sends_encrypted_password(self):
        session = Mock()
        key = base64.b64encode(b'0123456789abcdef').decode()
        session.get.return_value = Mock(url='https://sso.hdu.edu.cn/login', text=
            f'<p id="login-croypto">{key}</p><p id="login-page-flowkey">fixture-flow</p>')
        session.post.return_value = Mock(url='https://hdu.huitu.zhishulib.com/')
        http_cas_login(session, 'fixture-user', 'fixture-password')
        data = session.post.call_args.kwargs['data']
        self.assertEqual(data['execution'], 'fixture-flow')
        self.assertNotEqual(data['password'], 'fixture-password')

    def test_cloud_browser_timeout_uses_one_http_fallback_and_still_checks_login(self):
        import main
        client = main.SeatAutoBooker({})
        self.addCleanup(client.close)
        driver = Mock(current_url='https://sso.hdu.edu.cn/login')
        driver.get.side_effect = main.TimeoutException()
        driver.find_element.return_value.text = 'fixture page'
        driver.get_log.return_value = []
        client.get_user_info = Mock()
        client.load_categories = Mock()
        with patch.dict('os.environ', {'GITHUB_ACTIONS': 'true'}), \
                patch('main.credentials', return_value=('fixture-user', 'fixture-password')), \
                patch('main.webdriver.Chrome', return_value=driver), \
                patch('cas_auth.http_cas_login') as fallback:
            client.login()
        fallback.assert_called_once_with(client.session, 'fixture-user', 'fixture-password')
        driver.quit.assert_called_once()
        client.get_user_info.assert_called_once()
        client.load_categories.assert_called_once()


if __name__ == '__main__':
    unittest.main()
