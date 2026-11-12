"""
Ledger (12 Nov 02:00 CT, Matt: "track wins and losses -- not money I deposit as a win").

Every LEDGER_EVERY seconds it asks Kalshi for:
  * cash balance and the value of open positions  -> total account value
  * every deposit (and withdrawal, if Kalshi reports them)
and logs the only number that can't be fooled by deposits:

    TRADING P/L = account value now - all money ever deposited + all money ever withdrawn

plus the same thing since the bot started (account value change minus deposits made since then).
Read-only -- never trades.
"""
import json
import time
from datetime import datetime, timezone, timedelta

LEDGER_ENABLED = True
LEDGER_EVERY = 900   # 15 min
_last = [0.0]
_start = {}          # {"t": ts, "value": dollars, "dep": dollars, "wd": dollars}
_logged_raw = [False]


def _d(v):
    """Kalshi amounts: *_dollars strings, or integer cents."""
    try:
        return float(v)
    except Exception:
        return None


def _money(obj, *keys):
    for k in keys:
        if obj.get(k + "_dollars") is not None:
            return _d(obj.get(k + "_dollars"))
    for k in keys:
        if obj.get(k + "_cents") is not None:
            v = _d(obj.get(k + "_cents"))
            return None if v is None else v / 100.0
    for k in keys:
        if obj.get(k) is not None:
            v = _d(obj.get(k))
            return None if v is None else v / 100.0
    return None


def _ts(s):
    if isinstance(s, (int, float)):
        return float(s)
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _all(client, path, key):
    out, cursor = [], None
    for _ in range(50):
        params = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        r = client._request("GET", path, params=params)
        out += r.get(key, []) or []
        cursor = r.get("cursor")
        if not cursor:
            break
    return out


def _sum_moves(items):
    total, rows = 0.0, []
    for d in items:
        status = str(d.get("status", "")).lower()
        if status and status not in ("completed", "complete", "success", "succeeded", "settled", "applied", "done"):
            continue
        amt = _money(d, "amount")
        if amt is None:
            continue
        t = _ts(d.get("created_ts") or d.get("created_time") or d.get("timestamp"))
        total += amt
        rows.append((t, amt))
    return total, rows


def run(client) -> None:
    if not LEDGER_ENABLED:
        return
    now = time.time()
    if now - _last[0] < LEDGER_EVERY:
        return
    _last[0] = now
    try:
        time.sleep(0.5)
        bal = client.get_balance_cached(max_age=30)
        cash = _money(bal, "balance")
        pv = _money(bal, "portfolio_value")
        if pv is None:
            pv = 0.0
            try:
                pos = client.get_positions()
                for p in pos.get("market_positions", []) or []:
                    pv += abs(_money(p, "market_exposure") or 0.0)
            except Exception:
                pass
        deps = _all(client, "/portfolio/deposits", "deposits")
        try:
            wds = _all(client, "/portfolio/withdrawals", "withdrawals")
        except Exception:
            wds = []
        if not _logged_raw[0]:
            _logged_raw[0] = True
            print("[LEDGER] raw balance fields: " + json.dumps(bal)[:400])
            if deps:
                print("[LEDGER] raw deposit example: " + json.dumps(deps[0])[:400])
        if not _start:   # once per start: show the latest deposits/withdrawals exactly as Kalshi reports them
            def _row(d):
                t = _ts(d.get("created_ts") or d.get("created_time"))
                when = (datetime.fromtimestamp(t, timezone.utc) - timedelta(hours=6)).strftime("%b %d %H:%M CT") if t else "?"
                return f"{when} ${(_money(d, 'amount') or 0):.2f} {d.get('type', '')} {d.get('status', '')}"
            recent = sorted(deps, key=lambda d: -_ts(d.get("created_ts") or d.get("created_time")))[:15]
            print("[LEDGER] latest deposits: " + " | ".join(_row(d) for d in recent))
            if wds:
                recent_w = sorted(wds, key=lambda d: -_ts(d.get("created_ts") or d.get("created_time")))[:10]
                print("[LEDGER] latest withdrawals: " + " | ".join(_row(d) for d in recent_w))
        dep_total, dep_rows = _sum_moves(deps)
        wd_total, wd_rows = _sum_moves(wds)
        value = (cash or 0.0) + pv
        alltime = value - dep_total + wd_total
        if not _start:
            _start.update({"t": now, "value": value, "dep": dep_total, "wd": wd_total})
        dep_since = dep_total - _start["dep"]
        wd_since = wd_total - _start["wd"]
        since = (value - _start["value"]) - dep_since + wd_since
        day0 = datetime.now(timezone.utc).replace(hour=6, minute=0, second=0, microsecond=0).timestamp()   # midnight CT
        if day0 > now:
            day0 -= 86400
        dep_today = sum(a for t, a in dep_rows if t >= day0)
        print(f"[LEDGER] account ${value:.2f} (cash ${cash or 0:.2f} + open bets ${pv:.2f}) | deposited all-time "
              f"${dep_total:.2f} ({len(dep_rows)} deposits, ${dep_today:.2f} today), withdrawn ${wd_total:.2f} | "
              f"TRADING P/L all-time ${alltime:+.2f} | since bot start "
              f"{datetime.fromtimestamp(_start['t'], timezone.utc).strftime('%H:%MZ')}: ${since:+.2f} "
              f"(deposits in that time ${dep_since:.2f} not counted)")
    except Exception as e:
        _last[0] = now - LEDGER_EVERY + 60   # try again in a minute (startup is busy -- Kalshi rate limit)
        print(f"[LEDGER] check failed ({e}) -- retrying in 60s.")
