"""
RIDE strategy -- 1 share, runs alongside flip-star and Pulse's other rules.

Idea: in the middle of a 15-minute window, when a coin is clearly moving one way, buy ONE share of the favored side
while it is still mid-priced (60-85c), ride it up while the price keeps climbing, and sell as soon as it turns around,
locking in what it gained.

  ENTRY (1 share) when ALL of these are true (numbers come from the Crypto Edge Board):
    - 4 to 10 minutes left in the window
    - favored side costs 60c-85c (room to climb)
    - model gives that side 75%+ to win and "To flip" is Yellow 2 or better (1.33+ sigma)
    - the coin is moving our way right now: its 1-minute AND 5-minute price changes both point in our direction
    - tight book (spread 3c or less); at most RIDE_MAX_OPEN ride positions at once

  EXIT
    - TAKE PROFIT: once the share has gained RIDE_ARM (10c) it is "armed"; it is sold when it falls RIDE_GIVEBACK (4c)
      from its best price. If it gets very safe (win 95%+ and To flip green) the giveback widens to 8c so it can run.
    - TURNED: model win % falls under 55% -> sell at once.
    - STOP: price 8c+ under what was paid for 8 seconds -> sell.
    - Last 20 seconds: no more selling; it settles.
  Kalshi charges ~2c per 1-share order each way, so a ride needs ~5c+ of gain to make money -- hence the 10c arm.
"""
import time
import uuid

import flip_star

RIDE_ENABLED = True
RIDE_SHARES = 1
RIDE_MIN_SECONDS_LEFT = 240
RIDE_MAX_SECONDS_LEFT = 600
RIDE_MIN_PRICE = 0.60
RIDE_MAX_PRICE = 0.85
RIDE_MIN_WIN = 0.75
RIDE_MIN_SIGMA = 4 / 3         # Yellow 2 or better -- 1.0-1.3 (Yellow 3) is too close to red: first live ride stopped out at 1.1
RIDE_MAX_SPREAD = 0.03
RIDE_MAX_OPEN = 2
RIDE_ARM = 0.10              # gain needed before the trailing take-profit is active
RIDE_GIVEBACK = 0.04         # sell when the price falls this far from its best (after armed)
RIDE_GIVEBACK_SAFE = 0.08    # wider giveback once it is very safe (win 95%+, To flip green)
RIDE_STOP = 0.08             # loss per share that triggers the stop ...
RIDE_STOP_CONFIRM_SECONDS = 8  # ... if it lasts this long
RIDE_TURNED_WIN = 0.55       # model win % under this -> it turned, sell now
RIDE_NO_SELL_LAST_SECONDS = 20
RIDE_MAX_ATTEMPTS_PER_TICKER = 2

_attempts = {}


def _moving_our_way(n: dict) -> bool:
    c1, c5 = n.get("chg_1m"), n.get("chg_5m")
    if c1 is None or c5 is None:
        return False
    sign = 1 if n["side"] == "yes" else -1
    return c1 * sign > 0 and c5 * sign > 0


