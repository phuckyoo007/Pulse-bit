"""
Trend report: every 15 minutes, logs a table of how each crypto coin has moved over the last
15 minutes and the last hour, so you can get to know how each coin behaves.

Read-only and non-fatal: it never places orders, never touches positions, and any error is
swallowed so it cannot interfere with trading. Uses the same price sources the bot already uses
(Coinbase 1-minute candles, CoinGecko 5-minute points for BNB).

Log lines start with [TREND] so they are easy to filter in Railway (filter: TREND).
"""
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import price_feed

TREND_ENABLED = True
TREND_INTERVAL_SECONDS = 900          # one report per 15-minute bucket
TREND_HISTORY_FILE = Path("trend_history.json")
TREND_HISTORY_MAX = 96                # 24 hours of reports
FLAT_BAND_PCT = 0.05                  # moves smaller than this (in %) count as flat

COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "HYPE", "NEAR", "ZEC", "BCH", "SUI", "TON", "ADA"]

_last_bucket = [None]
_running = threading.Lock()


def _interval_seconds(coin: str) -> int:
    return 60 if coin in price_feed.COINBASE_PRODUCT_MAP else 300


def _pct(a, b):
    return (a / b - 1.0) * 100.0 if b else 0.0


def _label(r: float) -> str:
    if r > FLAT_BAND_PCT:
        return "UP"
    if r < -FLAT_BAND_PCT:
        return "DOWN"
    return "FLAT"


def analyze_coin(coin: str, prices: list):
    """prices: closes, oldest first. Returns a dict or None if there is not enough data."""
    step = _interval_seconds(coin)
    n15 = 900 // step
    n60 = 3600 // step
    if not prices or len(prices) < n15 + 1:
        return None
    spot = prices[-1]
    p15 = prices[-1 - n15]
    have_hour = len(prices) >= n60 - 2
    window = prices[-(n60 + 1):] if have_hour else prices
    p60 = window[0]
    ret15 = _pct(spot, p15)
    ret60 = _pct(spot, p60)
    hi, lo = max(window), min(window)
    range_pct = _pct(hi, lo)
    pos_in_range = (spot - lo) / (hi - lo) * 100.0 if hi > lo else 50.0
    # Last four 15-minute blocks, oldest first (block return = end vs start of that block).
    blocks = []
    for k in range(4, 0, -1):
        end_i = len(prices) - 1 - (k - 1) * n15
        start_i = end_i - n15
        if start_i < 0:
            continue
        blocks.append(_pct(prices[end_i], prices[start_i]))
    ups = sum(1 for b in blocks if b > FLAT_BAND_PCT)
    downs = sum(1 for b in blocks if b < -FLAT_BAND_PCT)
    vol = price_feed.realized_volatility(window, step)
    l15, l60 = _label(ret15), _label(ret60)
    if l15 == l60 and l15 != "FLAT":
        trend = f"{l15} (15m & 1h agree)"
    elif l15 == "FLAT" and l60 == "FLAT":
        trend = "FLAT"
    else:
        trend = f"MIXED (15m {l15}, 1h {l60})"
    return {
        "coin": coin, "spot": spot, "ret15": ret15, "ret60": ret60,
        "hour_covered": have_hour, "range_pct": range_pct, "pos_in_range": pos_in_range,
        "blocks": blocks, "ups": ups, "downs": downs, "vol": vol, "trend": trend,
    }


def _fmt_price(p: float) -> str:
    if p >= 1000:
        return f"{p:,.0f}"
    if p >= 10:
        return f"{p:.2f}"
    if p >= 1:
        return f"{p:.3f}"
    return f"{p:.4f}"


def build_report(rows: list) -> str:
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    lines = [f"[TREND] ===== crypto trends @ {now} (15m and 1h windows) ====="]
    for r in sorted(rows, key=lambda x: x["ret60"], reverse=True):
        blk = " ".join(f"{b:+.2f}" for b in r["blocks"])
        vol = f"{r['vol'] * 100:.0f}%" if r["vol"] is not None else "n/a"
        hr = "" if r["hour_covered"] else " (<1h data)"
        lines.append(
            f"[TREND] {r['coin']:<5} ${_fmt_price(r['spot']):>9}  15m {r['ret15']:+.2f}%  1h {r['ret60']:+.2f}%{hr}  "
            f"1h-range {r['range_pct']:.2f}% (at {r['pos_in_range']:.0f}% of range)  vol {vol}  "
            f"last 4x15m [{blk}] {r['ups']}up/{r['downs']}dn  -> {r['trend']}")
    up = sum(1 for r in rows if r["trend"].startswith("UP"))
    dn = sum(1 for r in rows if r["trend"].startswith("DOWN"))
    mx = sum(1 for r in rows if r["trend"].startswith("MIXED"))
    fl = sum(1 for r in rows if r["trend"] == "FLAT")
    avg15 = sum(r["ret15"] for r in rows) / len(rows)
    avg60 = sum(r["ret60"] for r in rows) / len(rows)
    lines.append(f"[TREND] market: {up} up / {dn} down / {mx} mixed / {fl} flat   "
                 f"avg 15m {avg15:+.2f}%  avg 1h {avg60:+.2f}%")
    return "\n".join(lines)


def _save_history(rows: list):
    try:
        try:
            hist = json.loads(TREND_HISTORY_FILE.read_text())
        except Exception:
            hist = []
        hist.append({"t": time.time(), "coins": {r["coin"]: {
            "spot": r["spot"], "ret15": round(r["ret15"], 4), "ret60": round(r["ret60"], 4),
            "trend": r["trend"]} for r in rows}})
        TREND_HISTORY_FILE.write_text(json.dumps(hist[-TREND_HISTORY_MAX:]))
    except Exception:
        pass


def run_report():
    if not _running.acquire(blocking=False):
        return
    try:
        rows = []
        for coin in COINS:
            try:
                prices = price_feed.get_recent_prices(coin, max_age_seconds=20)
                res = analyze_coin(coin, prices) if prices else None
                if res:
                    rows.append(res)
            except Exception:
                continue
        if rows:
            print(build_report(rows), flush=True)
            _save_history(rows)
        else:
            print("[TREND] no price data available this round", flush=True)
    except Exception as e:
        print(f"[TREND] report failed (non-fatal): {e}", flush=True)
    finally:
        _running.release()


def maybe_run():
    """Call every loop iteration. Starts a background report once per 15-minute bucket."""
    if not TREND_ENABLED:
        return
    bucket = int(time.time() // TREND_INTERVAL_SECONDS)
    if _last_bucket[0] == bucket:
        return
    _last_bucket[0] = bucket
    threading.Thread(target=run_report, daemon=True).start()
