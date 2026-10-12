from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8787
URL = f"http://{HOST}:{PORT}/"


def _open_when_ready(url: str, timeout: float = 10.0) -> None:
    """Open the browser once the server accepts connections; give up quietly on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((HOST, PORT), timeout=0.5):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.25)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local job desk.")
    parser.add_argument("--no-browser", action="store_true", help="do not open the Dashboard automatically")
    args = parser.parse_args()

    if str(WEB_DIR) not in sys.path:
        sys.path.insert(0, str(WEB_DIR))

    import uvicorn

    print(f"Job desk: {URL}", flush=True)
    print("Bound to localhost only. For live UI reload, run npm run dev in web/frontend.", flush=True)
    if not args.no_browser:
        threading.Thread(target=_open_when_ready, args=(URL,), daemon=True).start()
    uvicorn.run(
        "api.app:app",
        host=HOST,
        port=PORT,
        app_dir=str(WEB_DIR),
        reload=False,
    )


if __name__ == "__main__":
    main()
