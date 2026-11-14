"""
Per-strategy loss limit (11 Nov 11:24 CT, Matt): once any strategy's bets have lost $4.00 in total (net, since the bot
started), that strategy is switched off until the bot is restarted. Other strategies keep running.
"""
import time

STRATEGY_MAX_LOSS = 4.00
MAX_LOSS_BY_STRATEGY = {"flip_star": 2.00}   # 12 Nov 15:08 CT: Flip Star switches off sooner (-$2)

# 12 Nov 14:53 CT (Matt: "tighten every strategy so the losses don't happen again").
# Paper + real records 11-12 Nov: most losses come in rounds where SEVERAL coins crash together (all 7 coins move
# together -- e.g. noon 11 Nov lost 5 coins in one round, 6 AM 12 Nov lost 4). So:
#   * ONE coin per 15-minute round across every real-money strategy (the most stable coin that qualifies first);
#   * after ANY losing real bet, no new real bets for COOLDOWN_AFTER_LOSS seconds (losses come in clusters).
ONE_COIN_PER_ROUND = True
COOLDOWN_AFTER_LOSS = 1800
_rounds = {}          # round id (e.g. 26NOV121500) -> ticker we bet
_cool_until = [0.0]
_BOT_FLAGS = ("early93", "late78", "momride", "ride82", "last3", "late80", "late60", "btc92", "flip_star", "rising", "rise")
_own = {}            # (round id, strategy) -> ticker, for strategies with their own slot
_said = {}
_pnl = {}       # strategy -> net P/L since start
_off = set()


def _which(p: dict):
    for flag in ("early93", "late78", "momride", "ride82", "last3", "late80", "late60", "btc92", "rising", "rise"):
        if p.get(flag):
            return flag
    if p.get("flip_star") and not p.get("manually_adopted"):
        return "flip_star"
    return None


def _switch_off(name):
    import btc92, early93, flip_star, last3, late60, late78, late80, momride, ride82
    if name == "early93":
        early93.EARLY93_ENABLED = False
    elif name == "late78":
        late78.LATE78_ENABLED = False
    elif name == "momride":
        momride.MOMRIDE_ENABLED = False
    elif name == "ride82":
        ride82.RIDE82_ENABLED = False
    elif name == "last3":
        last3.LAST3_ENABLED = False
    elif name == "late80":
        late80.LATE80_ENABLED = False
    elif name == "late60":
        late60.LATE60_ENABLED = False
    elif name == "btc92":
        btc92.BTC92_ENABLED = False
    elif name == "flip_star":
        flip_star.FLIP_ENABLED = False


def _round(ticker):
    parts = str(ticker).split("-")
    return parts[1] if len(parts) > 1 else str(ticker)


def _say(key, msg):
    now = time.time()
    if now - _said.get(key, 0) > 60:
        _said[key] = now
        print(msg)


def can_enter(ticker, state=None, tag="", own_slot=False) -> bool:
    """False while cooling down after a loss, or if another coin is already bet this round.
    own_slot=True (13 Nov, RIDE82): the strategy gets its own one-bet-per-round slot instead of sharing."""
    now = time.time()
    if now < _cool_until[0]:
        _say("cool", f"[GUARD] cooling down after a loss -- no new real bets for {(_cool_until[0] - now) / 60:.0f} more min.")
        return False
    if own_slot:
        k = (_round(ticker), tag)
        if k in _own and _own[k] != ticker:
            _say("o" + k[0] + tag, f"[GUARD] {tag} {ticker}: {tag} already has a bet this round ({_own[k]}).")
            return False
        return True
    if ONE_COIN_PER_ROUND:
        r = _round(ticker)
        if r in _rounds and _rounds[r] != ticker:
            _say("r" + r + tag, f"[GUARD] {tag} {ticker}: already have a bet this round ({_rounds[r]}) -- one coin per round.")
            return False
        for t, pos in list((state or {}).items()):
            # 13 Nov 16:12 CT (Matt: "nothing is bidding"): only the BOT's own crypto bets count -- Matt's manual bets
            # (and commodity markets like KXCOPPER15M) were blocking every strategy for the whole round.
            if not isinstance(pos, dict) or not any(pos.get(f) for f in _BOT_FLAGS):
                continue
            if t != ticker and _round(t) == r and "15M" in str(t).split("-")[0].upper():
                _say("s" + r + tag, f"[GUARD] {tag} {ticker}: already holding {t} this round -- one coin per round.")
                return False
    return True


def note_entry(ticker, own_slot=None) -> None:
    if own_slot:
        _own[(_round(ticker), own_slot)] = ticker
        if len(_own) > 200:
            for k in list(_own)[:100]:
                _own.pop(k, None)
        return
    _rounds[_round(ticker)] = ticker
    if len(_rounds) > 200:
        for k in list(_rounds)[:100]:
            _rounds.pop(k, None)


def record(position, pnl) -> None:
    try:
        if not isinstance(position, dict):
            return
        name = _which(position)
        if name is None:
            return
        _pnl[name] = _pnl.get(name, 0.0) + float(pnl)
        if float(pnl) < -0.005:
            _cool_until[0] = time.time() + COOLDOWN_AFTER_LOSS
            print(f"[GUARD] {name} lost {float(pnl):+.2f} -- pausing ALL new real bets for {COOLDOWN_AFTER_LOSS // 60} min.")
        print(f"[LOSS LIMIT] {name} bet closed {float(pnl):+.2f} -- {name} net {_pnl[name]:+.2f} since start "
              f"(switches off at -${MAX_LOSS_BY_STRATEGY.get(name, STRATEGY_MAX_LOSS):.2f}).")
        if name not in _off and _pnl[name] <= -MAX_LOSS_BY_STRATEGY.get(name, STRATEGY_MAX_LOSS) + 1e-9:
            _off.add(name)
            _switch_off(name)
            print(f"[LOSS LIMIT] {name} OFF -- it has lost ${-_pnl[name]:.2f} since start. Stays off until the bot restarts.")
    except Exception as e:
        print(f"[LOSS LIMIT] check failed ({e}).")
