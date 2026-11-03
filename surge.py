"""
SURGE strategy (3 Nov 10:40 CT, Matt) -- momentum rides, using the edge board's MOMENTUM meter (-100..+100).

  * Any time in a 15-minute round, any win %: when a coin's momentum meter reads 100 (full strength), buy that
    direction -- +100 (rising) buys UP, -100 (falling) buys DOWN. 5 shares, one bet per coin per round.
  * Sell the second the meter drops below 90 in our direction (bot.py exit loop, "surge" positions).
  * Never pays more than 97c (a 98-99c share can't pay for itself).
Momentum = flip_star's mirror of the board meter (same formula, smoothed the same way).
"""
import math
import time
import uuid

import requests

import flip_star

SURGE_ENABLED = True
SURGE_MARKETS_ON = True       # 3 Nov 13:17 CT (Matt): also commodities + interest rates (Markets / Rates boards)
SURGE_BET_DOLLARS = 8.00     # 3 Nov 13:43 CT (Matt): $8 per bet so it reaches the $0.50 take-profit sooner (shares = $8 / price)
SURGE_TAKE_PROFIT = 0.50     # 3 Nov 11:55 CT (Matt): cash out once the bet is up $0.50 (was $1.00)
SURGE_STOP_FRACTION = 0.75   # 3 Nov 11:53 CT (Matt): sell if the bet falls to 75% of what was paid
SURGE_MAX_PER_ROUND = 10     # 3 Nov 12:47 CT (Matt): up to 10 Surge bets per 15-minute round (was 5)
SURGE_ENTRY = 0.795          # 3 Nov 13:51 CT (Matt): buy at 80-100 on the meter while it is still climbing
SURGE_RISE_SECONDS = 3       # "climbing" = higher than ~3 seconds ago
SURGE_EXIT = 0.245           # 3 Nov 11:19 CT (Matt): sell when the meter drops under 25 our way (was 90)
SURGE_MIN_PRICE = 0.62       # 3 Nov 13:27 CT (Matt): only buy when our side's price is $.62-$.90
SURGE_MAX_PRICE = 0.90
SURGE_MIN_SECONDS_LEFT = 120   # 3 Nov 14:15 CT (Matt): no new Surge buys in the last 2 minutes (was 20s)
SURGE_PRICE_METER_MIN = 0.0   # 3 Nov 13:50 CT (Matt): price meter just has to be going UP -- any rise, no minimum

_done = {}      # ticker -> time we bet it (one bet per coin per round)
_tries = {}     # ticker -> entry attempts (max 3)
_mhist = {}     # ticker -> [(time, momentum)] for the rising check
_phist = {}     # ticker -> [(time, yes_ask, no_ask)] for the price-meter check
_note = {}


def _log_once(key, msg):
    if _note.get(key) != msg:
        _note[key] = msg
        print(msg)


# 3 Nov 13:17 CT (Matt): Surge also rides commodities and interest rates, read from the Markets and Rates edge boards
# (same /api/state format and the same momentum math as the crypto board).
SURGE_EXTRA_BOARDS = [  # 3 Nov 14:09 CT (Matt): commodities off -- interest rates only
                      "https://rates-board-production.up.railway.app/api/state"]
_xb = {"data": {}, "mom": {}, "coin_of": {}, "fetched": 0.0, "ok_at": 0.0}


