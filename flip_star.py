"""
FLIP-STAR strategy -- runs ALONGSIDE Pulse's other entry rules.

Uses the exact same numbers the Crypto Edge Board dashboard shows (it reads the board's /api/state), so what the
bot does matches what you see on the top coin boxes:

  ENTRY (10 shares of the favored side) when, with 8 minutes or less left in the 15-minute window:
      the coin has the RED STAR, or its "To flip" line is GREEN (2+ sigma) or YELLOW 1 / bid zone (1.5-2 sigma).
  HOLD through Yellow 1/2/3 (0.5-2 sigma); Yellow 3 (0.5-1) is the dashboard's "get ready to sell" warning.
  EXIT as soon as "To flip" turns RED (under 0.5 sigma).  [zones moved down 18 Oct: red was under 1]

If the board can't be reached (or its data is stale) the bot opens nothing new, and any flip-star position it holds
falls back to Pulse's normal stop-loss until fresh numbers come back. Pulse's hard backstop (72%) still applies.
"""
import os
import time
import uuid

import requests

FLIP_ENABLED = True
# 24 Oct 12:49 CT (per Matthew): never bid on these coins. Dropped from board() below, so neither flip-star nor the
# RIDE paper trial (which reads flip_star.board()) can open anything on them. NEAR: -$16.20 crash_stop 24 Oct 17:43 UTC.
FLIP_EXCLUDED_COINS = {"NEAR"}
FLIP_SHARES = 15                   # 25 Oct 15:56 CT (per Matthew): was 30. Shares per flip-star entry (ceiling; cash cap can size down)
# 20 Oct 19:27 CT (NEAR 19:14: one 15-share bet = most of the account, wiped out by a 4-second spike to 25c).
# A spike that fast can't be stopped by any stop, so cap how much one bet (and all open bets) can lose:
FLIP_MAX_BET_FRACTION = 0.98      # 21 Oct 21:19 CT: two full 10-share bets (~$20 total) per Matthew; 2% left for fees
FLIP_MAX_OPEN = 4                 # 25 Oct 13:33 CT (per Matthew): was 9 -- at most 4 Pulse bets open at once
FLIP_MAX_SECONDS_LEFT = 240        # 22 Oct 20:43 CT (per Matthew): only the last 4 minutes of the window (was 6, before that 8)
FLIP_MIN_SECONDS_LEFT = 2         # too close to the bell to get a clean fill
FLIP_NONSTAR_MIN_SECONDS_LEFT = 3   # green / Yellow 1: allowed right up to the last 30s again (was blocked under 2.5 min)
FLIP_ENTRY_SIGMA = 1.8            # 25 Oct 13:34 CT (per Matthew): non-star entries at 1.8+ sigma
FLIP_EXIT_SIGMA = 0.35              # 25 Oct 13:35 CT (per Matthew): sell instantly when To flip for our side falls to 0.35 sigma
FLIP_EXIT_CONFIRM_SECONDS = 3      # sell immediately on red
FLIP_EXIT_MAX_WIN = 1.01           # 20 Oct 15:32 CT: red alone sells (no win-% check)
FLIP_EXIT_PANIC_WIN = 0.60         # (was 0.75) if win % falls under 60% the coin is really turning -- sell at once, no waiting
FLIP_STOP_CONFIRM_SECONDS = 4      # (18 Oct 19:21: was 10) the 80% / 20c price stop must also hold this long (and win % be under 90%) -- thin books blip
FLIP_USE_80_STOP = False   # 20 Oct 00:18 CT: 80c stop OFF (sold SOL/ETH dips that came back). Stops now: To-flip red <0.2 sigma, 70/30 hard stop, 60/40 last 45s
FLIP_MAX_SPREAD = 0.05             # skip thin, jumpy books
FLIP_MAX_PRICE = 0.98              # 25 Oct 13:33 CT (per Matthew): 94-98c (99c bets: ~1c win, ~3/4 of losses)
FLIP_NONSTAR_MAX_PRICE = 0.98      # 25 Oct 13:33 CT: 94-98c
FLIP_NONSTAR_MIN_PRICE = 0.94      # 25 Oct 13:33 CT (per Matthew): 94-98c
FLIP_NONSTAR_MIN_WIN = 0.97        # 25 Oct 20:48 CT (per Matthew): 97%+ win
FLIP_MIN_WIN_ALL = 0.97            # 25 Oct 20:48 CT (per Matthew): 97%+ win; breaker steps up to 98% after a loss
FLIP_STAR_MIN_PRICE = 0.94         # 25 Oct 13:33 CT (per Matthew): 94-98c; breaker can only raise it
FLIP_STAR_REST_PRICE = 0.96        # 18 Oct: red star priced above this -> post a resting bid HERE and wait, instead of paying 97-99c
FLIP_RESTING_BIDS = False          # 18 Oct 19:15 CT: OFF. Resting 96c bids only filled when the price was falling into them
                                   # (HYPE -0.08, BTC -10.90, ETH -3.30 within seconds of filling). Buy at the live price only.