def run_entries(client, state: dict, excluded_tickers: set, state_lock, dry_run: bool, log_trade, ensure_shard_funds):
    if not RIDE_ENABLED:
        return
    b = flip_star.board()
    if not b:
        return
    open_rides = sum(1 for p in state.values() if p.get("ride"))
    for ticker, n in b.items():
        if open_rides >= RIDE_MAX_OPEN:
            return
        if ticker in state or ticker in excluded_tickers:
            continue
        if _attempts.get(ticker, 0) >= RIDE_MAX_ATTEMPTS_PER_TICKER:
            continue
        if n["tl"] is None or not (RIDE_MIN_SECONDS_LEFT <= n["tl"] <= RIDE_MAX_SECONDS_LEFT):
            continue
        if n["p"] < RIDE_MIN_WIN or n["zs"] is None or n["zs"] < RIDE_MIN_SIGMA:
            continue
        if not _moving_our_way(n):
            continue
        side = "bid" if n["side"] == "yes" else "ask"
        try:
            mk = client.get_market(ticker).get("market", {})
            ya, yb = mk.get("yes_ask_dollars"), mk.get("yes_bid_dollars")
            if ya is None or yb is None:
                continue
            ya, yb = float(ya), float(yb)
            if ya - yb > RIDE_MAX_SPREAD + 1e-9:
                continue
            cost = ya if side == "bid" else 1 - yb
            if not (RIDE_MIN_PRICE <= cost <= RIDE_MAX_PRICE):
                continue
            held, _ = flip_star._held_count(client, ticker)
            if held != 0:
                continue
        except Exception as e:
            print(f"[RIDE] {ticker}: couldn't read the live quote ({e}) -- skipping this pass.")
            continue

        _attempts[ticker] = _attempts.get(ticker, 0) + 1
        limit = min(RIDE_MAX_PRICE + 0.01, round(cost + 0.01, 2))
        print(f"[RIDE] ENTRY {ticker} ({n['coin']}): {n['side'].upper()} @ ~${cost:.2f}, win {n['p']*100:.0f}%, "
              f"To flip {n['zs']:.1f} sigma, coin 1m {n['chg_1m']:+.3f}% / 5m {n['chg_5m']:+.3f}% our way, "
              f"{int(n['tl'])}s left -- {'DRY-RUN ' if dry_run else ''}buying {RIDE_SHARES} up to ${limit:.2f}")
        if dry_run:
            with state_lock:
                state[ticker] = {"count": RIDE_SHARES, "entry_price": cost, "side": side, "dry_run": True,
                                 "peak_gain_per_contract": 0.0, "entry_time": time.time(), "coin": n["coin"],
                                 "ride": True, "ride_peak": cost}
            open_rides += 1
            continue
        try:
            ensure_shard_funds(client, ticker, limit * RIDE_SHARES)
            yes_price = limit if side == "bid" else round(1 - limit, 2)
            client.create_order(ticker=ticker, client_order_id=f"pulse-ride-{uuid.uuid4()}", side=side,
                                count=str(RIDE_SHARES), price=f"{yes_price:.2f}", time_in_force="immediate_or_cancel")
        except Exception as e:
            print(f"[RIDE] {ticker}: entry order failed ({e}).")
            if "market_closed" in str(e) or "market_not_found" in str(e):
                excluded_tickers.add(ticker)
            continue
        filled, exposure = 0.0, 0.0
        for _ in range(4):
            try:
                filled, exposure = flip_star._held_count(client, ticker)
            except Exception:
                pass
            if filled != 0:
                break
            time.sleep(0.4)
        count = abs(filled)
        if count < 1e-9:
            print(f"[RIDE] {ticker}: nothing filled (price moved).")
            continue
        entry = exposure / count if exposure > 0 else cost
        with state_lock:
            state[ticker] = {"count": count, "entry_price": entry, "side": side, "dry_run": False,
                             "peak_gain_per_contract": 0.0, "entry_time": time.time(), "coin": n["coin"],
                             "ride": True, "ride_peak": entry}
        open_rides += 1
        print(f"[RIDE] FILLED {ticker}: {count:g} {n['side'].upper()} @ ${entry:.2f} -- riding; sells on a "
              f"{RIDE_GIVEBACK*100:.0f}c pullback once up {RIDE_ARM*100:.0f}c.")
        try:
            from types import SimpleNamespace
            log_trade(SimpleNamespace(ticker=ticker, title=n["title"], coin=n["coin"], seconds_remaining=n["tl"],
                                      model_prob=n["p"], edge_pct=None),
                      side, entry, count, False, entry_reason="ride")
        except Exception as e:
            print(f"[RIDE] trade log failed (non-fatal): {e}")


def exit_decision(ticker: str, position: dict, market: dict, seconds_remaining: float):
    """(should_exit, reason) for a ride position using the live Kalshi quote. Updates the position's peak."""
    yb, ya = market.get("yes_bid_dollars"), market.get("yes_ask_dollars")
    if yb is None or ya is None:
        return False, ""
    yb, ya = float(yb), float(ya)
    sell_value = yb if position["side"] == "bid" else 1 - ya      # what we'd actually get selling right now
    entry = position["entry_price"]
    peak = max(position.get("ride_peak", entry), sell_value)
    position["ride_peak"] = peak
    if seconds_remaining is not None and seconds_remaining < RIDE_NO_SELL_LAST_SECONDS:
        return False, ""
    n = flip_star.board().get(ticker)
    now = time.time()
    if n and n["p"] < RIDE_TURNED_WIN:
        return True, f"ride_turned (win % fell to {n['p']*100:.0f}%; entry ${entry:.2f}, now ${sell_value:.2f})"
    if peak - entry >= RIDE_ARM - 1e-9:
        safe = n is not None and n["p"] >= 0.95 and n["zs"] is not None and n["zs"] >= 2
        give = RIDE_GIVEBACK_SAFE if safe else RIDE_GIVEBACK
        if sell_value <= peak - give + 1e-9:
            return True, (f"ride_take_profit (peaked ${peak:.2f}, pulled back to ${sell_value:.2f}; "
                          f"entry ${entry:.2f})")
    if sell_value <= entry - RIDE_STOP + 1e-9:
        since = position.setdefault("ride_stop_since", now)
        if now - since >= RIDE_STOP_CONFIRM_SECONDS:
            return True, f"ride_stop (down {(entry - sell_value)*100:.0f}c for {now - since:.0f}s; entry ${entry:.2f})"
    else:
        position.pop("ride_stop_since", None)
    return False, ""
