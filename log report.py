#!/usr/bin/env python3
"""
Pulse log report: replays Pulse's own Railway logs to show, per coin and per setting, what the
entry rules would have done. It reads log lines like

  [SOL] SOL price up in next 15 mins?   up=$0.96 down=$0.04 model=0.99 edge=+3.1pp shares_left=? t-120s

(with or without a leading timestamp) and rebuilds each 15-minute window from the countdown (t-...s).

Usage:
  python3 log_report.py logs.txt
  python3 log_report.py logs.txt --floor 0.95 --ceiling 0.99 --tmax 140 --model 0.82 --edge 1.5 --shares 3 --stop 0.92
  python3 log_report.py logs.txt --csv trades.csv

Offline analysis only: it never connects to anything. Prices are the logged Kalshi quotes (mid), so
real fills, spreads and fees will differ a little; treat results as a guide, not exact P&L.
"""
import argparse
import csv
import re
import sys
from collections import defaultdict

LINE = re.compile(
    r"\[(?P<coin>[A-Z0-9]+)\].*?up=\$(?P<up>\d*\.?\d+)\s+down=\$(?P<down>\d*\.?\d+)\s+"
    r"model=(?P<model>\d*\.?\d+).*?edge=(?P<edge>[+-]?\d*\.?\d+)pp.*?t-(?P<t>\d+)s")


def parse(path):
    """Returns {coin: [window, ...]}; a window is a list of (t, up, down, model, edge), t descending."""
    snaps = defaultdict(list)
    with open(path, errors="replace") as f:
        for line in f:
            m = LINE.search(line)
            if not m:
                continue
            snaps[m["coin"]].append((int(m["t"]), float(m["up"]), float(m["down"]),
                                     float(m["model"]), float(m["edge"])))
    windows = {}
    for coin, rows in snaps.items():
        ws, cur = [], []
        for r in rows:
            if cur and r[0] > cur[-1][0] + 30:      # countdown jumped up -> a new window began
                ws.append(cur)
                cur = []
            cur.append(r)
        if cur:
            ws.append(cur)
        windows[coin] = ws
    return windows


def settled(window, max_final_t=40):
    """Winner of a window ('up'/'down') from its last snapshot, or None if the log didn't reach the end."""
    t, up, down, *_ = window[-1]
    if t > max_final_t:
        return None
    if up >= 0.9:
        return "up"
    if down >= 0.9:
        return "down"
    return None


def simulate(window, floor, ceiling, tmax, min_model, min_edge, stop):
    """First qualifying snapshot -> entry. Returns dict or None."""
    for i, (t, up, down, model, edge) in enumerate(window):
        if t >= tmax:
            continue
        for side, price, smodel, sedge in (("up", up, model, edge), ("down", down, 1 - model, -edge)):
            if floor <= price <= ceiling and smodel > min_model and sedge > min_edge:
                return {"i": i, "t": t, "side": side, "entry": price}
    return None


def run_window(window, floor, ceiling, tmax, min_model, min_edge, stop):
    winner = settled(window)
    if winner is None:
        return None
    ent = simulate(window, floor, ceiling, tmax, min_model, min_edge, stop)
    if not ent:
        return {"entered": False}
    exit_value, how = None, None
    for t, up, down, *_ in window[ent["i"] + 1:]:
        value = up if ent["side"] == "up" else down
        if value <= ent["entry"] * stop + 1e-9:
            exit_value, how = value, "stop"
            break
    if exit_value is None:
        exit_value = 1.0 if ent["side"] == winner else 0.0
        how = "win" if exit_value == 1.0 else "loss"
    return {"entered": True, "side": ent["side"], "t": ent["t"], "entry": ent["entry"],
            "exit": exit_value, "how": how, "pnl": exit_value - ent["entry"], "winner": winner}


def summarize(windows, **p):
    per_coin, trades = {}, []
    for coin, ws in windows.items():
        s = {"windows": 0, "entries": 0, "win": 0, "stop": 0, "loss": 0, "pnl": 0.0}
        for w in ws:
            r = run_window(w, **p)
            if r is None:
                continue
            s["windows"] += 1
            if not r["entered"]:
                continue
            s["entries"] += 1
            s[r["how"]] += 1
            s["pnl"] += r["pnl"]
            trades.append({"coin": coin, **r})
        per_coin[coin] = s
    return per_coin, trades