# FALLING-KNIFE GUARD (18 Oct): never buy while the coin or our side's price is moving against us.
FLIP_MAX_SIDE_DROP_1M = 0.02       # skip if our side's Kalshi price fell 2c+ in the last minute
FLIP_MAX_SPOT_AGAINST_1M = 0.05    # 19 Oct 18:07: loosened from 0.02% -- tiny wiggles were blocking most entries; still blocks real moves (0.1%+)
FLIP_KNIFE_GUARD = False           # 20 Oct 12:00 CT: falling-knife guard OFF (temporary, per Matthew) -- set True to turn it back on
FLIP_RED_STAR_ONLY = False        # 25 Oct 13:34 CT (per Matthew): also bid without the red star once To flip >= FLIP_ENTRY_SIGMA (1.8)

# FLOOR CIRCUIT BREAKER (18 Oct): the red-star floor is on trial at 92c. If any flip-star bet bought under 93c
# ends in a loss (early exit, emergency backstop or settlement), the floor goes straight back to 93c and stays
# there. The trip is saved to a small file so a restart keeps it (a fresh deploy starts at 92c again).


def _order_fee(count, price):
    """Kalshi taker fee for one order, in dollars: ceil(0.07 * C * p * (1-p)) cents."""
    import math
    return math.ceil(0.07 * count * price * (1 - price) * 100 - 1e-9) / 100


FLIP_STAR_SAFE_MIN_PRICE = 0.93
_BREAKER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flip_floor_breaker.json")
try:
    if os.path.exists(_BREAKER_FILE):
        FLIP_STAR_MIN_PRICE = max(FLIP_STAR_MIN_PRICE, FLIP_STAR_SAFE_MIN_PRICE)
        print(f"[FLIP] floor circuit breaker already tripped earlier -- red-star floor stays at {FLIP_STAR_MIN_PRICE*100:.0f}c.")
except Exception:
    pass


# WIN-% BREAKER (20 Oct 21:11 CT, Matthew): entries allowed at 97%+, but the first losing bet that was bought under
# 98% puts the minimum back to 98% (saved to a file so a restart keeps it; a fresh deploy starts at 97% again).
FLIP_WIN_SAFE_MIN = 0.98   # 25 Oct 20:48 CT (per Matthew): after a loss on a sub-98% bet, minimum steps up to 98%
_WIN_BREAKER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flip_win_breaker.json")
try:
    if os.path.exists(_WIN_BREAKER_FILE):
        FLIP_MIN_WIN_ALL = max(FLIP_MIN_WIN_ALL, FLIP_WIN_SAFE_MIN)
        FLIP_NONSTAR_MIN_WIN = max(FLIP_NONSTAR_MIN_WIN, FLIP_WIN_SAFE_MIN)
        print(f"[FLIP] win-% breaker already tripped earlier -- minimum win stays at {FLIP_MIN_WIN_ALL*100:.0f}%.")
except Exception:
    pass


# LOSS KILL SWITCH (18 Oct): after 3 losing flip-star bets, stop opening new bets until Pulse is restarted
# (restart the pulse service in Railway). Open bets keep their stops; only new buying stops.
FLIP_MAX_LOSSES_BEFORE_HALT = 10
_loss_count = 0
# 20 Oct 11:29 CT (Matthew): every stop OFF for Pulse bets except two --
#   60/40: sell when the side we hold is at/under 60c (other side at/over 40c), any time in the round
#   50% crash: sell when the position is worth half (or less) of what was paid
# The red <0.2 sigma exit, 70/30 hard stop, 72% backstop, win-% collapse and 80c stop are all off while this is True.
FLIP_STOPS_ONLY_6040_AND_CRASH = True   # 20 Oct 15:45 CT (5-day data): price stops only -- 70/30 (60/40 last 45s) + 50% crash; red/win-collapse exits OFF
# 24 Oct 12:49 CT (per Matthew): stops tightened after the last losses -- NEAR crash_stop 18c (entry 99c, -$16.20),
# DOGE crash_stop 41c (entry 94c, -$10.79), HYPE stop_6040 68c after 6.6s (-$7.13). All three sold far below the line.
FLIP_6040_HELD = 0.80             # 25 Oct 14:07 CT (per Matthew): instant sell at/under 80c (was 75c); last-45s line stays 0.70 in bot.py
FLIP_CRASH_FRACTION = 0.87        # (was 0.50) instant sell when the position is worth 70% or less of what was paid
# 24 Oct 13:53 CT (per Matthew): manually entered bets (bot.py "manually_adopted") also get an instant stop at 85% of what
# was paid, all round long incl. the last 45s. The 75c line and 70% crash stops above still apply to them too.
MANUAL_STOP_FRACTION = 0.85
FLIP_PAUSED = False                # set to True to pause new bets (open bets keep their stops)
FLIP_HALTED = FLIP_PAUSED
_halt_note_at = 0.0


