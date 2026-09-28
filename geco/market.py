"""Market math: GE tax, flip metrics, movers, high alch and decanting.

Price terms follow the Wiki API:
  high = latest instant-buy price (someone bought instantly, so sellers get this)
  low  = latest instant-sell price (someone sold instantly, so patient buyers pay this)
A flip buys near `low` and sells near `high`; tax is charged on the sell side.
"""
import re
import time

NATURE_RUNE = 561
DOSE_RE = re.compile(r"^(.*)\((\d)\)$")


class Tax:
    def __init__(self, cfg, mapping):
        # Integer basis points avoid float rounding (2% = 200 bp).
        self.bp = int(round(float(cfg.get("tax_rate", 0.02)) * 10000))
        self.cap = int(cfg.get("tax_cap", 5_000_000))
        names = {n.lower() for n in cfg.get("tax_exempt_names", [])}
        self.exempt = {iid for iid, m in mapping.items() if m["name"].lower() in names}

    def __call__(self, price, item_id=None):
        if not price or self.bp <= 0 or item_id in self.exempt:
            return 0
        # Rounds down, so items under 50 gp pay nothing at 2%.
        return min(int(price) * self.bp // 10000, self.cap)


def _pct(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b - 1.0


def build_market(mapping, latest, h1_data, stats, tax, cfg, now=None):
    """One row per tradeable item with every computed field the dashboard needs."""
    now = now or time.time()
    stale_s = cfg.get("stale_minutes", 20) * 60
    nat = latest.get(NATURE_RUNE, {}).get("high") or 0
    rows = []
    for iid, m in mapping.items():
        lt = latest.get(iid)
        if not lt:
            continue
        high, low = lt.get("high"), lt.get("low")
        ht, lt_ = lt.get("highTime"), lt.get("lowTime")
        hw = h1_data.get(iid, {})
        hv1 = hw.get("highPriceVolume") or 0
        lv1 = hw.get("lowPriceVolume") or 0
        st = stats.get(iid, {})
        vol24 = st.get("vol24") or 0
        t = tax(high, iid) if high else 0
        profit = (high - t - low) if (high and low) else None
        roi = (profit / low) if (profit is not None and low) else None
        limit = m.get("limit")
        per4h = vol24 / 6 if vol24 else (hv1 + lv1) * 4
        est_qty = min(limit, per4h) if limit else per4h
        est_qty = int(est_qty)
        ages = [now - x for x in (ht, lt_) if x]
        age = max(ages) if ages else None
        chg1 = _pct(st.get("p_now"), st.get("p_1h"))
        chg6 = _pct(st.get("p_now"), st.get("p_6h"))
        chg24 = _pct(st.get("p_now"), st.get("p_24h"))
        chg7 = _pct(st.get("p_now"), st.get("p_7d"))
        avg_prev = st.get("avg_hourly_prev") or 0
        last_v = st.get("vol_last_hour") or 0
        spike = bool(avg_prev >= 5 and last_v >= 50 and last_v >= 4 * avg_prev)
        signal = None
        if spike and chg1 is not None and chg1 >= 0.08:
            signal = "pump"
        elif spike and chg1 is not None and chg1 <= -0.08:
            signal = "dump"
        elif spike:
            signal = "spike"
        ha = m.get("highalch") or 0
        alch_profit = (ha - high - nat) if (ha and high and nat) else None
        rows.append({
            "id": iid, "name": m["name"], "icon": m.get("icon"), "members": m.get("members"),
            "limit": limit, "highalch": ha or None, "value": m.get("value"),
            "high": high, "highTime": ht, "low": low, "lowTime": lt_,
            "tax": t, "margin": (high - low) if (high and low) else None,
            "profit": profit, "roi": roi,
            "vol1h": hv1 + lv1, "hv1h": hv1, "lv1h": lv1, "vol24": vol24,
            "buyPressure": (hv1 / (hv1 + lv1)) if (hv1 + lv1) else None,
            "estQty": est_qty,
            "est4h": int(profit * est_qty) if (profit is not None and profit > 0) else None,
            "max4h": int(profit * limit) if (profit is not None and profit > 0 and limit) else None,
            "age": int(age) if age is not None else None,
            "stale": bool(age is not None and age > stale_s),
            "chg1h": chg1, "chg6h": chg6, "chg24h": chg24, "chg7d": chg7,
            "signal": signal, "alchProfit": alch_profit,
            "taxExempt": iid in tax.exempt,
        })
    return rows


def decant_opportunities(mapping, latest, tax):
    """Buy cheap low-dose potions, decant free at Bob Barter, sell as 4-dose."""
    groups = {}
    for iid, m in mapping.items():
        mt = DOSE_RE.match(m["name"])
        if not mt:
            continue
        groups.setdefault(mt.group(1).strip(), {})[int(mt.group(2))] = iid
    out = []
    for base, doses in groups.items():
        if 4 not in doses or max(doses) != 4 or len(doses) < 2:
            continue
        four = latest.get(doses[4], {})
        sell = four.get("high")
        if not sell:
            continue
        sell_net = sell - tax(sell, doses[4])
        options = []
        for d, iid in sorted(doses.items()):
            lt = latest.get(iid, {})
            buy = lt.get("low")
            if not buy:
                continue
            per_dose = buy / d
            profit4 = sell_net - per_dose * 4
            limit = mapping[iid].get("limit")
            options.append({"dose": d, "id": iid, "buy": buy, "perDose": per_dose,
                            "profitPer4": profit4, "limit": limit})
        if not options:
            continue
        best = max(options, key=lambda o: o["profitPer4"])
        if best["dose"] == 4:
            continue
        potions_per_limit = (best["limit"] or 0) * best["dose"] / 4
        out.append({
            "name": base, "sellId": doses[4], "sell4": sell, "sell4Net": sell_net,
            "best": best, "options": options,
            "profitPerLimit": int(best["profitPer4"] * potions_per_limit) if best["limit"] else None,
            "members": mapping[doses[4]].get("members"),
        })
    out.sort(key=lambda r: r["best"]["profitPer4"], reverse=True)
    return out


ALERT_KINDS = {
    "price_below": "Instant-sell price at or below",
    "price_above": "Instant-buy price at or above",
    "profit_above": "Flip profit (after tax) at or above",
    "roi_above": "ROI % at or above",
    "move_pct": "1h price move of at least %",
    "spike": "Volume spike",
}


def check_alert(alert, row):
    k, th = alert["kind"], alert["threshold"]
    if row is None:
        return None
    if k == "price_below" and row["low"] is not None and row["low"] <= th:
        return f"{row['name']} instant-sell hit {row['low']:,} gp (target {int(th):,})"
    if k == "price_above" and row["high"] is not None and row["high"] >= th:
        return f"{row['name']} instant-buy hit {row['high']:,} gp (target {int(th):,})"
    if k == "profit_above" and row["profit"] is not None and row["profit"] >= th:
        return f"{row['name']} flip profit {row['profit']:,} gp each after tax"
    if k == "roi_above" and row["roi"] is not None and row["roi"] * 100 >= th:
        return f"{row['name']} flip ROI {row['roi'] * 100:.1f}%"
    if k == "move_pct" and row["chg1h"] is not None and abs(row["chg1h"]) * 100 >= th:
        d = "up" if row["chg1h"] > 0 else "down"
        return f"{row['name']} moved {d} {abs(row['chg1h']) * 100:.1f}% in the last hour"
    if k == "spike" and row["signal"]:
        return f"{row['name']} volume spike ({row['vol1h']:,} traded last hour, flag: {row['signal']})"
    return None