def coin_character(windows, at_t=140):
    """Per coin: how often the side leading at ~at_t seconds left went on to lose, and typical late swing."""
    out = {}
    for coin, ws in windows.items():
        n = flips = 0
        swings = []
        for w in ws:
            winner = settled(w)
            if winner is None:
                continue
            snap = next((s for s in w if s[0] <= at_t), None)
            if snap is None:
                continue
            n += 1
            leader = "up" if snap[1] >= snap[2] else "down"
            if leader != winner:
                flips += 1
            late = [s[1] for s in w if s[0] <= at_t]
            if late:
                swings.append(max(late) - min(late))
        if n:
            out[coin] = (n, flips / n, sum(swings) / len(swings))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logfile")
    ap.add_argument("--floor", type=float, default=0.95)
    ap.add_argument("--ceiling", type=float, default=0.99)
    ap.add_argument("--tmax", type=int, default=140)
    ap.add_argument("--model", type=float, default=0.82)
    ap.add_argument("--edge", type=float, default=1.5)
    ap.add_argument("--stop", type=float, default=0.92)
    ap.add_argument("--shares", type=int, default=3)
    ap.add_argument("--csv", help="write every simulated trade to this file")
    a = ap.parse_args()

    windows = parse(a.logfile)
    if not windows:
        sys.exit("No Pulse market lines found in that file.")
    p = dict(floor=a.floor, ceiling=a.ceiling, tmax=a.tmax, min_model=a.model, min_edge=a.edge, stop=a.stop)
    per_coin, trades = summarize(windows, **p)

    print(f"Settings: price {a.floor:.2f}-{a.ceiling:.2f}, t<{a.tmax}s, model>{a.model}, edge>{a.edge}pp, "
          f"stop {a.stop:.0%}, {a.shares} shares\n")
    print(f"{'coin':<6}{'windows':>8}{'entries':>8}{'wins':>6}{'stops':>6}{'losses':>7}{'win%':>7}{'net $':>9}")
    tot = defaultdict(float)
    for coin in sorted(per_coin, key=lambda c: -per_coin[c]["pnl"]):
        s = per_coin[coin]
        if not s["windows"]:
            continue
        wr = f"{s['win'] / s['entries'] * 100:.0f}%" if s["entries"] else "-"
        print(f"{coin:<6}{s['windows']:>8}{s['entries']:>8}{s['win']:>6}{s['stop']:>6}{s['loss']:>7}"
              f"{wr:>7}{s['pnl'] * a.shares:>+9.2f}")
        for k in ("windows", "entries", "win", "stop", "loss"):
            tot[k] += s[k]
        tot["pnl"] += s["pnl"]
    wr = f"{tot['win'] / tot['entries'] * 100:.0f}%" if tot["entries"] else "-"
    print(f"{'ALL':<6}{int(tot['windows']):>8}{int(tot['entries']):>8}{int(tot['win']):>6}{int(tot['stop']):>6}"
          f"{int(tot['loss']):>7}{wr:>7}{tot['pnl'] * a.shares:>+9.2f}")

    print("\nCoin character (the side leading at 140s left: how often it still lost, and the typical late swing):")
    for coin, (n, flip, swing) in sorted(coin_character(windows).items(), key=lambda kv: -kv[1][1]):
        print(f"  {coin:<6} {n:>3} windows  leader lost {flip * 100:4.0f}%   avg late swing {swing * 100:4.0f}c")

    print("\nSetting sweep (all coins, net $ with your share size / entries):")
    tmaxes = [60, 110, 140, 210, 300]
    print(f"  {'floor':<8}" + "".join(f"t<{t:<10}" for t in tmaxes))
    for floor in (0.91, 0.93, 0.95, 0.97):
        row = f"  {floor:<8.2f}"
        for t in tmaxes:
            _, tr = summarize(windows, **{**p, "floor": floor, "tmax": t})
            row += f"{sum(x['pnl'] for x in tr) * a.shares:+7.2f}/{len(tr):<4}"
        print(row)

    if a.csv:
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["coin", "side", "t", "entry", "exit", "how", "pnl", "winner"])
            w.writeheader()
            for tr in trades:
                w.writerow({k: tr.get(k) for k in w.fieldnames})
        print(f"\nWrote {len(trades)} trades to {a.csv}")


if __name__ == "__main__":
    main()
