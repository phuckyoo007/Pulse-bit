"""
MAKER93 -- REAL MONEY (16 Nov 17:58 CT, Matt: "turn maker93 on").  Live version of the paper MAKER93S, which went
35 for 36 (+$0.76 at $1 a ride) after the DOGE/ZEC filter.
  * Last MAKER93_MAX_SECONDS_LEFT (5 min) down to MAKER93_MIN_SECONDS_LEFT, BTC / SOL / XRP / BNB / HYPE (no DOGE, ZEC).
  * The favourite (the side priced over 50c) is asked at 95-97c AND the edge board picks that same side 1.5+ sigma from
    flipping  ->  post a RESTING bid 2c under the ask (93-95c).  It only fills if the price dips to it.
  * Not filled within MAKER93_REST_SECS, or the board stops agreeing / sigma falls under 1.5  ->  cancel the bid.
  * Filled  ->  held to the end (bot.py), with the 76%-of-cost stop as the backup (Matt's rule for every strategy).
Research basis: Whelan (UCD, Kalshi "Makers and Takers") -- resting-bid makers earn ~+1.9% after fees on 50c+ contracts.
"""
import time
import uuid

import flip_star
import strategy_guard

MAKER93_ENABLED = True
MAKER93_SHARES = 1          # small to start -- the paper version was $1 a ride
MAKER93_MAX_SECONDS_LEFT = 300
MAKER93_MIN_SECONDS_LEFT = 30
MAKER93_ASK_MIN, MAKER93_ASK_MAX = 0.95, 0.97
MAKER93_UNDER = 0.02
MAKER93_MIN_SIGMA = 1.5
MAKER93_REST_SECS = 60
MAKER93_SKIP_COINS = {"DOGE", "ZEC"}

_resting = {}    # ticker -> {"order_id", "side", "price", "at", "n"}
_done = {}


def owned() -> set:
    return set(_resting)


def record(position, pnl) -> None:
    return


def _cancel(client, ticker, r, why):
    if r.get("order_id"):
        try:
            client.cancel_order(r["order_id"])
        except Exception as e:
            if "not_found" not in str(e) and "404" not in str(e):
                print(f"[MAKER93] {ticker}: cancel failed ({e}) -- will retry.")
                return False
    print(f"[MAKER93] {ticker}: resting {r['price']*100:.0f}c bid cancelled -- {why}.")
    return True


def _check(client, state, state_lock, b):
    now = time.time()
    for ticker, r in list(_resting.items()):
        try:
            held, exposure = flip_star._held_count(client, ticker)
        except Exception as e:
            print(f"[MAKER93] {ticker}: couldn't check the resting bid ({e}).")
            continue
        n = b.get(ticker)
        fav_ok = bool(n) and n.get("side") == r["side"] and (n.get("zs") or 0) >= MAKER93_MIN_SIGMA - 1e-9 \
            and (n.get("tl") or 0) >= MAKER93_MIN_SECONDS_LEFT
        if abs(held) < 1e-9 and fav_ok and now - r["at"] < MAKER93_REST_SECS:
            continue   # still waiting for the price to dip to us
        if abs(held) < 1e-9 and not _cancel(client, ticker, r,
                                              "not filled in time" if fav_ok else "board stopped agreeing / time short"):
            continue
        if abs(held) >= 1e-9:
            _cancel(client, ticker, r, "filled -- cancelling any remainder")
            try:
                held, exposure = flip_star._held_count(client, ticker)
            except Exception:
                pass
        _resting.pop(ticker, None)
        cnt = abs(held)
        if cnt >= 1e-9:
            px = exposure / cnt if exposure > 0 else r["price"]
            with state_lock:
                state[ticker] = {"count": cnt, "entry_price": px, "side": "bid" if r["side"] == "yes" else "ask",
                                 "dry_run": False, "peak_gain_per_contract": 0.0, "entry_time": now,
                                 "coin": (r["n"] or {}).get("coin"), "maker93": True, "flip_star": False,
                                 "force_exit": False, "entry_zs": (r["n"] or {}).get("zs")}
            strategy_guard.note_entry(ticker, own_slot=True)
            print(f"[MAKER93] FILLED {ticker}: {cnt:g} {r['side'].upper()} @ ${px:.2f} (resting bid hit) -- held to the end, "
                  f"76% stop backup.")


def run(client, state: dict, excluded_tickers: set, state_lock, dry_run: bool, ensure_shard_funds) -> None:
    b = flip_star.board() or {}
    if _resting:
        _check(client, state, state_lock, b)
    if not MAKER93_ENABLED or flip_star.in_pause() or dry_run:
        return
    now = time.time()
    for t in [t for t, at in _done.items() if now - at > 1800]:
        _done.pop(t, None)
    for ticker, n in b.items():
        if "15M" not in ticker.split("-")[0].upper() or n.get("coin") in MAKER93_SKIP_COINS:
            continue
        tl = n.get("tl")
        if tl is None or not (MAKER93_MIN_SECONDS_LEFT <= tl <= MAKER93_MAX_SECONDS_LEFT):
            continue
        if ticker in _done or ticker in _resting or ticker in state or ticker in excluded_tickers \
                or ticker in flip_star.pending_tickers() or ticker in flip_star.external_owned[0]():
            continue
        side = n.get("side")
        ya, na = n.get("yes_ask"), n.get("no_ask")
        if side not in ("yes", "no") or ya is None or na is None or (n.get("zs") or 0) < MAKER93_MIN_SIGMA - 1e-9:
            continue
        ask = float(ya if side == "yes" else na)
        if not (MAKER93_ASK_MIN - 1e-9 <= ask <= MAKER93_ASK_MAX + 1e-9) or ask < 0.5:
            continue
        if not strategy_guard.can_enter(ticker, state, "MAKER93-" + str(n.get("coin")), own_slot=True):
            continue
        try:
            held, _ = flip_star._held_count(client, ticker)
        except Exception:
            continue
        if abs(held) > 1e-9:
            continue
        price = round(ask - MAKER93_UNDER, 2)
        yes_price = price if side == "yes" else round(1 - price, 2)
        coid = f"pulse-surge-maker93-{uuid.uuid4()}"
        try:
            ensure_shard_funds(client, ticker, price * MAKER93_SHARES)
        except Exception:
            pass
        try:
            resp = client.create_order(ticker=ticker, client_order_id=coid, side="bid" if side == "yes" else "ask",
                                       count=str(MAKER93_SHARES), price=f"{yes_price:.2f}",
                                       time_in_force="good_till_canceled")
        except Exception as e:
            print(f"[MAKER93] {ticker}: resting bid failed ({e}).")
            _done[ticker] = now
            continue
        _done[ticker] = now
        _resting[ticker] = {"order_id": flip_star._find_order_id(client, ticker, coid, resp), "side": side,
                            "price": price, "at": now, "n": n}
        print(f"[MAKER93] {ticker} ({n.get('coin')}): board {side.upper()} {n.get('zs'):.2f} sigma, ask {ask*100:.0f}c -- "
              f"resting {MAKER93_SHARES} share bid at {price*100:.0f}c for up to {MAKER93_REST_SECS}s, {tl:.0f}s left.")