def record_result(position, pnl, how=""):
    """Called by bot.py whenever a position closes. Counts losses for the kill switch and trips the floor breaker
    on a losing flip-star bet bought under 93c."""
    global FLIP_STAR_MIN_PRICE, _loss_count, FLIP_HALTED, FLIP_MIN_WIN_ALL, FLIP_NONSTAR_MIN_WIN
    try:
        if not position or not position.get("flip_star"):
            return
        entry = float(position.get("entry_price") or 0)
        if pnl < -1e-9:
            _loss_count += 1
            print(f"[FLIP] loss {_loss_count}/{FLIP_MAX_LOSSES_BEFORE_HALT} since restart: ${-pnl:.2f} ({how}).")
            if _loss_count >= FLIP_MAX_LOSSES_BEFORE_HALT and not FLIP_HALTED:
                FLIP_HALTED = True
                print(f"[FLIP] KILL SWITCH: {_loss_count} losing bets since the last restart -- NO NEW BETS until Pulse "
                      f"is restarted in Railway. Open bets are still protected by their stops.")
        _ew = position.get("entry_win")
        if (pnl < -1e-9 and _ew is not None and float(_ew) < FLIP_WIN_SAFE_MIN - 0.005
                and FLIP_MIN_WIN_ALL < FLIP_WIN_SAFE_MIN):
            FLIP_MIN_WIN_ALL = FLIP_WIN_SAFE_MIN
            FLIP_NONSTAR_MIN_WIN = max(FLIP_NONSTAR_MIN_WIN, FLIP_WIN_SAFE_MIN)
            print(f"[FLIP] WIN-% BREAKER TRIPPED: a bet bought at {float(_ew)*100:.0f}% win lost ${-pnl:.2f} ({how}) -- "
                  f"minimum win back to {FLIP_WIN_SAFE_MIN*100:.0f}%.")
            try:
                open(_WIN_BREAKER_FILE, "w").write('{"tripped": true}')
            except Exception:
                pass
        if pnl < -1e-9 and entry < FLIP_STAR_SAFE_MIN_PRICE - 0.005 and FLIP_STAR_MIN_PRICE < FLIP_STAR_SAFE_MIN_PRICE:
            FLIP_STAR_MIN_PRICE = FLIP_STAR_SAFE_MIN_PRICE
            print(f"[FLIP] FLOOR CIRCUIT BREAKER TRIPPED: a bet bought at ${entry:.2f} lost ${-pnl:.2f} ({how}) -- "
                  f"red-star floor back to {FLIP_STAR_SAFE_MIN_PRICE*100:.0f}c.")
            try:
                import json as _json
                with open(_BREAKER_FILE, "w") as f:
                    _json.dump({"tripped_at": time.time(), "entry": entry, "pnl": pnl, "how": how}, f)
            except Exception as e:
                print(f"[FLIP] couldn't save the breaker state ({e}) -- floor is 93c until the next restart.")
    except Exception as e:
        print(f"[FLIP] record_result error (non-fatal): {e}")
FLIP_SLIPPAGE = 0.01               # limit price = ask + 1c (capped at FLIP_MAX_PRICE); order is immediate-or-cancel
FLIP_MAX_ATTEMPTS_PER_TICKER = 3
FLIP_STALE_SECONDS = 15            # board data older than this is not trusted
EDGE_BOARD_URL = os.getenv("EDGE_BOARD_URL", "https://edge-board-production.up.railway.app/api/state")