def _extra_board():
    """ticker -> board numbers for the commodity / rate 15-minute markets (cached ~1s)."""
    now = time.time()
    if now - _xb["fetched"] < 1.0:
        return _xb["data"] if now - _xb["ok_at"] <= flip_star.FLIP_STALE_SECONDS else {}
    _xb["fetched"] = now
    out, ok = {}, False
    for url in SURGE_EXTRA_BOARDS:
        try:
            r = requests.get(url, timeout=4, headers={"Cache-Control": "no-store"})
            r.raise_for_status()
            st = r.json()
            age = max(0.0, now - float(st.get("now", now)))
            if age > flip_star.FLIP_STALE_SECONDS:
                continue
            ok = True
            coins = st.get("coins") or {}
            for k, c in coins.items():
                try:
                    v = c.get("ann_vol_30m") or c.get("ann_vol_24h")
                    c1, c5 = c.get("chg_1m"), c.get("chg_5m")
                    if not v or c1 is None or c5 is None:
                        continue
                    sm = float(v) / math.sqrt(525600)
                    z1, z5 = float(c1) / sm, float(c5) / (sm * math.sqrt(5))
                    m = max(-1.0, min(1.0, (0.4 * z1 + 0.6 * z5) / 2))
                    pm = _xb["mom"].get(k)
                    _xb["mom"][k] = m if pm is None else pm * 0.85 + m * 0.15
                except Exception:
                    continue
            for m in st.get("markets", []):
                if m.get("kind") != "15m":
                    continue
                n = flip_star._side_numbers(m, age, coins)
                if n and n["ticker"]:
                    out[n["ticker"]] = n
                    _xb["coin_of"][n["ticker"]] = n["coin"]
        except Exception as e:
            _log_once("xb:" + url, f"[SURGE] couldn't read {url.split('//')[1].split('.')[0]} ({e}).")
    if ok:
        _xb["data"], _xb["ok_at"] = out, now
    return _xb["data"] if now - _xb["ok_at"] <= flip_star.FLIP_STALE_SECONDS else {}


def momentum_for(ticker: str, side: str):
    """Meter reading for OUR side (+1 = full strength our way), or None without fresh data."""
    coin = _xb["coin_of"].get(ticker)
    if coin is not None:
        if time.time() - _xb["ok_at"] > flip_star.FLIP_STALE_SECONDS:
            return None
        m = _xb["mom"].get(coin)
        return None if m is None else (m if side == "yes" else -m)
    ag = flip_star.momentum_against(ticker, side)
    return None if ag is None else -ag


