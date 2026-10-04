"""
Aggregates trades.json, settlements.json, and scan.json into
dashboard_data.json for dashboard.html.

Run whenever you want fresher numbers:
    python3 build_dashboard_data.py
"""
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

TRADES_FILE = Path("trades.json")
SETTLEMENTS_FILE = Path("settlements.json")
SCAN_FILE = Path("scan.json")
STARTING_BANKROLL = 10.0
OUT_FILE = Path("dashboard_data.json")


def load(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"WARNING: {path} is corrupted ({e}) — treating it as empty for this build.")
        return default


def build():
    trades = load(TRADES_FILE, [])
    all_settlements = load(SETTLEMENTS_FILE, [])
    scan = load(SCAN_FILE, {"results": [], "bankroll": STARTING_BANKROLL, "generated_at": None})
    state_data = load(Path("state.json"), {})
    now = datetime.now(timezone.utc)

    # Only count settlements explicitly marked dry_run=False. Older
    # records with no marker default to EXCLUDED -- same fix already
    # applied to risk.py and Omni's dashboard builder, after old
    # phantom-bankroll test settlements got silently summed into a real
    # display and produced a nonsense bankroll figure next to a real
    # ~$10 account.
    settlements = [s for s in all_settlements if s.get("dry_run") is False]
    excluded_count = len(all_settlements) - len(settlements)
    if excluded_count:
        print(f"Excluded {excluded_count} settlement(s) without a confirmed dry_run=False marker (old/test data).")

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    today_pnl = sum(s["pnl"] for s in settlements if s["timestamp"] >= today_start)
    total_pnl = sum(s["pnl"] for s in settlements)
    wins = len([s for s in settlements if s["pnl"] > 0])
    stop_loss_count = len([s for s in settlements if s.get("exit_reason", "").startswith("stop_loss")])

    running = STARTING_BANKROLL
    series = []
    for s in sorted(settlements, key=lambda x: x["timestamp"]):
        running += s["pnl"]
        series.append({"time": s["timestamp"], "value": round(running, 2)})
    if not series:
        series = [{"time": now.timestamp(), "value": STARTING_BANKROLL}]

    by_coin = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for s in settlements:
        coin = next((t.get("coin") for t in trades if t["ticker"] == s["ticker"]), "?")
        row = by_coin[coin]
        row["trades"] += 1
        row["pnl"] += s["pnl"]
        if s["pnl"] > 0:
            row["wins"] += 1

    coin_table = [
        {"coin": coin, "trades": row["trades"], "wins": row["wins"],
         "win_rate": round(row["wins"] / row["trades"] * 100, 1) if row["trades"] else 0,
         "pnl": round(row["pnl"], 2)}
        for coin, row in by_coin.items()
    ]

    # Per-position status (green/yellow/orange), ported from Omni --
    # this is what the dashboard's winning-highlight and firefly
    # animation depend on, and Pulse never had it before.
    scan_by_ticker = {r["ticker"]: r for r in scan.get("results", [])}
    open_positions_detail = []
    for ticker, position in state_data.items():
        entry_price = position.get("entry_price", 0)
        side = position.get("side", "bid")
        live = scan_by_ticker.get(ticker)
        current_price = live["market_price"] if live else None
        value_now = None
        pct_change = None
        status = "unknown"
        if current_price is not None and entry_price:
            value_now = current_price if side == "bid" else (1 - current_price)
            pct_change = (value_now - entry_price) / entry_price * 100
            if pct_change >= 5:
                status = "green"
            elif pct_change >= -15:
                status = "yellow"
            else:
                status = "orange"
        count = position.get("count", 0)
        dollar_pnl = None
        if value_now is not None and entry_price:
            dollar_pnl = (value_now - entry_price) * count
        entry_time = position.get("entry_time")   # None for positions opened before this was tracked

        open_positions_detail.append({
            "ticker": ticker,
            "title": live["title"] if live else ticker,
            "coin": position.get("coin", live["coin"] if live else "?"),
            "side": side,
            "count": count,
            "entry_price": entry_price,
            "entry_time": entry_time,
            "current_value": round(value_now, 4) if value_now is not None else None,
            "pct_change": round(pct_change, 1) if pct_change is not None else None,
            "dollar_pnl": round(dollar_pnl, 2) if dollar_pnl is not None else None,
            "status": status,
        })
    open_positions_detail.sort(key=lambda p: (p["pct_change"] is None, p["pct_change"] if p["pct_change"] is not None else 0))
    status_by_ticker = {p["ticker"]: p["status"] for p in open_positions_detail}

    # Sum of open positions' CURRENT value (not entry price), per
    # explicit request -- dashboard bankroll now shows cash + this,
    # not cash alone. Positions with unknown current value (e.g. price
    # feed missed this cycle) contribute 0 rather than crashing or
    # silently understating equity with a None.
    open_positions_value = sum(
        (p["current_value"] * p["count"]) for p in open_positions_detail
        if p["current_value"] is not None
    )

    # Recent real closes with their actual PnL, per explicit request --
    # most recent first, capped at 15 so the feed stays a manageable size.
    recent_closes = sorted(settlements, key=lambda s: s["timestamp"], reverse=True)[:15]
    recent_closes = [{
        "ticker": s["ticker"],
        "coin": s["ticker"].split("-")[0].replace("KX", "").replace("15M", ""),
        "pnl": round(s["pnl"], 2),
        "reason": s.get("exit_reason", "natural"),
        "timestamp": s["timestamp"],
    } for s in recent_closes]

    data = {
        "generated_at": now.isoformat(),
        "scan_generated_at": scan.get("generated_at"),
        # Real, periodically-resynced CASH bankroll from bot.py itself
        # (fetched from Kalshi directly, corrected for real fees every
        # 5 minutes), PLUS the current value of any open positions --
        # per explicit request, this now represents total equity
        # (cash + open positions), not cash alone. Falls back to the
        # old cash-only estimate only if scan.json doesn't have a real
        # balance yet (e.g. right after upgrading).
        "bankroll": round(scan.get("bankroll", round(STARTING_BANKROLL + total_pnl, 2)) + open_positions_value, 2),
        "cash_only_bankroll": scan.get("bankroll", round(STARTING_BANKROLL + total_pnl, 2)),
        "open_positions_value": round(open_positions_value, 2),
        "today_pnl": round(today_pnl, 2),
        "total_pnl": round(total_pnl, 2),
        "total_trades": len(trades),
        "settled_count": len(settlements),
        "recent_closes": recent_closes,
        "win_count": wins,
        "stop_loss_count": stop_loss_count,
        "win_rate": round(wins / len(settlements) * 100, 1) if settlements else 0,
        "equity_series": series,
        "coin_table": coin_table,
        "live_scan": sorted(
            scan.get("results", []),
            key=lambda r: (
                0 if status_by_ticker.get(r["ticker"]) == "green" else
                1 if r["ticker"] in state_data else
                2,
                # FIXED after a real crash: generic (non-crypto) results
                # have edge_pct=None, since there's no model to compute
                # an edge from. abs(None) crashes -- treat a missing
                # edge as 0 so these sort in without erroring out.
                -abs(r["edge_pct"]) if r.get("edge_pct") is not None else 0
            )
        ),
        "markets_scanned": scan.get("markets_scanned", 0),
        "markets_evaluated": scan.get("markets_evaluated", 0),
        "open_positions": len(state_data),
        "open_position_tickers": list(state_data.keys()),
        "open_positions_detail": open_positions_detail,
        "dry_run": scan.get("dry_run", True),
    }
    OUT_FILE.write_text(json.dumps(data, indent=2))
    print(f"Wrote {OUT_FILE} — {len(trades)} trades, {len(settlements)} real settled (of {len(all_settlements)} total on file).")


if __name__ == "__main__":
    build()
