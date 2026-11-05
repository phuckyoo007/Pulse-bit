"""
Live Kalshi price feed (5 Nov 12:25 CT, Matt) -- Kalshi's streaming "ticker" channel over a websocket, so the stop-loss /
take-profit checks see every price change the moment it happens instead of polling the REST API (which is capped at
about 20 lookups a second).

  * One background thread keeps a websocket open, subscribed to the markets we hold (added/removed as positions change).
  * Kalshi pushes a "ticker" message every time a market's bid/ask changes; we keep the latest per market.
  * overlay(ticker, market) copies the live bid/ask onto a REST market dict. If the feed is down or hasn't seen that
    market yet, it returns the market unchanged -- so the bot falls back to the old REST prices on its own.
"""
import json
import threading
import time

try:
    import websocket   # websocket-client
except Exception:      # library missing -> feed simply stays off, REST prices are used
    websocket = None

WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
WS_SIGN_PATH = "/trade-api/ws/v2"
MAX_QUIET_SECONDS = 25   # no message or heartbeat for this long -> treat the feed as down until it reconnects

_quotes = {}        # ticker -> {"yb": float, "ya": float, "at": time}
_want = set()       # tickers we want live prices for
_subbed = set()     # tickers currently subscribed
_lock = threading.Lock()
_st = {"ws": None, "sid": None, "connected": False, "last_msg": 0.0, "next_id": 1, "started": False,
       "logged_fields": False}


def _log(msg):
    print(f"[LIVE FEED] {msg}")


def healthy() -> bool:
    return _st["connected"] and time.time() - _st["last_msg"] < MAX_QUIET_SECONDS


def quote(ticker: str):
    """(yes_bid, yes_ask) from the live feed, or None."""
    if not healthy():
        return None
    with _lock:
        q = _quotes.get(ticker)
    if not q or q.get("yb") is None or q.get("ya") is None:
        return None
    return q["yb"], q["ya"]


def overlay(ticker: str, market: dict) -> dict:
    q = quote(ticker)
    if q is None or not isinstance(market, dict):
        return market
    yb, ya = q
    m = dict(market)
    m["yes_bid_dollars"] = f"{yb:.4f}"
    m["yes_ask_dollars"] = f"{ya:.4f}"
    if "no_bid_dollars" in m:
        m["no_bid_dollars"] = f"{1 - ya:.4f}"
    if "no_ask_dollars" in m:
        m["no_ask_dollars"] = f"{1 - yb:.4f}"
    m["_live"] = True
    return m


def reconcile(ticker: str, market: dict) -> None:
    """Safety net: a fresh REST lookup that disagrees with a live quote we haven't heard about in 5s+ means the stream
    missed something -- drop that quote so REST prices are used until the stream sends a new one."""
    try:
        rb, ra = _f(market.get("yes_bid_dollars")), _f(market.get("yes_ask_dollars"))
    except Exception:
        return
    if rb is None or ra is None:
        return
    with _lock:
        q = _quotes.get(ticker)
        if not q or time.time() - q.get("at", 0) < 5:
            return
        if abs((q.get("yb") or 0) - rb) > 0.005 or abs((q.get("ya") or 0) - ra) > 0.005:
            _quotes.pop(ticker, None)
            bad = True
        else:
            q["at"] = time.time()   # REST agrees -- still good
            bad = False
    if bad:
        _log(f"{ticker}: live price disagreed with a fresh lookup -- using lookups for it until the stream updates.")


def watch(tickers) -> None:
    """Tell the feed which markets to stream (call with the currently held tickers)."""
    new = set(t for t in tickers if t)
    with _lock:
        if new == _want:
            return
        _want.clear()
        _want.update(new)
    _sync()


def _send(obj):
    ws = _st["ws"]
    if ws is None or not _st["connected"]:
        return False
    try:
        obj["id"] = _st["next_id"]
        _st["next_id"] += 1
        ws.send(json.dumps(obj))
        return True
    except Exception as e:
        _log(f"send failed ({e}).")
        return False


