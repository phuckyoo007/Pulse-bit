"""
Per-strategy loss limit (11 Nov 11:24 CT, Matt): once any strategy's bets have lost $4.00 in total (net, since the bot
started), that strategy is switched off until the bot is restarted. Other strategies keep running.
"""
STRATEGY_MAX_LOSS = 4.00
_pnl = {}       # strategy -> net P/L since start
_off = set()


def _which(p: dict):
    for flag in ("early93", "late80", "late60", "btc92", "rising", "rise"):
        if p.get(flag):
            return flag
    if p.get("flip_star") and not p.get("manually_adopted"):
        return "flip_star"
    return None


def _switch_off(name):
    import btc92, early93, flip_star, late60, late80
    if name == "early93":
        early93.EARLY93_ENABLED = False
    elif name == "late80":
        late80.LATE80_ENABLED = False
    elif name == "late60":
        late60.LATE60_ENABLED = False
    elif name == "btc92":
        btc92.BTC92_ENABLED = False
    elif name == "flip_star":
        flip_star.FLIP_ENABLED = False


def record(position, pnl) -> None:
    try:
        if not isinstance(position, dict):
            return
        name = _which(position)
        if name is None:
            return
        _pnl[name] = _pnl.get(name, 0.0) + float(pnl)
        print(f"[LOSS LIMIT] {name} bet closed {float(pnl):+.2f} -- {name} net {_pnl[name]:+.2f} since start "
              f"(switches off at -${STRATEGY_MAX_LOSS:.2f}).")
        if name not in _off and _pnl[name] <= -STRATEGY_MAX_LOSS + 1e-9:
            _off.add(name)
            _switch_off(name)
            print(f"[LOSS LIMIT] {name} OFF -- it has lost ${-_pnl[name]:.2f} since start. Stays off until the bot restarts.")
    except Exception as e:
        print(f"[LOSS LIMIT] check failed ({e}).")
