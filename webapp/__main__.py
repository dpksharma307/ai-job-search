"""Start the local web app:  python -m webapp  [--port 8000] [--no-browser]"""

from __future__ import annotations

import argparse
import shutil
import sys
import threading
import webbrowser


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m webapp", description="AI Job Search local web app")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = parser.parse_args()

    try:
        import uvicorn
        from .server import create_app
    except ImportError as exc:
        print(f"Missing a Python package ({exc.name}). Run:  pip install -r webapp/requirements.txt")
        return 1

    if not shutil.which("claude"):
        print("Warning: Claude Code ('claude') was not found on PATH. Pages will open, "
              "but actions can't run until it is installed and logged in.", file=sys.stderr)

    url = f"http://127.0.0.1:{args.port}/"
    print(f"AI Job Search is running at {url}  (press Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    # Loopback only: this app runs your Claude account and reads your personal files.
    uvicorn.run(create_app(port=args.port), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
