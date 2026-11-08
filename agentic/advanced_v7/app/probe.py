import concurrent.futures
import json
import sys
import time
import urllib.error
import urllib.request

base = sys.argv[1].rstrip('/')

def fetch(path, timeout=2.0):
    try:
        with urllib.request.urlopen(base + path, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, {}
    except Exception:
        return 0, {}

before_code, before = fetch('/metrics')
with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
    burst = list(pool.map(lambda _: fetch('/checkout', 2.4)[0], range(120)))
time.sleep(0.2)
steady = [fetch('/checkout', 2.4)[0] for _ in range(5)]
after_code, after = fetch('/metrics')
result = {
    'burst_count': len(burst),
    'burst_success': burst.count(200),
    'post_burst_statuses': steady,
    'post_burst_success': steady.count(200),
    'pending': after.get('pending'),
    'active_workers': after.get('active_workers'),
    'attempt_delta': after.get('upstream_attempts', 0) - before.get('upstream_attempts', 0),
    'error_delta': after.get('upstream_errors', 0) - before.get('upstream_errors', 0),
    'configured_attempts': after.get('max_attempts'),
    'metrics_reachable': before_code == 200 and after_code == 200,
}
print(json.dumps(result))
