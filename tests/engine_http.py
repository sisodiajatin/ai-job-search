"""Local pages for the form-engine tests."""

import functools
import http.server
import threading
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "forms"


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args, **kwargs):
        pass


class Server:
    def __init__(self, directory: Path = FIXTURES):
        handler = functools.partial(_Quiet, directory=str(directory))
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