_board = {"data": {}, "fetched": 0.0, "ok_at": 0.0}
_attempts = {}
_red_since = {}
_sold_red = set()   # tickers already sold on red this session -- never re-buy the same round (no buy/sell churn)


def _side_numbers(m: dict, age: float, coins: dict = None):
    side = m.get("best_side")
    p_yes = m.get("model_p")
    if side not in ("yes", "no") or p_yes is None:
        return None
    p = p_yes if side == "yes" else 1 - p_yes
    lo, hi = m.get("p_lo"), m.get("p_hi")
    worst = None
    if lo is not None and hi is not None:
        worst = lo if side == "yes" else 1 - hi
    z = m.get("z")
    zs = None if z is None else z * (1 if side == "yes" else -1)
    tl = m.get("t_left")
    tl = None if tl is None else tl - age
    spread = m.get("spread")
    lock = (zs is not None and zs >= 2 and p >= 0.95 and (worst is None or worst >= 0.92)
            and tl is not None and tl >= 30 and (spread is None or spread <= 0.03))
    return {"ticker": m.get("ticker"), "coin": m.get("coin"), "side": side, "p": p, "worst": worst, "zs": zs,
            "tl": tl, "spread": spread, "lock": lock, "yes_ask": m.get("yes_ask"), "no_ask": m.get("no_ask"),
            "title": m.get("title", m.get("ticker")),
            "chg_1m": ((coins or {}).get(m.get("coin")) or {}).get("chg_1m"),
            "chg_5m": ((coins or {}).get(m.get("coin")) or {}).get("chg_5m"),
            "mid_chg_1m": m.get("mid_chg_1m")}


def board(force: bool = False) -> dict:
    """ticker -> numbers for every live 15-minute window on the dashboard (cached ~1s). {} when unavailable/stale."""
    now = time.time()
    if not force and now - _board["fetched"] < 1.0:
        return _board["data"] if now - _board["ok_at"] <= FLIP_STALE_SECONDS else {}
    _board["fetched"] = now
    try:
        r = requests.get(EDGE_BOARD_URL, timeout=4, headers={"Cache-Control": "no-store"})
        r.raise_for_status()
        st = r.json()
        age = max(0.0, now - float(st.get("now", now)))
        if age > FLIP_STALE_SECONDS:
            print(f"[FLIP] dashboard data is {age:.0f}s old -- not trusting it.")
            return {}
        out = {}
        for m in st.get("markets", []):
            if m.get("kind") != "15m":
                continue
            if _is_excluded(m.get("coin"), m.get("ticker")):
                continue
            n = _side_numbers(m, age, st.get("coins"))
            if n and n["ticker"]:
                out[n["ticker"]] = n
        _board["data"], _board["ok_at"] = out, now
    except Exception as e:
        print(f"[FLIP] couldn't read the dashboard ({e}) -- no new flip-star entries until it's back.")
    return _board["data"] if now - _board["ok_at"] <= FLIP_STALE_SECONDS else {}


_knife_note = {}


def _falling_against(n: dict) -> bool:
    if not FLIP_KNIFE_GUARD:
        return False
    """True when the coin or our side's price is moving against us right now -- don't buy into a drop."""
    sgn = 1 if n["side"] == "yes" else -1
    why = None
    mc = n.get("mid_chg_1m")
    if mc is not None and mc * sgn <= -FLIP_MAX_SIDE_DROP_1M + 1e-9:
        why = f"our side's price fell {abs(mc)*100:.0f}c in the last minute"
    c1 = n.get("chg_1m")
    if why is None and c1 is not None and c1 * sgn <= -FLIP_MAX_SPOT_AGAINST_1M + 1e-9:
        why = f"{n.get('coin')} moved {abs(c1):.3f}% against us in the last minute"
    if why and time.time() - _knife_note.get(n.get("ticker"), 0) > 30:
        _knife_note[n.get("ticker")] = time.time()
        print(f"[FLIP] {n.get('ticker')}: skipping -- {why} (falling-knife guard).")
    return why is not None


def _is_excluded(coin, ticker) -> bool:
    """True for coins we never bid on (FLIP_EXCLUDED_COINS). Matches the coin name or the KX<COIN> ticker prefix."""
    c = str(coin or "").upper()
    t = str(ticker or "").upper()
    return c in FLIP_EXCLUDED_COINS or any(t.startswith("KX" + x) for x in FLIP_EXCLUDED_COINS)