def _sync():
    with _lock:
        add = sorted(_want - _subbed)
        drop = sorted(_subbed - _want)
    if not _st["connected"]:
        return
    if add:
        if _st["sid"] is None:
            ok = _send({"cmd": "subscribe", "params": {"channels": ["ticker"], "market_tickers": add}})
        else:
            ok = _send({"cmd": "update_subscription",
                        "params": {"sids": [_st["sid"]], "market_tickers": add, "action": "add_markets"}})
        if ok:
            with _lock:
                _subbed.update(add)
    if drop and _st["sid"] is not None:
        if _send({"cmd": "update_subscription",
                  "params": {"sids": [_st["sid"]], "market_tickers": drop, "action": "delete_markets"}}):
            with _lock:
                _subbed.difference_update(drop)
                for t in drop:
                    _quotes.pop(t, None)


def _f(v):
    try:
        return None if v is None else float(v)
    except Exception:
        return None


def _on_message(ws, raw):
    _st["last_msg"] = time.time()
    try:
        d = json.loads(raw)
    except Exception:
        return
    typ = d.get("type")
    if typ == "subscribed":
        sid = (d.get("msg") or {}).get("sid")
        if sid is not None and _st["sid"] is None:
            _st["sid"] = sid
            _log(f"subscribed (sid {sid}).")
        return
    if typ == "error":
        _log(f"Kalshi error: {d.get('msg')}")
        return
    if typ != "ticker":
        return
    m = d.get("msg") or {}
    t = m.get("market_ticker")
    if not t:
        return
    if not _st["logged_fields"]:
        _st["logged_fields"] = True
        _log(f"first price update ({t}): fields {sorted(m.keys())}")
    yb = _f(m.get("yes_bid_dollars"))
    ya = _f(m.get("yes_ask_dollars"))
    if yb is None and m.get("yes_bid") is not None:
        yb = _f(m.get("yes_bid")) / 100
    if ya is None and m.get("yes_ask") is not None:
        ya = _f(m.get("yes_ask")) / 100
    with _lock:
        q = _quotes.setdefault(t, {})
        if yb is not None:
            q["yb"] = yb
        if ya is not None:
            q["ya"] = ya
        q["at"] = time.time()


def _on_open(ws):
    _st["connected"] = True
    _st["last_msg"] = time.time()
    _st["sid"] = None
    with _lock:
        _subbed.clear()
        _quotes.clear()
    _log("connected.")
    _sync()


def _on_close(ws, code, reason):
    if _st["connected"]:
        _log(f"disconnected ({code} {reason}) -- using REST prices until it reconnects.")
    _st["connected"] = False


def _on_error(ws, err):
    _log(f"error: {err}")


def _on_ping(ws, data):
    _st["last_msg"] = time.time()


def _run(client):
    backoff = 2
    while True:
        try:
            headers = client._sign("GET", WS_SIGN_PATH)
            headers.pop("Content-Type", None)
            ws = websocket.WebSocketApp(WS_URL, header=[f"{k}: {v}" for k, v in headers.items()],
                                        on_open=_on_open, on_message=_on_message, on_close=_on_close,
                                        on_error=_on_error, on_ping=_on_ping)
            _st["ws"] = ws
            t0 = time.time()
            ws.run_forever(ping_interval=20, ping_timeout=10)
            if time.time() - t0 > 60:
                backoff = 2
        except Exception as e:
            _log(f"crashed ({e}).")
        _st["connected"] = False
        time.sleep(backoff)
        backoff = min(backoff * 2, 60)


def start(client) -> None:
    if _st["started"]:
        return
    _st["started"] = True
    if websocket is None:
        _log("websocket-client isn't installed -- live feed off, using REST prices.")
        return
    threading.Thread(target=_run, args=(client,), name="kalshi-ws", daemon=True).start()
    _log("starting.")
