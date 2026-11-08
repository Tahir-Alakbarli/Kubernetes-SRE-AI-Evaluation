import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

semaphore = threading.BoundedSemaphore(int(os.getenv('MAX_ACTIVE', '2')))
lock = threading.Lock()
stats = {'accepted': 0, 'rejected': 0, 'active': 0, 'completed': 0}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def respond(self, code, value):
        payload = json.dumps(value).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == '/health':
            return self.respond(200, {'status': 'ready'})
        if self.path == '/metrics':
            with lock:
                result = dict(stats)
            return self.respond(200, result)
        if not self.path.startswith('/reserve'):
            return self.respond(404, {'error': 'unknown_route'})
        acquired = semaphore.acquire(blocking=False)
        with lock:
            stats['accepted' if acquired else 'rejected'] += 1
            if acquired:
                stats['active'] += 1
        if not acquired:
            return self.respond(503, {'error': 'saturated', 'retryable': True})
        try:
            time.sleep(float(os.getenv('WORK_SECONDS', '0.32')))
            with lock:
                stats['completed'] += 1
            self.respond(200, {'approved': True})
        finally:
            with lock:
                stats['active'] -= 1
            semaphore.release()

ThreadingHTTPServer(('0.0.0.0', int(os.getenv('PROCESSOR_PORT', '8090'))), Handler).serve_forever()
