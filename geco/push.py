"""Phone notifications through services you set up yourself (all optional, off by default).

  Discord   a webhook URL for a channel of yours (Server Settings, Integrations, Webhooks)
  ntfy      a topic name on ntfy.sh (or your own ntfy server); install the ntfy app on your
            phone and subscribe to the same topic. Pick a long random topic: anyone who knows
            it can read the messages.

Which events are sent is chosen in Settings: GE fills, alerts, offer coach warnings, buy limit
resets and the daily bank statement. Messages are sent from a background thread so a slow
service never holds up the app, and a failure is logged, never retried in a loop.
"""
import json
import logging
import threading
import urllib.error
import urllib.request

log = logging.getLogger("bankstanding.push")
EVENTS = {"fills": "GE offers filling", "alerts": "Price alerts", "coach": "Offer coach warnings",
          "limits": "Buy limit resets", "statement": "Daily bank statement"}


def enabled(cfg, event):
    if not (cfg.get("push_discord_webhook") or cfg.get("push_ntfy_topic")):
        return False
    return bool((cfg.get("push_events") or {}).get(event, event in ("alerts", "coach", "statement")))


def _post(url, data, headers, user_agent):
    req = urllib.request.Request(url, data=data, headers=dict(headers, **{"User-Agent": user_agent}), method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status


def send(cfg, title, message, event=None, force=False, wait=False):
    """Send to every configured service. Returns a list of (service, ok, detail) when wait=True."""
    if not force and event and not enabled(cfg, event):
        return []
    ua = cfg.get("user_agent", "Bankstanding")
    results = []

    def work():
        hook = (cfg.get("push_discord_webhook") or "").strip()
        if hook:
            try:
                if not hook.startswith("https://"):
                    raise ValueError("the webhook must start with https://")
                body = json.dumps({"username": "Bankstanding", "content": f"**{title}**\n{message}"[:1900]}).encode()
                _post(hook, body, {"Content-Type": "application/json"}, ua)
                results.append(("discord", True, "sent"))
            except (urllib.error.URLError, ValueError, OSError) as e:
                log.warning("Discord notification failed: %s", e)
                results.append(("discord", False, str(e)))
        topic = (cfg.get("push_ntfy_topic") or "").strip()
        if topic:
            server = (cfg.get("push_ntfy_server") or "https://ntfy.sh").rstrip("/")
            try:
                if not server.startswith("https://") and not server.startswith("http://"):
                    raise ValueError("the ntfy server must be a web address")
                _post(f"{server}/{topic}", message.encode("utf-8"),
                      {"Title": title.encode("ascii", "ignore").decode(), "Tags": "moneybag"}, ua)
                results.append(("ntfy", True, "sent"))
            except (urllib.error.URLError, ValueError, OSError) as e:
                log.warning("ntfy notification failed: %s", e)
                results.append(("ntfy", False, str(e)))
    if wait:
        work()
        return results
    threading.Thread(target=work, daemon=True, name="push").start()
    return []
