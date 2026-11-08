import json
import sys
import urllib.request

urls = json.loads(sys.argv[1])
results = {}
for url in urls:
    try:
        with urllib.request.urlopen(url, timeout=1.2) as response:
            results[url] = response.status
    except Exception:
        results[url] = 0
print(json.dumps(results))
