"""Non-mutating API smoke tests. Does not upload/create/delete data."""
import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PATHS = ['/health', '/api/v1/system/status', '/api/v1/career/employees',
         '/api/v1/career/pptx/templates']


def check(base_url, paths=PATHS):
    results = []
    for path in paths:
        req = Request(base_url.rstrip('/') + path, headers={'Accept': 'application/json'})
        try:
            with urlopen(req, timeout=10) as response:
                data = response.read(1024 * 1024)
                try:
                    body = json.loads(data)
                except (ValueError, UnicodeError):
                    body = {'non_json': True}
                ok = 200 <= response.status < 300
                results.append((path, ok, response.status, body))
        except HTTPError as e:
            results.append((path, False, e.code, None))
        except URLError as e:
            results.append((path, False, 'UNREACHABLE', None))
    return results


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--base-url', default='http://localhost:18000')
    args = p.parse_args()
    data = check(args.base_url)
    for path, ok, code, body in data:
        print(f'{"PASS" if ok else "FAIL"} [{code}] {path}')
        if path.endswith('/system/status') and isinstance(body, dict):
            print('  statuses:', {k: body.get(k) for k in ('api', 'postgresql', 'careerAgent')})
            if body.get('postgresql') != 'UP' or body.get('careerAgent') != 'UP':
                print('  WARNING: connected services not fully UP')
    return 0 if all(ok for _, ok, _, _ in data) else 1

if __name__ == '__main__':
    sys.exit(main())
