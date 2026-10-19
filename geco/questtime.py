"""How long a quest takes.

Three layers:
1. The Wiki's length rating (Very Short to Very Long), turned into typical minutes.
2. The quick guide itself: steps, teleports, fights, dialogue, items to bring and words. A
   small ridge regression fitted across every quest maps those counts to the rating's minutes,
   so two "Long" quests get different estimates. The estimate stays inside the rating's range.
3. Your pace: quests the plugin saw from start to finish, active time only (time logged in),
   against their estimate. The median ratio scales every estimate once there are two or more.
"""
import math
import threading

# Rating: (typical minutes, low, high)
LENGTHS = {"very short": (8, 3, 15), "short": (18, 8, 30), "medium": (35, 18, 60),
           "long": (75, 45, 120), "very long": (180, 100, 360)}
FEATURES = ("steps", "teleports", "fights", "chats", "words", "items", "difficulty")
DIFF = {"novice": 0, "intermediate": 1, "experienced": 2, "master": 3, "grandmaster": 4}
_model = {}
_lock = threading.Lock()


def rating(length):
    """(typical, low, high) minutes for a Wiki length like "Long" or "Short - Medium"."""
    parts = [p.strip().lower() for p in (length or "").replace("\u2013", "-").split("-") if p.strip()]
    vals = [LENGTHS[p] for p in parts if p in LENGTHS]
    if not vals:
        return None
    return (math.exp(sum(math.log(v[0]) for v in vals) / len(vals)), min(v[1] for v in vals), max(v[2] for v in vals))


def features(q):
    g = q.get("guide") or {}
    return [math.log1p(g.get("steps", 0)), math.log1p(g.get("teleports", 0)), math.log1p(g.get("fights", 0)),
            math.log1p(g.get("chats", 0)), math.log1p(g.get("words", 0)), math.log1p(len(q.get("items") or [])),
            DIFF.get((q.get("difficulty") or "").lower(), 1)]


def _solve(A, b):
    """Gaussian elimination for a small dense system."""
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        if abs(M[c][c]) < 1e-12:
            continue
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                for k in range(c, n + 1):
                    M[r][k] -= f * M[c][k]
    return [M[i][n] / M[i][i] if abs(M[i][i]) > 1e-12 else 0.0 for i in range(n)]


def fit(quests, lam=1.0):
    """Ridge regression of log(typical minutes for the rating) on the guide features."""
    X, y = [], []
    for q in quests.values():
        r = rating(q.get("length"))
        if r and (q.get("guide") or {}).get("steps"):
            X.append([1.0] + features(q))
            y.append(math.log(r[0]))
    if len(X) < 20:
        return None
    k = len(X[0])
    mu = [sum(x[j] for x in X) / len(X) for j in range(k)]
    sd = [math.sqrt(sum((x[j] - mu[j]) ** 2 for x in X) / len(X)) or 1.0 for j in range(k)]
    Z = [[1.0] + [(x[j] - mu[j]) / sd[j] for j in range(1, k)] for x in X]
    A = [[sum(z[i] * z[j] for z in Z) + (lam if i == j and i > 0 else 0) for j in range(k)] for i in range(k)]
    b = [sum(z[i] * yy for z, yy in zip(Z, y)) for i in range(k)]
    w = _solve(A, b)
    pred = [sum(wi * zi for wi, zi in zip(w, z)) for z in Z]
    resid = [p - yy for p, yy in zip(pred, y)]
    rmse = math.sqrt(sum(r * r for r in resid) / len(resid))
    base = math.sqrt(sum((yy - sum(y) / len(y)) ** 2 for yy in y) / len(y))
    return {"w": w, "mu": mu, "sd": sd, "rmse": rmse, "base": base, "n": len(X)}


def model(quests):
    key = len(quests)
    with _lock:
        if _model.get("key") != key:
            _model.update(key=key, m=fit(quests))
        return _model["m"]


def estimate(q, quests, pace=1.0):
    """{minutes, low, high, source} for one quest."""
    r = rating(q.get("length"))
    m = model(quests)
    if m and (q.get("guide") or {}).get("steps"):
        x = [1.0] + features(q)
        z = [1.0] + [(x[j] - m["mu"][j]) / m["sd"][j] for j in range(1, len(x))]
        mins = math.exp(sum(wi * zi for wi, zi in zip(m["w"], z)))
        if r:
            mins = min(max(mins, r[1]), r[2])
        spread = math.exp(m["rmse"])
        source = "guide"
    elif r:
        mins, spread, source = r[0], 1.6, "rating"
    else:
        mins, spread, source = 20.0, 2.0, "default"
    mins *= pace
    return {"minutes": round(mins), "low": round(mins / spread), "high": round(mins * spread), "source": source}


def pace(db, acct, quests, sessions):
    """Your speed vs the estimates: median of (active minutes / estimate) over timed quests."""
    from . import account
    from .quests import norm
    by = {norm(k): v for k, v in quests.items()}
    ratios = []
    for r in account.quest_times(db, acct):
        q = by.get(norm(r["name"]))
        if not q or r["finished"] <= r["started"]:
            continue
        active = 0
        for s in sessions:
            a, b = max(s["start"], r["started"]), min(s["last"], r["finished"])
            if b > a:
                active += b - a
        if not sessions:
            active = r["finished"] - r["started"]
        est = estimate(q, quests)["minutes"]
        if active > 120 and est:
            ratios.append(active / 60 / est)
    if len(ratios) < 2:
        return {"factor": 1.0, "n": len(ratios)}
    ratios.sort()
    k = len(ratios) // 2
    med = ratios[k] if len(ratios) % 2 else (ratios[k - 1] + ratios[k]) / 2
    return {"factor": min(2.5, max(0.4, med)), "n": len(ratios)}
