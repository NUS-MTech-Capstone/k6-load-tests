"""Small, secret-safe helpers for the opt-in AWS performance campaign."""
import datetime as dt
import http.cookiejar
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

BASE = 'https://api.ddm.keiyam.me'
PROFILE = 'capstone'
REGION = 'ap-southeast-1'
ACCOUNT = '891377133321'
CONTEXT = f'arn:aws:eks:{REGION}:{ACCOUNT}:cluster/dendenmushi-cluster-dev'


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def command(args):
    p = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if p.returncode:
        # Do not echo subprocess arguments or output: they may contain credentials.
        raise RuntimeError(f'{args[0]} failed with exit {p.returncode}')
    return p.stdout


def aws(*args):
    return json.loads(command(['aws', *args, '--profile', PROFILE, '--region', REGION, '--output', 'json']))


def kube(*args):
    return json.loads(command(['kubectl', '--context', CONTEXT, *args, '-o', 'json']))


class LoginForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = None
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'form' and a.get('method', '').lower() == 'post':
            self.action = a.get('action', '')
        if tag == 'input' and a.get('name'):
            self.fields[a['name']] = a.get('value', '')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Api:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.api_opener = urllib.request.build_opener(NoRedirect())
        self.cookie = os.environ.get('DDM_ALB_COOKIE_HEADER', '').removeprefix('Cookie:').strip()

    def login(self):
        if self.cookie:
            self.get('/ddm/auth/me')
            return
        try:
            r = self.opener.open(BASE + '/ddm/auth/login', timeout=30)
            u = urllib.parse.urlsplit(r.url)
            if u.hostname != 'ddm-keiyam-dev.auth.ap-southeast-1.amazoncognito.com':
                raise RuntimeError('Unexpected authentication destination')
            form = LoginForm()
            form.feed(r.read().decode())
            if form.action is None or 'csrf' not in form.fields:
                raise RuntimeError('Unsupported Cognito login form')
            action = urllib.parse.urljoin(r.url, form.action)
            if urllib.parse.urlsplit(action).hostname != u.hostname:
                raise RuntimeError('Unexpected form destination')
            username = 'kei-admin@ddm.keiyam.me'
            password = command(['security', 'find-generic-password', '-s', 'DenDenMushi E2E Cognito', '-a', username, '-w']).strip()
            form.fields.update(username=username, password=password)
            req = urllib.request.Request(action, urllib.parse.urlencode(form.fields).encode(), {'Content-Type': 'application/x-www-form-urlencoded', 'Referer': r.url})
            self.opener.open(req, timeout=30).read()
            password = None
            form.fields.clear()
            self.cookie = '; '.join(f'{c.name}={c.value}' for c in self.jar if c.name.startswith('AWSELBAuthSessionCookie') and c.domain.lstrip('.') == 'api.ddm.keiyam.me')
            if not self.cookie:
                raise RuntimeError('Cognito did not establish an ALB session; interactive sign-in may be required')
            self.get('/ddm/auth/me')
        except Exception:
            raise RuntimeError('Cognito sign-in failed; no credentials or response bodies were recorded') from None

    def request(self, path, method='GET', body=None):
        req = urllib.request.Request(BASE + path, None if body is None else json.dumps(body).encode(), {
            'Cookie': self.cookie, 'Accept': 'application/json', 'Content-Type': 'application/json', 'x-api-version': '1'
        }, method=method)
        try:
            r = self.api_opener.open(req, timeout=15)
            if urllib.parse.urlsplit(r.url).hostname != urllib.parse.urlsplit(BASE).hostname:
                return 401, None
            data = r.read()
            try:
                return r.status, json.loads(data)
            except ValueError:
                return r.status, None
        except urllib.error.HTTPError as e:
            return e.code, None
        except Exception:
            return 0, None

    def get(self, path):
        status, body = self.request(path)
        if status != 200 or body is None:
            raise RuntimeError(f'Authenticated API read failed ({status})')
        return body


def items(data):
    if isinstance(data, list):
        return data
    for key in ('items', 'data', 'sessions'):
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return data[key]
    return []


if __name__ == '__main__':
    if aws('sts', 'get-caller-identity')['Account'] != ACCOUNT:
        raise SystemExit('Wrong AWS account')
    api = Api()
    api.login()
    print('Authenticated ALB session established; cookie held in memory only.')
    records = items(api.get('/ddm/change-requests'))
    print(json.dumps([{
        k: row.get(k) for k in ('id', 'title', 'status', 'implementationStartAt', 'implementationEndAt', 'vmIds')
    } for row in records if row.get('status') == 'Approved'], indent=2))