def entry_signal(n: dict) -> bool:
    if _is_excluded(n.get("coin"), n.get("ticker")):
        return False
    if n["zs"] is None or n["tl"] is None or not (FLIP_MIN_SECONDS_LEFT <= n["tl"] <= FLIP_MAX_SECONDS_LEFT):
        return False
    if n["p"] < FLIP_MIN_WIN_ALL - 1e-9:
        return False
    if n["lock"]:
        return True
    if FLIP_RED_STAR_ONLY:
        return False   # red stars only -- no green / yellow entries
    return (n["zs"] >= FLIP_ENTRY_SIGMA and n["p"] >= FLIP_NONSTAR_MIN_WIN - 1e-9
            and n["tl"] >= FLIP_NONSTAR_MIN_SECONDS_LEFT)


def win_now(ticker: str):
    """Model win chance for the favored side right now, or None if the board has no fresh numbers."""
    n = board().get(ticker)
    return None if not n else n["p"]


def held_sigma(ticker: str, held_side: str):
    """'To flip' sigma for the side WE hold ('yes'/'no'), or None without fresh board numbers.
    Negative means the market has crossed to the other side."""
    n = board().get(ticker)
    if not n or n.get("zs") is None:
        return None
    return n["zs"] if n.get("side") == held_side else -n["zs"]


def exit_decision(ticker: str):
    """(should_exit, reason) for a flip-star position, or None if the board has no fresh numbers for it
    (caller then falls back to the normal stop-loss)."""
    n = board().get(ticker)
    if not n or n["zs"] is None:
        return None
    # Lesson from ZEC 15 Oct 22:26: To-flip dropped 1.7 -> 0.9 sigma in one minute on a small wiggle while the model
    # (and the market, at 98c) still gave our side 98%. One red reading sold a winning position for -$0.46.
    # Now a red reading only counts if it lasts and the win % has actually dropped; a real collapse still exits at once.
    now = time.time()
    if n["p"] < FLIP_EXIT_PANIC_WIN:
        _red_since.pop(ticker, None)
        _sold_red.add(ticker)
        return True, f"flip_collapse (win % fell to {n['p']*100:.0f}%, To flip {n['zs']:.1f} sigma)"
    if n["zs"] < FLIP_EXIT_SIGMA:
        since = _red_since.setdefault(ticker, now)
        held = now - since
        if held >= FLIP_EXIT_CONFIRM_SECONDS and n["p"] < FLIP_EXIT_MAX_WIN:
            _red_since.pop(ticker, None)
            _sold_red.add(ticker)
            return True, (f"flip_red (To flip under {FLIP_EXIT_SIGMA:g} sigma for {held:.0f}s, now {n['zs']:.1f}; "
                          f"win {n['p']*100:.0f}%)")
        if held < 1.0:
            print(f"[FLIP] {ticker}: To flip red ({n['zs']:.1f} sigma, win {n['p']*100:.0f}%) -- watching; sells if it stays red "
                  f"{FLIP_EXIT_CONFIRM_SECONDS}s with win under {FLIP_EXIT_MAX_WIN*100:.0f}%, or at once if win drops under "
                  f"{FLIP_EXIT_PANIC_WIN*100:.0f}%.")
        return False, ""
    if ticker in _red_since:
        print(f"[FLIP] {ticker}: back out of red ({n['zs']:.1f} sigma) -- holding.")
        _red_since.pop(ticker, None)
    return False, ""


def _held_count(client, ticker):
    resp = client.get_positions()
    for p in resp.get("market_positions", resp.get("positions", [])):
        if p.get("ticker") == ticker:
            return float(p.get("position_fp", p.get("position", 0)) or 0), float(p.get("market_exposure_dollars", 0) or 0)
    return 0.0, 0.0


_resting = {}   # ticker -> resting red-star bid waiting at FLIP_STAR_REST_PRICE


def pending_tickers() -> set:
    """Tickers with a resting flip-star bid -- the manual-position adopter must leave these alone."""
    return set(_resting)


def _find_order_id(client, ticker, client_order_id, resp):
    for src in (resp or {}, (resp or {}).get("order") or {}):
        if isinstance(src, dict) and src.get("order_id"):
            return src["order_id"]
    try:
        orders = client._request("GET", "/portfolio/orders", params={"ticker": ticker, "status": "resting"}).get("orders", [])
        for o in orders:
            if o.get("client_order_id") == client_order_id:
                return o.get("order_id")
    except Exception as e:
        print(f"[FLIP] {ticker}: couldn't look up the resting order id ({e}).")
    return None