def run(client, state: dict, excluded_tickers: set, state_lock, dry_run: bool, ensure_shard_funds) -> None:
    if not SURGE_ENABLED:
        return
    b = dict(flip_star.board())
    if SURGE_MARKETS_ON:
        b.update(_extra_board())
    if not b:
        return
    now = time.time()
    for t in [t for t, at in _done.items() if now - at > 1800]:
        _done.pop(t, None)
    for ticker, n in b.items():
        if "15M" not in ticker.split("-")[0].upper():
            continue
        if ticker in _done or ticker in state or ticker in excluded_tickers or ticker in flip_star.pending_tickers() \
                or ticker in flip_star.external_owned[0]():
            continue
        tl = n.get("tl")
        if tl is None or tl < SURGE_MIN_SECONDS_LEFT:
            continue
        _rk = ticker.split("-")[1] if "-" in ticker else ""
        if sum(1 for t in _done if t.split("-")[1:2] == [_rk]) >= SURGE_MAX_PER_ROUND:
            continue
        up = momentum_for(ticker, "yes")
        if up is None:
            continue
        hist = _mhist.setdefault(ticker, [])
        hist.append((now, up))
        while hist and now - hist[0][0] > 30:
            hist.pop(0)
        ref = None
        for t0, v0 in hist:
            if now - t0 >= SURGE_RISE_SECONDS:
                ref = v0
        # price meter: our side's buy price now vs ~5s ago (same as the board's PRICE meter)
        ph = _phist.setdefault(ticker, [])
        ph.append((now, n.get("yes_ask"), n.get("no_ask")))
        while ph and now - ph[0][0] > 30:
            ph.pop(0)
        if abs(up) < SURGE_ENTRY:
            continue
        # 3 Nov 11:21 CT (Matt): only while it is ESCALATING -- the meter must be higher (our way) than a few seconds ago.
        if ref is None or abs(up) <= abs(ref) + 1e-6 or (ref * up) < 0:
            continue
        side_name = "yes" if up > 0 else "no"
        side = "bid" if side_name == "yes" else "ask"
        # 3 Nov 13:31 CT (Matt): the PRICE meter must also be rising at 75%+ of its scale (board: full scale = 5c in ~5s,
        # so 75% = our side's price up 3.75c+ over the last ~5 seconds).
        _ix = 1 if side_name == "yes" else 2
        _pref = None
        for _row in ph:
            if now - _row[0] >= 5 and _row[_ix] is not None:
                _pref = _row[_ix]
        _pnow = ph[-1][_ix] if ph else None
        if _pref is None or _pnow is None or (float(_pnow) - float(_pref)) <= SURGE_PRICE_METER_MIN * 0.05 + 1e-9:
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
            cost = ya if side == "bid" else 1 - yb
            if cost < SURGE_MIN_PRICE - 1e-9 or cost > SURGE_MAX_PRICE + 1e-9:
                _log_once(ticker, f"[SURGE] {ticker} ({n['coin']}): momentum 100 {'UP' if up > 0 else 'DOWN'} but price "
                                  f"{cost*100:.0f}c is outside {SURGE_MIN_PRICE*100:.0f}-{SURGE_MAX_PRICE*100:.0f}c -- skipping.")
                continue
            limit = min(SURGE_MAX_PRICE, round(cost + flip_star.FLIP_SLIPPAGE, 2))
        except Exception as e:
            print(f"[SURGE] {ticker}: couldn't read the live quote ({e}) -- skipping this pass.")
            continue
        _done[ticker] = now
        _bet = SURGE_BET_DOLLARS
        try:
            _cash = float(client.get_balance_cached().get("balance", 0)) / 100
            if _cash < _bet:
                _bet = _cash * 0.97   # not enough cash for the full bet -- use what's there
        except Exception:
            pass
        _sh = int(_bet // limit)
        if _sh < 1:
            _log_once("cash", f"[SURGE] not enough cash for even 1 share (${_bet:.2f}) -- skipping until the balance is back up.")
            _done.pop(ticker, None)
            continue
        print(f"[SURGE] ENTRY {ticker} ({n['coin']}): momentum meter at {abs(up)*100:.0f} and climbing {'UP' if up > 0 else 'DOWN'}, "
              f"{int(tl)}s left -- {'DRY-RUN ' if dry_run else ''}buying {_sh} {side_name.upper()} (${SURGE_BET_DOLLARS:.0f}) up to ${limit:.2f}")
        if dry_run:
            continue
        try:
            ensure_shard_funds(client, ticker, limit * _sh)
            yes_price = limit if side == "bid" else round(1 - limit, 2)
            client.create_order(ticker=ticker, client_order_id=f"pulse-surge-{uuid.uuid4()}", side=side,
                                count=str(_sh), price=f"{yes_price:.2f}", time_in_force="immediate_or_cancel")
        except Exception as e:
            print(f"[SURGE] {ticker}: entry order failed ({e}).")
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
            print(f"[SURGE] {ticker}: nothing filled at ${limit:.2f} (price moved).")
            _tries[ticker] = _tries.get(ticker, 0) + 1
            if _tries[ticker] < 3:
                _done.pop(ticker, None)   # allow another try (max 3) while the meter is still at 100
            continue
        entry_price = exposure / count if exposure > 0 else cost
        with state_lock:
            state[ticker] = {"count": count, "entry_price": entry_price, "side": side, "dry_run": False,
                             "peak_gain_per_contract": 0.0, "entry_time": time.time(), "coin": n["coin"],
                             "flip_star": True, "surge": True}
        print(f"[SURGE] FILLED {ticker}: {count:g} {side_name.upper()} @ ${entry_price:.2f} -- sells when momentum drops under {SURGE_EXIT*100:.0f}.")
