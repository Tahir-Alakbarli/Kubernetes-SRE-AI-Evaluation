import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

upstream = os.getenv('PROCESSOR_URL', 'http://reservation-engine:8090/reserve')
attempts = int(os.getenv('MAX_ATTEMPTS', '42'))
delay = int(os.getenv('RETRY_DELAY_MS', '5')) / 1000
workers = int(os.getenv('WORKERS', '12'))
max_pending = int(os.getenv('MAX_PENDING', '250'))
requests = queue.Queue(maxsize=max_pending)
lock = threading.Lock()
stats = {'received': 0, 'completed': 0, 'rejected': 0, 'upstream_attempts': 0, 'upstream_errors': 0, 'timeouts': 0, 'active_workers': 0}

def worker():
    while True:
        job = requests.get()
        with lock:
            stats['active_workers'] += 1
        status = 503
        for attempt in range(max(1, attempts)):
            with lock:
                stats['upstream_attempts'] += 1
            try:
                with urllib.request.urlopen(upstream, timeout=0.9) as response:
                    if response.status == 200:
                        status = 200
                        break
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
                pass
            with lock:
                stats['upstream_errors'] += 1
            if attempt + 1 < attempts:
                time.sleep(delay)
        job['status'] = status
        job['event'].set()
        with lock:
            stats['completed'] += 1
        with lock:
            stats['active_workers'] -= 1
        requests.task_done()

for i in range(workers):
    threading.Thread(target=worker, daemon=True, name=f'gateway-worker-{i}').start()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def respond(self, code, value):
        raw = json.dumps(value).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == '/health':
            return self.respond(200, {'status': 'ready'})
        if self.path == '/metrics':
            with lock:
                view = dict(stats)
            view['pending'] = requests.qsize()
            view['max_attempts'] = attempts
            view['retry_delay_ms'] = int(delay * 1000)
            return self.respond(200, view)
        if not self.path.startswith('/checkout'):
            return self.respond(404, {'error': 'not_found'})
        with lock:
            stats['received'] += 1
        job = {'event': threading.Event(), 'status': 503}
        try:
            requests.put_nowait(job)
        except queue.Full:
            with lock:
                stats['rejected'] += 1
            return self.respond(503, {'error': 'queue_full'})
        if not job['event'].wait(1.5):
            with lock:
                stats['timeouts'] += 1
            return self.respond(504, {'error': 'upstream_deadline'})
        self.respond(job['status'], {'status': 'approved' if job['status'] == 200 else 'unavailable'})

ThreadingHTTPServer(('0.0.0.0', int(os.getenv('GATEWAY_PORT', '8080'))), Handler).serve_forever()
