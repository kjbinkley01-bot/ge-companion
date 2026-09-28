"""Read-only clients for the OSRS Wiki real-time prices API and the official hiscores.

Nothing here talks to the game client. Every request is a plain HTTPS GET to a
public web API, sent with a descriptive User-Agent as the Wiki asks.
"""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

PRICES = "https://prices.runescape.wiki/api/v2/osrs"
HISCORE_BASE = "https://secure.runescape.com/m={board}/index_lite.json?player={player}"
HISCORE_BOARDS = {
    "normal": "hiscore_oldschool",
    "ironman": "hiscore_oldschool_ironman",
    "hardcore": "hiscore_oldschool_hardcore_ironman",
    "ultimate": "hiscore_oldschool_ultimate",
}
VALID_LOOKBACKS = ("6h", "24h", "7d", "30d", "6m", "1y")


class ApiError(Exception):
    pass


class WikiClient:
    def __init__(self, user_agent):
        self.user_agent = user_agent
        self._cache = {}
        self._lock = threading.Lock()

    def _get(self, url, timeout=30):
        req = urllib.request.Request(url, headers={
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise ApiError(f"HTTP {e.code} from {url.split('?')[0]}") from e
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise ApiError(f"Could not reach {url.split('?')[0]}: {e}") from e

    def _cached(self, key, ttl, fn):
        now = time.time()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
        val = fn()
        with self._lock:
            self._cache[key] = (now, val)
        return val

    # Prices -----------------------------------------------------------------
    def mapping(self):
        return self._get(f"{PRICES}/mapping")

    def latest(self):
        return self._get(f"{PRICES}/latest")["data"]

    def five_min(self, timestamp=None):
        q = f"?timestamp={int(timestamp)}" if timestamp else ""
        return self._get(f"{PRICES}/5m{q}")

    def one_hour(self, timestamp=None):
        q = f"?timestamp={int(timestamp)}" if timestamp else ""
        return self._get(f"{PRICES}/1h{q}")

    def one_day(self, timestamp):
        """Daily averages and volumes for every item, for the day starting at `timestamp`."""
        return self._get(f"{PRICES}/24h?timestamp={int(timestamp)}")

    def timeseries(self, item_id, lookback):
        if lookback not in VALID_LOOKBACKS:
            raise ApiError("bad lookback")
        key = ("ts", int(item_id), lookback)
        ttl = 60 if lookback in ("6h", "24h") else 600
        return self._cached(key, ttl, lambda: self._get(
            f"{PRICES}/timeseries?id={int(item_id)}&lookback={lookback}"))

    # Hiscores ---------------------------------------------------------------
    def hiscores(self, player, mode="normal"):
        board = HISCORE_BOARDS.get(mode, HISCORE_BOARDS["normal"])
        url = HISCORE_BASE.format(board=board, player=urllib.parse.quote(player))
        try:
            return self._get(url, timeout=20)
        except ApiError as e:
            if "HTTP 404" in str(e):
                raise ApiError(f"No {mode} hiscores entry for '{player}'. "
                               "Check the spelling, or the player may be unranked.") from e
            raise
