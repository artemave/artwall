"""A real loopback HTTP server for tests.

Lets the HTTP layer be exercised over genuine sockets instead of mocking
urllib. `router(path) -> (status, content_type, body_bytes)`.
"""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Server:
    def __init__(self, router):
        captured = self.requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                captured.append(self.path)
                status, ctype, body = router(self.path)
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._httpd.shutdown()
        self._thread.join()
        self._httpd.server_close()


def serve(router):
    return _Server(router)
