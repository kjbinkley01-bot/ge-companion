"""Market math: GE tax, flip metrics, movers, high alch, decanting and alert checks.

Price terms follow the Wiki API:
  high = latest instant-buy price (someone bought instantly, so sellers get this)
  low  = latest instant-sell price (someone sold instantly, so patient buyers pay this)
A flip buys near `low` and sells near `high`; tax is charged on the sell side.

Fills: a patient buy offer at `low` only fills when someone instant-sells, which is the
low-side volume (`lv`). The matching sell offer at `high` fills against instant buys
(`hv`). Other flippers compete for the same trades, so only a share of that volume is
assumed to be yours (`fill_share` in the config).
"""
import json
import re
import time

from .categories import JEWELLERY_WORDS

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

    def net(self, price, item_id=None):
        """What a sale at `price` puts in your pocket after tax."""
        return (price - self(price, item_id)) if price else None

    def breakeven(self, buy, item_id=None):
        """Lowest sell price that at least returns `buy` after tax."""
        if buy is None:
            return None
        # price - tax(price) never decreases as price rises, so binary search works.
        lo, hi = int(buy), int(buy) * 2 + 2
        while lo < hi:
            mid = (lo + hi) // 2
            if mid - self(mid, item_id) >= buy:
                hi = mid
            else:
                lo = mid + 1
        return lo