def _record_fill(client, state, state_lock, log_trade, ticker, n, side, count, entry_price, how):
    with state_lock:
        state[ticker] = {"count": count, "entry_price": entry_price, "side": side, "dry_run": False,
                         "peak_gain_per_contract": 0.0, "entry_time": time.time(), "coin": n["coin"],
                         "flip_star": True, "force_exit": False, "entry_win": n.get("p")}
    print(f"[FLIP] FILLED {ticker}: {count:g} {n['side'].upper()} @ ${entry_price:.2f} ({how}) -- holding until To flip turns red.")
    try:
        from types import SimpleNamespace
        log_trade(SimpleNamespace(ticker=ticker, title=n["title"], coin=n["coin"], seconds_remaining=n["tl"],
                                  model_prob=n["p"], edge_pct=None),
                  side, entry_price, count, False, entry_reason="flip_star_red")
    except Exception as e:
        print(f"[FLIP] trade log failed (non-fatal): {e}")


def _check_resting(client, state, state_lock, log_trade, b):
    """Each pass: a resting red-star bid that filled becomes a normal flip-star position; one whose red star is
    gone (or the window is nearly over) is cancelled."""
    for ticker, r in list(_resting.items()):
        n = b.get(ticker) or r["n"]
        still_good = bool(b.get(ticker)) and n["lock"] and entry_signal(n)
        try:
            held, exposure = _held_count(client, ticker)
        except Exception as e:
            print(f"[FLIP] {ticker}: couldn't check the resting bid ({e}).")
            continue
        if held == 0 and still_good:
            continue   # still waiting at the resting price
        # Filled (some or all), or the red star is gone: cancel whatever is left, then take what we got.
        if r.get("order_id"):
            try:
                client.cancel_order(r["order_id"])
            except Exception as e:
                if "not_found" not in str(e) and "404" not in str(e):
                    print(f"[FLIP] {ticker}: cancel of resting bid failed ({e}) -- will try again next pass.")
                    continue
        try:
            held, exposure = _held_count(client, ticker)
        except Exception:
            pass
        _resting.pop(ticker, None)
        count = abs(held)
        if count >= 1e-9:
            entry_price = exposure / count if exposure > 0 else FLIP_STAR_REST_PRICE
            _record_fill(client, state, state_lock, log_trade, ticker, r["n"], r["side"], count, entry_price,
                         f"resting {FLIP_STAR_REST_PRICE*100:.0f}c bid filled")
        else:
            print(f"[FLIP] {ticker}: red star gone or window ending -- resting {FLIP_STAR_REST_PRICE*100:.0f}c bid cancelled, nothing filled.")


