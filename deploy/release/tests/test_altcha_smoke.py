"""Regression coverage for ALTCHA-aware deployment smoke verification."""
import contextlib
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest


PLUGIN = Path(__file__).resolve().parents[1] / "smoke.d" / "20-auth-forms.py"
PATHS = ("/login/", "/register/", "/reset-password/", "/contact/")
ACTIONS = {
    "/login/": ("login_password", "login_otp"),
    "/register/": ("register",),
    "/reset-password/": ("password_reset",),
    "/contact/": ("contact",),
}


def page(actions):
    widgets = "".join(
        '<div class="rasti-altcha" data-altcha-action="{a}">'
        '<altcha-widget id="captcha-{a}" type="checkbox" auto="off" name="altcha" '
        'challenge="/altcha/challenge/?action={a}"></altcha-widget></div>'.format(a=a)
        for a in actions
    )
    return ('<html><script type="module" src="/static/portal/js/altcha-forms.js?v=1"></script>'
            '<form><input type="hidden" name="csrfmiddlewaretoken" value="abc">'
            + widgets + '</form></html>')


class AltchaSmokeTests(unittest.TestCase):
    def setUp(self):
        self.pages = {path: page(actions) for path, actions in ACTIONS.items()}
        self.headers = {}
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.release = Path(self.temp.name)
        asset = self.release / 'apps/portal/static/portal/js/altcha-forms.js'
        asset.parent.mkdir(parents=True)
        asset.write_text('/* test-only asset */', encoding='utf-8')
        template = self.release / 'apps/portal/templates/portal/public_v2/_captcha.html'
        template.parent.mkdir(parents=True)
        template.write_text('<altcha-widget></altcha-widget>', encoding='utf-8')
        testcase = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = testcase.pages.get(self.path)
                if body is None:
                    self.send_error(404)
                    return
                data = body.encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                for key, value in testcase.headers.get(self.path, {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.shutdown)

    def run_check(self):
        env = dict(os.environ, BASE_URL='http://127.0.0.1:%d' % self.server.server_port,
                   RELEASE_DIR=str(self.release), SMOKE_REQUIRE_CAPTCHA='1')
        proc = subprocess.run([sys.executable, str(PLUGIN)], env=env, text=True,
                              capture_output=True, timeout=40)
        self.assertTrue(proc.stdout.strip(), proc.stderr)
        return proc.returncode, json.loads(proc.stdout.strip().splitlines()[-1])

    def test_valid_widgets_for_all_five_form_actions_pass(self):
        code, result = self.run_check()
        self.assertEqual(code, 0, result)
        self.assertEqual(sum('ALTCHA well formed' in item for item in result['passed']), 5)

    def test_missing_login_otp_widget_fails(self):
        self.pages['/login/'] = page(('login_password',))
        self.assertEqual(self.run_check()[0], 1)

    def test_missing_widget_fails_closed(self):
        self.pages['/contact/'] = '<form><input name="csrfmiddlewaretoken"></form>'
        self.assertEqual(self.run_check()[0], 1)

    def test_missing_challenge_fails(self):
        self.pages['/register/'] = self.pages['/register/'].replace(
            'challenge="/altcha/challenge/?action=register"', '')
        self.assertEqual(self.run_check()[0], 1)

    def test_external_challenge_fails(self):
        self.pages['/register/'] = self.pages['/register/'].replace(
            '/altcha/challenge/?action=register', 'https://evil.example/challenge')
        self.assertEqual(self.run_check()[0], 1)

    def test_missing_js_reference_fails(self):
        self.pages['/contact/'] = self.pages['/contact/'].replace('altcha-forms.js', 'missing.js')
        self.assertEqual(self.run_check()[0], 1)

    def test_missing_js_source_fails(self):
        (self.release / 'apps/portal/static/portal/js/altcha-forms.js').unlink()
        self.assertEqual(self.run_check()[0], 1)

    def test_unavailable_service_fails(self):
        self.pages['/contact/'] += '<div class="captcha-unavailable">Service unavailable</div>'
        self.assertEqual(self.run_check()[0], 1)

    def test_disabled_widget_fails(self):
        self.pages['/contact/'] = self.pages['/contact/'].replace('name="altcha"', 'name="altcha" disabled')
        self.assertEqual(self.run_check()[0], 1)

    def test_public_cache_fails(self):
        self.headers['/login/'] = {'Cache-Control': 'public, max-age=600'}
        self.assertEqual(self.run_check()[0], 1)

    def test_legacy_turnstile_in_altcha_release_fails(self):
        self.pages['/contact/'] = ('<form><input name="csrfmiddlewaretoken">'
                                  '<div class="cf-turnstile"></div></form>')
        self.assertEqual(self.run_check()[0], 1)

    def test_math_captcha_in_altcha_release_fails(self):
        self.pages['/contact/'] = '<form><input name="csrfmiddlewaretoken"><input id="captcha-answer-contact"></form>'
        self.assertEqual(self.run_check()[0], 1)


if __name__ == '__main__':
    unittest.main()