def _pct(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b - 1.0


def fill_model(limit, lv24, hv24, lv1h, hv1h, share):
    """Items per hour you can realistically buy and sell, and what fits in one limit window.

    Uses 24h volume when history is loaded, otherwise the latest hourly window.
    Returns (buy_rate, sell_rate, qty_4h, hours_to_fill_limit).
    """
    if lv24 or hv24:
        buy_rate = share * (lv24 or 0) / 24.0
        sell_rate = share * (hv24 or 0) / 24.0
    else:
        buy_rate = share * (lv1h or 0)
        sell_rate = share * (hv1h or 0)
    rate = min(buy_rate, sell_rate)
    qty = rate * 4
    if limit:
        qty = min(limit, qty)
    fill_hrs = (limit / rate) if (limit and rate > 0) else None
    return buy_rate, sell_rate, int(qty), fill_hrs


def build_market(mapping, latest, h1_data, stats, tax, cfg, m5=None, m5_windows=0, now=None, share_fn=None):
    """One row per tradeable item with every computed field the dashboard needs."""
    now = now or time.time()
    m5 = m5 or {}
    stale_s = cfg.get("stale_minutes", 20) * 60
    trap_s = cfg.get("trap_gap_minutes", 15) * 60
    share = max(0.01, min(1.0, float(cfg.get("fill_share", 0.2))))
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
        hv24, lv24 = st.get("hv24") or 0, st.get("lv24") or 0
        exempt = iid in tax.exempt
        t = tax(high, iid) if high else 0
        profit = (high - t - low) if (high and low) else None
        roi = (profit / low) if (profit is not None and low) else None
        limit = m.get("limit")
        item_share, share_src = share_fn(iid) if share_fn else (share, "setting")
        buy_rate, sell_rate, est_qty, fill_hrs = fill_model(limit, lv24, hv24, lv1, hv1, item_share)
        ages = [now - x for x in (ht, lt_) if x]
        age = max(ages) if ages else None
        gap = abs(ht - lt_) if (ht and lt_) else None
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
        # Margin stability: share of recent 5 minute windows where the flip worked.
        ms = m5.get(iid)
        stability = cv = avg_margin = None
        if ms and m5_windows >= 6:
            held = ms["held_raw"] if exempt else ms["held"]
            stability = min(1.0, held / m5_windows)
            cv = ms["cv"]
            avg_margin = ms["avg_margin_raw"] if exempt else ms["avg_margin"]
        trap = False
        if profit is not None and profit > 0:
            if gap is not None and gap > trap_s:
                trap = True
            elif ms and ms["both"] >= 6 and avg_margin is not None and avg_margin <= 0:
                trap = True
        est4h = int(profit * est_qty) if (profit is not None and profit > 0) else None
        adj = None
        if est4h is not None:
            adj = int(est4h * (stability if stability is not None else 0.5) * (0.25 if trap else 1))
        ha = m.get("highalch") or 0
        alch_profit = (ha - high - nat) if (ha and high and nat) else None
        rows.append({
            "id": iid, "name": m["name"], "icon": m.get("icon"), "members": m.get("members"),
            "limit": limit, "highalch": ha or None, "value": m.get("value"),
            "high": high, "highTime": ht, "low": low, "lowTime": lt_,
            "tax": t, "margin": (high - low) if (high and low) else None,
            "profit": profit, "roi": roi,
            "vol1h": hv1 + lv1, "hv1h": hv1, "lv1h": lv1,
            "vol24": vol24, "hv24": hv24, "lv24": lv24,
            "buyPressure": (hv1 / (hv1 + lv1)) if (hv1 + lv1) else None,
            "estQty": est_qty, "buyRate": round(buy_rate, 2), "sellRate": round(sell_rate, 2),
            "fillShare": item_share, "fillFrom": share_src,
            "fillHrs": round(fill_hrs, 2) if fill_hrs is not None else None,
            "capital": int(low * est_qty) if (low and est_qty) else None,
            "est4h": est4h, "adj4h": adj,
            "max4h": int(profit * limit) if (profit is not None and profit > 0 and limit) else None,
            "age": int(age) if age is not None else None,
            "stale": bool(age is not None and age > stale_s),
            "gap": int(gap) if gap is not None else None, "trap": trap,
            "stability": round(stability, 3) if stability is not None else None,
            "volatility": round(cv, 4) if cv is not None else None,
            "avgMargin5m": int(avg_margin) if avg_margin is not None else None,
            "chg1h": chg1, "chg6h": chg6, "chg24h": chg24, "chg7d": chg7,
            "signal": signal, "alchProfit": alch_profit,
            "taxExempt": exempt,
        })
    return rows


def decant_opportunities(mapping, latest, tax):
    """Buy cheap low-dose potions, decant free at Bob Barter, sell as 4-dose."""
    groups = {}
    for iid, m in mapping.items():
        mt = DOSE_RE.match(m["name"])
        if not mt or int(mt.group(2)) == 0:
            continue  # "(0)" is an empty item, not a dose
        base = mt.group(1).strip()
        if any(w in base.lower() for w in JEWELLERY_WORDS + ("waterskin",)):
            continue  # Bob Barter only decants potions, not charged jewellery or waterskins
        groups.setdefault(base, {})[int(mt.group(2))] = iid
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


# Alerts --------------------------------------------------------------------------

ALERT_KINDS = {
    "price_below": "Instant-sell price at or below",
    "price_above": "Instant-buy price at or above",
    "profit_above": "Flip profit (after tax) at or above",
    "roi_above": "ROI % at or above",
    "move_pct": "1h price move of at least %",
    "drop_pct": "1h price drop of at least %",
    "rise_pct": "1h price rise of at least %",
    "vol_above": "Last hour volume at or above",
    "stability_above": "Margin stability % at or above",
    "spike": "Volume spike",
    "dump": "Dump flag (volume spike with a sharp drop)",
}
# Kinds whose threshold is a percentage (shown with a % sign in the UI).
PCT_KINDS = {"roi_above", "move_pct", "drop_pct", "rise_pct", "stability_above"}
NO_VALUE_KINDS = {"spike", "dump"}


def _cond(kind, th, row):
    """A short phrase when the condition holds for this row, else None."""
    if kind == "price_below" and row["low"] is not None and row["low"] <= th:
        return f"instant-sell hit {row['low']:,} gp (target {int(th):,})"
    if kind == "price_above" and row["high"] is not None and row["high"] >= th:
        return f"instant-buy hit {row['high']:,} gp (target {int(th):,})"
    if kind == "profit_above" and row["profit"] is not None and row["profit"] >= th:
        return f"flip profit {row['profit']:,} gp each after tax"
    if kind == "roi_above" and row["roi"] is not None and row["roi"] * 100 >= th:
        return f"flip ROI {row['roi'] * 100:.1f}%"
    c = row.get("chg1h")
    if kind == "move_pct" and c is not None and abs(c) * 100 >= th:
        d = "up" if c > 0 else "down"
        return f"moved {d} {abs(c) * 100:.1f}% in the last hour"
    if kind == "drop_pct" and c is not None and -c * 100 >= th:
        return f"fell {abs(c) * 100:.1f}% in the last hour"
    if kind == "rise_pct" and c is not None and c * 100 >= th:
        return f"rose {c * 100:.1f}% in the last hour"
    if kind == "vol_above" and row.get("vol1h") is not None and row["vol1h"] >= th:
        return f"{row['vol1h']:,} traded last hour"
    s = row.get("stability")
    if kind == "stability_above" and s is not None and s * 100 >= th:
        return f"margin held in {s * 100:.0f}% of recent 5 minute windows"
    if kind == "spike" and row["signal"]:
        return f"volume spike ({row['vol1h']:,} traded last hour, flag: {row['signal']})"
    if kind == "dump" and row["signal"] == "dump":
        return f"dump flag ({row['vol1h']:,} traded last hour, {abs(c or 0) * 100:.1f}% down)"
    return None


def alert_conditions(alert):
    """[(kind, threshold)] for an alert row, including any extra AND conditions."""
    conds = [(alert["kind"], alert["threshold"] or 0)]
    extra = alert.get("extra")
    if extra:
        try:
            for c in json.loads(extra):
                if c.get("kind") in ALERT_KINDS:
                    conds.append((c["kind"], float(c.get("threshold") or 0)))
        except (ValueError, TypeError, AttributeError):
            pass
    return conds


def check_alert(alert, row):
    """Message when every condition of the alert holds for this item, else None."""
    if row is None:
        return None
    parts = []
    for kind, th in alert_conditions(alert):
        p = _cond(kind, th, row)
        if p is None:
            return None
        parts.append(p)
    return f"{row['name']} " + ", and ".join(parts)
