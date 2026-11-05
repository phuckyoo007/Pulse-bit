"""
RISE strategy (5 Nov 12:30 CT, Matt) -- ride the coins the edge board shows as "RISING FASTEST".

  * Every pass, rank the 15-minute crypto coins by the board's MOMENTUM meter (same math as the board). The top 2 with
    positive, still-climbing momentum are the "rising fastest" pair.
  * Buy 2 shares of UP (YES) on each, one bet per coin per round, at most 2 open at once.
  * Exits (bot.py, positions flagged "rise"):
      - take profit the moment the momentum meter stops climbing (drops RISE_SLOWDOWN below its high since we bought)
        while selling now would net a profit (after the fee, at the price we can actually sell at);
      - stop: sell if our side falls to 90% of what we paid.
Rise bets ride on Surge's plumbing ("surge": True), so Flip Star's other stops leave them alone.
"""
import time
import uuid

import flip_star
import surge

RISE_ENABLED = False   # 5 Nov 13:10 CT (Matt): OFF (open Rise bets keep their exits)
RISE_SHARES = 2
RISE_TOP_N = 2               # the 2 coins rising fastest
RISE_MAX_OPEN = 2
RISE_STOP_FRACTION = 0.80    # 5 Nov 13:04 CT (Matt): sell if our side falls to 80% of what we paid (was 90%)
RISE_SLOWDOWN = 0.03         # momentum 3 points under its high since entry = "stopped climbing"
RISE_MIN_PROFIT = 0.01       # needs at least +1c net to count as "positive P/L"
RISE_RISE_SECONDS = 3        # entry: momentum must be higher than ~3s ago (still climbing)
RISE_MIN_PRICE = 0.10        # never buy a lottery ticket ...
RISE_MAX_PRICE = 0.90        # ... or a share that can't pay for itself
RISE_MIN_SECONDS_LEFT = 60

RISE_MAX_LOSS = 2.00         # 5 Nov 13:06 CT (Matt): Rise turns itself OFF once its bets have lost $2.00 in total
_pnl = [-0.30]               # Rise P/L so far -- starts at -$0.30 (its 4 bets earlier today, 5 Nov 12:45-12:56)

_done = {}      # ticker -> time we bet it (one bet per coin per round)
_tries = {}
_mhist = {}     # ticker -> [(time, momentum UP)]
_note = {}


def _log_once(key, msg):
    if _note.get(key) != msg:
        _note[key] = msg
        print(msg)


def top_rising(b: dict):
    """[(momentum, ticker, numbers)] for the coins rising fastest right now, best first."""
    now = time.time()
    rows = []
    for ticker, n in b.items():
        if "15M" not in ticker.split("-")[0].upper():
            continue
        up = surge.momentum_for(ticker, "yes")
        if up is None:
            continue
        h = _mhist.setdefault(ticker, [])
        h.append((now, up))
        while h and now - h[0][0] > 30:
            h.pop(0)
        if up > 0:
            rows.append((up, ticker, n))
    rows.sort(key=lambda r: -r[0])
    return rows[:RISE_TOP_N]


def _climbing(ticker, up):
    ref = None
    for t0, v0 in _mhist.get(ticker, []):
        if time.time() - t0 >= RISE_RISE_SECONDS:
            ref = v0
    return ref is not None and up > ref + 1e-6


def record(position, pnl) -> None:
    """Called for every closed bet; tallies Rise's own P/L and shuts Rise off at -$2.00 total."""
    global RISE_ENABLED
    try:
        if not isinstance(position, dict) or not position.get("rise"):
            return
        _pnl[0] += float(pnl)
        print(f"[RISE] bet closed {pnl:+.2f} -- Rise total {_pnl[0]:+.2f} (turns off at -${RISE_MAX_LOSS:.2f}).")
        if RISE_ENABLED and _pnl[0] <= -RISE_MAX_LOSS + 1e-9:
            RISE_ENABLED = False
            print(f"[RISE] OFF -- Rise bets have lost ${-_pnl[0]:.2f} in total (limit ${RISE_MAX_LOSS:.2f}). No new Rise bets.")
    except Exception:
        pass


