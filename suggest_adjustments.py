"""
Looks for patterns in real trade history that are actually statistically
distinguishable from noise, and prints concrete suggestions -- it never
changes bot.py's parameters itself. That's a deliberate choice, not a
missing feature: with sample sizes this small, an auto-tuning system
would mostly learn to chase noise, confidently "correcting" itself based
on random variation. You stay the one who decides whether a suggestion
is worth acting on.

Two things guard against false patterns:
  1. A MINIMUM SAMPLE SIZE per bucket (default 30) -- a pattern in 5
     trades isn't a pattern, it's an anecdote.
  2. A real significance test (two-proportion z-test) comparing each
     bucket's win rate against everything else, not just eyeballing
     whether the numbers look different.

Usage: python3 suggest_adjustments.py
"""
import math
from collections import defaultdict
from pathlib import Path
import json

MIN_SAMPLE_SIZE = 30
SIGNIFICANCE_Z = 1.96   # ~95% two-tailed


def load(path):
    return json.loads(path.read_text()) if path.exists() else []


def two_proportion_z_test(wins_a, n_a, wins_b, n_b):
    """Returns the z-score for whether group A's win rate differs from
    group B's by more than chance would explain. None if either group
    is too small to compute anything meaningful."""
    if n_a == 0 or n_b == 0:
        return None
    p_a, p_b = wins_a / n_a, wins_b / n_b
    p_pool = (wins_a + wins_b) / (n_a + n_b)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n_a + 1 / n_b))
    if se == 0:
        return None
    return (p_a - p_b) / se


def bucket_time(seconds):
    if seconds is None:
        return None
    if seconds < 120:
        return "near-expiry (45-120s)"
    if seconds < 300:
        return "2-5 min"
    return "5min+"


def bucket_edge(edge_pct):
    if edge_pct is None:
        return None
    e = abs(edge_pct)
    if e < 15:
        return "8-15pp"
    if e < 25:
        return "15-25pp"
    return "25pp+"


def analyze_dimension(name, joined, bucket_fn):
    suggestions = []
    buckets = defaultdict(list)
    for r in joined:
        b = bucket_fn(r)
        if b:
            buckets[b].append(r)

    for bucket_name, rows in buckets.items():
        if len(rows) < MIN_SAMPLE_SIZE:
            continue
        rest = [r for r in joined if r not in rows]
        if len(rest) < MIN_SAMPLE_SIZE:
            continue

        wins_bucket = sum(1 for r in rows if r["pnl"] > 0)
        wins_rest = sum(1 for r in rest if r["pnl"] > 0)
        z = two_proportion_z_test(wins_bucket, len(rows), wins_rest, len(rest))
        if z is None:
            continue

        bucket_wr = wins_bucket / len(rows) * 100
        rest_wr = wins_rest / len(rest) * 100

        if abs(z) >= SIGNIFICANCE_Z:
            direction = "WORSE" if bucket_wr < rest_wr else "BETTER"
            suggestions.append(
                f"[{name}] '{bucket_name}' (n={len(rows)}) wins {bucket_wr:.1f}% of the time vs "
                f"{rest_wr:.1f}% for everything else (n={len(rest)}) -- statistically {direction} "
                f"(z={z:.2f}, |z|>=1.96). Worth considering an adjustment here."
            )
        else:
            print(f"  [{name}] '{bucket_name}': {bucket_wr:.1f}% vs {rest_wr:.1f}% baseline "
                  f"(n={len(rows)}) -- not statistically significant yet (z={z:.2f}), no suggestion.")
    return suggestions


def main():
    trades = [t for t in load(Path("trades.json")) if t.get("dry_run") is False]
    settlements = [s for s in load(Path("settlements.json")) if s.get("dry_run") is False]
    trades_by_ticker = {t["ticker"]: t for t in trades}

    joined = []
    for s in settlements:
        t = trades_by_ticker.get(s["ticker"])
        joined.append({
            "pnl": s["pnl"],
            "coin": s.get("coin") or (t.get("coin") if t else None),
            "edge_pct": t.get("edge_pct") if t else None,
            "seconds_remaining_at_entry": t.get("seconds_remaining_at_entry") if t else None,
        })

    print(f"Analyzing {len(joined)} real settled trades (minimum {MIN_SAMPLE_SIZE} per bucket to test anything)...\n")

    if len(joined) < MIN_SAMPLE_SIZE * 2:
        print(f"Not enough real trades yet ({len(joined)}) to test any bucket against a same-size "
              f"baseline. Come back once you have more real settled trades -- forcing a conclusion "
              f"from too little data is exactly the failure mode this script exists to avoid.")
        return

    all_suggestions = []
    all_suggestions += analyze_dimension("time-to-expiry", joined, lambda r: bucket_time(r["seconds_remaining_at_entry"]))
    all_suggestions += analyze_dimension("edge size", joined, lambda r: bucket_edge(r["edge_pct"]))
    all_suggestions += analyze_dimension("coin", joined, lambda r: r["coin"])

    print()
    if all_suggestions:
        print("STATISTICALLY SIGNIFICANT PATTERNS FOUND:")
        for s in all_suggestions:
            print(f"  - {s}")
        print("\nThese are suggestions to consider, not instructions -- nothing has been changed "
              "automatically. Decide for yourself whether each is worth acting on.")
    else:
        print("No statistically significant patterns found in the dimensions checked. "
              "That's a legitimate result, not a failure of the script -- it means nothing here "
              "clears the bar for 'more than noise' yet.")


if __name__ == "__main__":
    main()
