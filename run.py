"""GE Companion: start the local price engine and open the dashboard.

Usage:
    python run.py           live data from the OSRS Wiki
    python run.py --demo    offline demo with synthetic prices
    python run.py --no-browser
"""
import argparse
import logging
import os
import sys
import threading
import time
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from geco import config  # noqa: E402
from geco.db import DB  # noqa: E402
from geco.engine import Engine  # noqa: E402
from geco.server import App, serve  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="GE Companion")
    ap.add_argument("--demo", action="store_true", help="use offline synthetic data")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--port", type=int)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    cfg = config.load()
    port = args.port or int(cfg.get("port", 8765))

    if args.demo:
        from geco.demo import DemoClient
        client = DemoClient()
        db_path = os.path.join(config.DATA_DIR, "demo.sqlite3")
    else:
        from geco.wiki import WikiClient
        client = WikiClient(cfg["user_agent"])
        db_path = os.path.join(config.DATA_DIR, "ge_companion.sqlite3")

    db = DB(db_path)
    engine = Engine(cfg, client, db)
    print("GE Companion: loading item list and prices from the OSRS Wiki..." if not args.demo
          else "GE Companion: demo mode (synthetic prices)")
    try:
        engine.start()
    except Exception as e:
        print(f"\nCould not load the item list: {e}\n"
              "Check your internet connection, then try again. "
              "(Run with --demo to see the dashboard offline.)")
        sys.exit(1)

    try:
        httpd = serve(App(cfg, engine, db, client), port)
    except OSError:
        print(f"\nPort {port} is busy. GE Companion may already be running: "
              f"open http://127.0.0.1:{port} in your browser.")
        sys.exit(1)

    url = f"http://127.0.0.1:{port}"
    print(f"\nDashboard: {url}\nLeave this window open while you use it. Press Ctrl+C to stop.\n")
    if cfg.get("open_browser", True) and not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        engine.stop()
        httpd.server_close()
        time.sleep(0.2)


if __name__ == "__main__":
    main()
