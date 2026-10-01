"""Bankstanding: start the local price engine and open the dashboard.

Usage:
    python run.py           live data from the OSRS Wiki
    python run.py --demo    offline demo with synthetic prices
    python run.py --no-browser
Windows desktop use (see install.bat):
    --install / --uninstall   start with Windows plus desktop and Start menu icons
    --background              run with no window, logging to data/bankstanding.log
    --open                    start in the background if needed, then open the dashboard
    --runelite                start if needed, then open RuneLite with the plugin
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
    ap = argparse.ArgumentParser(description="Bankstanding")
    ap.add_argument("--demo", action="store_true", help="use offline synthetic data")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--port", type=int)
    ap.add_argument("--background", action="store_true", help="no window; log to data/bankstanding.log")
    ap.add_argument("--open", action="store_true", help="start in the background if needed, open the dashboard")
    ap.add_argument("--runelite", action="store_true", help="start if needed, open RuneLite with the plugin")
    ap.add_argument("--install", action="store_true", help="start with Windows, add desktop and Start menu icons")
    ap.add_argument("--uninstall", action="store_true", help="undo --install (keeps your data)")
    args = ap.parse_args()

    cfg = config.load()
    port = args.port or int(cfg.get("port", 8765))
    if args.install or args.uninstall or args.open or args.runelite:
        from geco import desktop
        if args.install:
            sys.exit(desktop.install(port))
        if args.uninstall:
            sys.exit(desktop.uninstall())
        if args.open:
            sys.exit(0 if desktop.open_dashboard(port) else 1)
        sys.exit(desktop.launch_runelite(port))
    if args.background:
        from geco import desktop
        if desktop.running(port):
            sys.exit(0)  # already running (single instance)
        os.makedirs(config.DATA_DIR, exist_ok=True)
        try:
            if os.path.getsize(desktop.LOG) > 5_000_000:
                os.replace(desktop.LOG, desktop.LOG + ".old")
        except OSError:
            pass
        log = open(desktop.LOG, "a", buffering=1, encoding="utf-8", errors="replace")
        sys.stdout = sys.stderr = log
        os.environ["BANKSTANDING_BACKGROUND"] = "1"
        print(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} starting in the background ====")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S",
                        stream=sys.stdout)

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
    print("Bankstanding: loading item list and prices from the OSRS Wiki..." if not args.demo
          else "Bankstanding: demo mode (synthetic prices)")
    while True:
        try:
            engine.start()
            break
        except Exception as e:
            print(f"\nCould not load the item list: {e}\n"
                  "Check your internet connection, then try again. "
                  "(Run with --demo to see the dashboard offline.)")
            if not args.background:
                sys.exit(1)
            time.sleep(60)  # started with Windows before the network was up: keep trying

    try:
        httpd = serve(App(cfg, engine, db, client), port)
    except OSError:
        print(f"\nPort {port} is busy. Bankstanding may already be running: "
              f"open http://127.0.0.1:{port} in your browser.")
        sys.exit(1)

    url = f"http://127.0.0.1:{port}"
    print(f"\nDashboard: {url}" + ("" if args.background else "\nLeave this window open while you use it. Press Ctrl+C to stop.\n"))
    if cfg.get("lan_enabled") and len(cfg.get("lan_password") or "") >= 8:
        from geco.server import lan_addresses
        for ip in lan_addresses():
            print(f"On your phone (same Wi-Fi): http://{ip}:{port}  (password from Settings)")
    if cfg.get("open_browser", True) and not args.no_browser and not args.background:
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
