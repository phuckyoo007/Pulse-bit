"""
Answers the actual question "did this exit rule help or hurt" with a
real measurement instead of a belief. When a position exits early, this
keeps a lightweight SHADOW record of that same market -- no real
position, no money at risk, just watching what it eventually settles to
-- so we can compare what you actually got against what holding to the
end would have paid.

This is pure observation. It never places an order, never affects
sizing, never touches state.json. It exists solely to make exits.py's
rules accountable to real outcomes instead of intuition.
"""
import json
import time
from pathlib import Path

SHADOW_FILE = Path("shadow_positions.json")
COMPARISON_FILE = Path("exit_comparisons.json")


def _load(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def _save(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(path)


def record_early_exit(ticker: str, side: str, entry_price: float, count: float,
                       exit_value: float, exit_reason: str, category: str = "?"):
    """Call this at the moment a position exits early -- starts shadowing
    that same market to see what it would have paid if held instead."""
    shadow = _load(SHADOW_FILE, {})
    shadow[ticker] = {
        "side": side, "entry_price": entry_price, "count": count,
        "exit_value": exit_value, "exit_reason": exit_reason, "category": category,
        "exited_at": time.time(),
    }
    _save(SHADOW_FILE, shadow)


def check_shadow_positions(client) -> list:
    """Call every loop. Checks whether any shadowed market has now
    finalized -- if so, computes what holding to settlement would have
    paid, compares it to what the early exit actually got, and logs the
    comparison. Returns the comparisons made this call (for printing)."""
    shadow = _load(SHADOW_FILE, {})
    if not shadow:
        return []

    comparisons = _load(COMPARISON_FILE, [])
    made_this_call = []

    for ticker in list(shadow.keys()):
        record = shadow[ticker]
        try:
            market = client.get_market(ticker).get("market", {})
        except Exception:
            continue
        if market.get("status") != "finalized" or not market.get("result"):
            continue   # still waiting -- not resolved yet

        result = market["result"]
        won_if_held = (record["side"] == "bid" and result == "yes") or \
                      (record["side"] == "ask" and result == "no")
        pnl_if_held = ((1.0 if won_if_held else 0.0) - record["entry_price"]) * record["count"]
        pnl_actual = (record["exit_value"] - record["entry_price"]) * record["count"]
        difference = round(pnl_actual - pnl_if_held, 2)   # positive = exiting early was BETTER than holding

        comparison = {
            "ticker": ticker, "category": record.get("category", "?"), "exit_reason": record["exit_reason"],
            "side": record["side"], "entry_price": record["entry_price"], "count": record["count"],
            "exit_value": record["exit_value"], "natural_result": result,
            "pnl_actual": round(pnl_actual, 2), "pnl_if_held": round(pnl_if_held, 2),
            "difference": difference, "timestamp": time.time(),
        }
        comparisons.append(comparison)
        made_this_call.append(comparison)
        del shadow[ticker]

    if made_this_call:
        _save(SHADOW_FILE, shadow)
        _save(COMPARISON_FILE, comparisons)
    return made_this_call