def run_entries(client, state: dict, excluded_tickers: set, state_lock, dry_run: bool, log_trade, ensure_shard_funds):
    """Open 4-share flip-star positions where the dashboard says so. Called once per scan loop."""
    global _halt_note_at
    if not FLIP_ENABLED:
        return
    if FLIP_HALTED:
        if _resting:
            _check_resting(client, state, state_lock, log_trade, {})   # halted: cancel any resting bids
        if time.time() - _halt_note_at > 300:
            _halt_note_at = time.time()
            print("[FLIP] PAUSED by request -- no new bets (FLIP_PAUSED=True in flip_star.py)." if FLIP_PAUSED and _loss_count < FLIP_MAX_LOSSES_BEFORE_HALT
                  else f"[FLIP] halted after {_loss_count} losses -- restart Pulse in Railway to resume betting.")
        return
    b = board()
    if _resting:
        _check_resting(client, state, state_lock, log_trade, b)
    if not b:
        return
    for ticker, n in b.items():
        if not entry_signal(n):
            continue
        if ticker in state or ticker in excluded_tickers or ticker in _resting or ticker in _sold_red:
            continue
        if _attempts.get(ticker, 0) >= FLIP_MAX_ATTEMPTS_PER_TICKER:
            continue
        if _falling_against(n):
            continue
        side = "bid" if n["side"] == "yes" else "ask"
        try:
            held, _ = _held_count(client, ticker)
            if held != 0:
                continue   # already holding this market (main strategy or a manual bet) -- don't double up
            # Live quote straight from Kalshi (the board can lag a second or two).
            mk = client.get_market(ticker).get("market", {})
            ya, yb = mk.get("yes_ask_dollars"), mk.get("yes_bid_dollars")
            if ya is None or yb is None:
                continue
            ya, yb = float(ya), float(yb)
            if ya - yb > FLIP_MAX_SPREAD + 1e-9:
                continue
            cost = ya if side == "bid" else 1 - yb                       # what one share of our side costs now
            max_px = FLIP_MAX_PRICE if n["lock"] else FLIP_NONSTAR_MAX_PRICE
            if cost <= 0 or cost > max_px + 1e-9:
                continue
            if not n["lock"] and cost < FLIP_NONSTAR_MIN_PRICE - 1e-9:
                continue   # no red star: only buy at 96c or higher
            if n["lock"] and cost < FLIP_STAR_MIN_PRICE - 1e-9:
                continue   # red star: only buy at the current floor or higher
            limit_side_price = min(max_px, round(cost + FLIP_SLIPPAGE, 2))
            rest = FLIP_RESTING_BIDS and bool(n["lock"]) and cost > FLIP_STAR_REST_PRICE + 1e-9
            if not rest and cost > FLIP_STAR_REST_PRICE + 1e-9 and not FLIP_RESTING_BIDS:
                pass   # resting bids off: buy at the live price (up to the cap) like before
            if rest:
                limit_side_price = FLIP_STAR_REST_PRICE
        except Exception as e:
            print(f"[FLIP] {ticker}: couldn't read the live quote ({e}) -- skipping this pass.")
            continue

        if rest and not dry_run:
            coid = f"pulse-flip-{uuid.uuid4()}"
            print(f"[FLIP] REST {ticker} ({n['coin']}): RED STAR at {cost*100:.0f}c, win {n['p']*100:.0f}%, {int(n['tl'])}s left -- "
                  f"posting a {FLIP_SHARES}-share {n['side'].upper()} bid at ${FLIP_STAR_REST_PRICE:.2f} and waiting")
            try:
                ensure_shard_funds(client, ticker, FLIP_STAR_REST_PRICE * FLIP_SHARES)
                yes_price = FLIP_STAR_REST_PRICE if side == "bid" else round(1 - FLIP_STAR_REST_PRICE, 2)
                resp = client.create_order(ticker=ticker, client_order_id=coid, side=side, count=str(FLIP_SHARES),
                                           price=f"{yes_price:.2f}", time_in_force="good_till_canceled")
            except Exception as e:
                print(f"[FLIP] {ticker}: resting bid failed ({e}).")
                if "market_closed" in str(e) or "market_not_found" in str(e):
                    excluded_tickers.add(ticker)
                continue
            _attempts[ticker] = _attempts.get(ticker, 0) + 1
            _resting[ticker] = {"order_id": _find_order_id(client, ticker, coid, resp), "side": side, "n": n,
                                "placed": time.time()}
            continue

        _attempts[ticker] = _attempts.get(ticker, 0) + 1
        why = "RED STAR" if n["lock"] else (f"To flip GREEN {n['zs']:.1f} sigma" if n["zs"] >= 2 else f"To flip YELLOW 1 (bid zone) {n['zs']:.1f} sigma")
        print(f"[FLIP] ENTRY {ticker} ({n['coin']}): {why}, win {n['p']*100:.0f}%, {int(n['tl'])}s left -- "
              f"{'DRY-RUN ' if dry_run else ''}buying {FLIP_SHARES} {n['side'].upper()} up to ${limit_side_price:.2f}")
        if dry_run:
            with state_lock:
                state[ticker] = {"count": FLIP_SHARES, "entry_price": cost, "side": side, "dry_run": True,
                                 "peak_gain_per_contract": 0.0, "entry_time": time.time(), "coin": n["coin"],
                                 "flip_star": True}
            continue
        try:
            _open = sum(1 for _t, _p in list(state.items()) if isinstance(_p, dict) and _p.get("flip_star"))
            if _open >= FLIP_MAX_OPEN:
                print(f"[FLIP] {ticker}: skipping -- already {_open} Pulse bets open (max {FLIP_MAX_OPEN}).")
                continue
            _cash = float(client.get_balance_cached().get("balance", 0)) / 100
            n_sh = min(FLIP_SHARES, int((FLIP_MAX_BET_FRACTION * _cash) // max(limit_side_price, 0.01)))
            if n_sh < 1:
                print(f"[FLIP] {ticker}: skipping -- {FLIP_MAX_BET_FRACTION*100:.0f}% of the ${_cash:.2f} cash is less than one share at ${limit_side_price:.2f}.")
                continue
            if n_sh < FLIP_SHARES:
                print(f"[FLIP] {ticker}: sizing down to {n_sh} shares ({FLIP_MAX_BET_FRACTION*100:.0f}% of ${_cash:.2f} cash).")
            ensure_shard_funds(client, ticker, limit_side_price * n_sh)
            # Pre-order re-check: a Kalshi IOC order only caps the MAX price, it cannot set a minimum fill price.
            # So re-read the live quote right before sending and skip if it has dropped below the floor or below
            # the price we decided to bid at. (A small race remains between this check and the fill.)
            try:
                _mk2 = client.get_market(ticker).get("market", {})
                _ya2, _yb2 = _mk2.get("yes_ask_dollars"), _mk2.get("yes_bid_dollars")
                if _ya2 is None or _yb2 is None:
                    continue
                _ya2, _yb2 = float(_ya2), float(_yb2)
                live_cost = _ya2 if side == "bid" else 1 - _yb2
                effective_floor = max(FLIP_STAR_MIN_PRICE if n["lock"] else FLIP_NONSTAR_MIN_PRICE, cost)
            except Exception as e:
                print(f"[FLIP] {ticker}: couldn't re-check the live quote ({e}) -- skipping this pass.")
                continue
            if live_cost < effective_floor - 1e-9:
                print(f"[FLIP] {ticker}: SKIPPED: price dropped below bid/floor "
                      f"(live {live_cost:.2f} < floor {effective_floor:.2f}) -- no order sent, no position taken.")
                _attempts[ticker] = FLIP_MAX_ATTEMPTS_PER_TICKER
                continue
            yes_price = limit_side_price if side == "bid" else round(1 - limit_side_price, 2)
            client.create_order(ticker=ticker, client_order_id=f"pulse-flip-{uuid.uuid4()}", side=side,
                                count=str(n_sh), price=f"{yes_price:.2f}", time_in_force="immediate_or_cancel")
        except Exception as e:
            print(f"[FLIP] {ticker}: entry order failed ({e}).")
            if "market_closed" in str(e) or "market_not_found" in str(e):
                excluded_tickers.add(ticker)
            if "too_many_requests" in str(e):
                _attempts[ticker] = max(0, _attempts.get(ticker, 1) - 1)   # Kalshi rate limit, not a real try -- don't burn the 3-per-market budget
            continue
        # Read what actually filled (immediate-or-cancel: whatever didn't fill right away is cancelled).
        filled, exposure = 0.0, 0.0
        for _ in range(4):
            try:
                filled, exposure = _held_count(client, ticker)
            except Exception:
                pass
            if filled != 0:
                break
            time.sleep(0.4)
        count = abs(filled)
        if count < 1e-9:
            print(f"[FLIP] {ticker}: nothing filled at ${limit_side_price:.2f} (price moved) -- will retry if the signal holds.")
            continue
        entry_price = exposure / count if exposure > 0 else cost
        # Lesson from BNB 15 Oct 20:12 CT: the quote passed the 96c floor, but by the time the order hit the book the
        # price had dropped and the order filled at 91c (a buy limit is only a MAXIMUM -- it happily fills cheaper).
        # A cheaper fill means the market just lost confidence, so anything under the floor is sold straight away.
        floor = FLIP_STAR_MIN_PRICE if n["lock"] else FLIP_NONSTAR_MIN_PRICE
        below_floor = entry_price < floor - 0.025   # 25 Oct: a 1-2c cheaper fill is fine (DOGE 93c was sold at 93c and then won)
        with state_lock:
            state[ticker] = {"count": count, "entry_price": entry_price, "side": side, "dry_run": False,
                             "peak_gain_per_contract": 0.0, "entry_time": time.time(), "coin": n["coin"],
                             "flip_star": True, "force_exit": below_floor, "entry_win": n.get("p")}
        if below_floor:
            print(f"[FLIP] FILLED {ticker}: {count:g} {n['side'].upper()} @ ${entry_price:.2f} -- UNDER the {floor*100:.0f}c floor "
                  f"(price dropped between the check and the fill) -- selling right away.")
        else:
            print(f"[FLIP] FILLED {ticker}: {count:g} {n['side'].upper()} @ ${entry_price:.2f} -- holding until To flip turns red.")
        try:
            from types import SimpleNamespace
            log_trade(SimpleNamespace(ticker=ticker, title=n["title"], coin=n["coin"], seconds_remaining=n["tl"],
                                      model_prob=n["p"], edge_pct=None),
                      side, entry_price, count, False, entry_reason="flip_star_red" if n["lock"] else ("flip_star_green" if n["zs"] >= 2 else "flip_star_y1"))
        except Exception as e:
            print(f"[FLIP] trade log failed (non-fatal): {e}")