def run(client, state: dict, excluded_tickers: set, state_lock, dry_run: bool, ensure_shard_funds) -> None:
    if not RISE_ENABLED or flip_star.in_pause():
        return
    b = flip_star.board()
    if not b:
        return
    now = time.time()
    for t in [t for t, at in _done.items() if now - at > 1800]:
        _done.pop(t, None)
        _tries.pop(t, None)
    for up, ticker, n in top_rising(b):
        _open = sum(1 for p in list(state.values()) if isinstance(p, dict) and p.get("rise"))
        if _open >= RISE_MAX_OPEN:
            return
        if ticker in _done or ticker in state or ticker in excluded_tickers or ticker in flip_star.pending_tickers() \
                or ticker in flip_star.external_owned[0]():
            continue
        if flip_star._is_excluded(n.get("coin"), ticker):
            continue
        tl = n.get("tl")
        if tl is None or tl < RISE_MIN_SECONDS_LEFT:
            continue
        if not _climbing(ticker, up):
            continue
        try:
            held, _ = flip_star._held_count(client, ticker)
            if held != 0:
                continue
            mk = client.get_market(ticker).get("market", {})
            ya, yb = mk.get("yes_ask_dollars"), mk.get("yes_bid_dollars")
            if ya is None or yb is None:
                continue
            ya, yb = float(ya), float(yb)
            if ya - yb > flip_star.FLIP_MAX_SPREAD + 1e-9:
                continue
            if ya < RISE_MIN_PRICE - 1e-9 or ya > RISE_MAX_PRICE + 1e-9:
                _log_once(ticker, f"[RISE] {ticker} ({n['coin']}): rising fastest (momentum {up*100:+.0f}) but UP costs "
                                  f"{ya*100:.0f}c, outside {RISE_MIN_PRICE*100:.0f}-{RISE_MAX_PRICE*100:.0f}c -- skipping.")
                continue
            limit = min(RISE_MAX_PRICE, round(ya + flip_star.FLIP_SLIPPAGE, 2))
        except Exception as e:
            print(f"[RISE] {ticker}: couldn't read the live quote ({e}) -- skipping this pass.")
            continue
        _done[ticker] = now
        print(f"[RISE] ENTRY {ticker} ({n['coin']}): rising fastest, momentum {up*100:+.0f} and climbing, {int(tl)}s left -- "
              f"{'DRY-RUN ' if dry_run else ''}buying {RISE_SHARES} UP up to ${limit:.2f}")
        if dry_run:
            continue
        try:
            ensure_shard_funds(client, ticker, limit * RISE_SHARES)
            client.create_order(ticker=ticker, client_order_id=f"pulse-surge-rise-{uuid.uuid4()}", side="bid",
                                count=str(RISE_SHARES), price=f"{limit:.2f}", time_in_force="immediate_or_cancel")
        except Exception as e:
            print(f"[RISE] {ticker}: entry order failed ({e}).")
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
            print(f"[RISE] {ticker}: nothing filled at ${limit:.2f} (price moved).")
            _tries[ticker] = _tries.get(ticker, 0) + 1
            if _tries[ticker] < 3:
                _done.pop(ticker, None)
            continue
        entry_price = exposure / count if exposure > 0 else ya
        with state_lock:
            state[ticker] = {"count": count, "entry_price": entry_price, "side": "bid", "dry_run": False,
                             "peak_gain_per_contract": 0.0, "entry_time": time.time(), "coin": n["coin"],
                             "flip_star": True, "surge": True, "rise": True, "rise_peak": up}
        print(f"[RISE] FILLED {ticker}: {count:g} UP @ ${entry_price:.2f} -- sells when momentum stops climbing in profit, "
              f"or at {RISE_STOP_FRACTION*100:.0f}% of cost.")
