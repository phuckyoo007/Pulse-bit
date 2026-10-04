"""
Every fee number this project has used until now (apply_real_fees.py,
the modest_profit exit rule) is based on a FORMULA that ESTIMATES what
Kalshi charges -- confirmed against Kalshi's documented fee schedule,
but never checked against what you were ACTUALLY charged on a real
fill. This does that check.

Kalshi's /portfolio/fills endpoint returns real, executed fills,
including the real fee paid on each one. This pulls that data and
compares the real total against what our formula would have estimated
for the same fills.

ONE HONEST CAVEAT: the exact field name Kalshi uses for the fee amount
on a fill wasn't independently verified against a live response while
building this (no network access in this environment). This checks
several likely candidates defensively -- if none match, it says so
plainly rather than silently reporting a wrong number as if it were
right.

Usage: python3 check_real_fees.py
"""
from kalshi_client import KalshiClient
from apply_real_fees import fee_per_contract

FEE_FIELD_CANDIDATES = ["fee", "fee_dollars", "fee_cents", "taker_fee", "taker_fee_dollars"]


def _extract_fee_dollars(fill: dict):
    for field in FEE_FIELD_CANDIDATES:
        if field in fill and fill[field] is not None:
            value = float(fill[field])
            # cents-named fields need dividing; dollar-named fields don't
            return value / 100 if "cents" in field else value
    return None


def main():
    client = KalshiClient()
    try:
        resp = client.get_fills(limit=100)
    except Exception as e:
        print(f"Couldn't fetch real fills ({e}) -- can't do this comparison right now.")
        return

    fills = resp.get("fills", [])
    if not fills:
        print("No fills returned -- either no real trading history yet, or the response "
              "shape doesn't match what this expects (see the raw response check below).")
        print(f"Raw response keys: {list(resp.keys())}")
        return

    real_fee_total = 0.0
    real_fee_found_count = 0
    estimated_fee_total = 0.0

    for fill in fills:
        real_fee = _extract_fee_dollars(fill)
        price = fill.get("price") or fill.get("yes_price") or fill.get("price_dollars")
        count = fill.get("count") or fill.get("size")

        if real_fee is not None:
            real_fee_total += real_fee
            real_fee_found_count += 1

        if price is not None and count is not None:
            estimated_fee_total += fee_per_contract(float(price)) * float(count)

    print(f"Real fills checked: {len(fills)}")
    print(f"Fills where a real fee field was found: {real_fee_found_count}")

    if real_fee_found_count == 0:
        print("\nNone of the expected fee field names were found on any fill.")
        print(f"Checked for: {FEE_FIELD_CANDIDATES}")
        print(f"Here's one raw fill so the real field name can be found and fixed: {fills[0]}")
        return

    print(f"\nReal total fees actually charged: ${real_fee_total:.2f}")
    print(f"Our formula's estimate for the same fills: ${estimated_fee_total:.2f}")
    diff = real_fee_total - estimated_fee_total
    if abs(diff) < 0.05:
        print(f"Difference: ${diff:+.2f} -- the estimate has been genuinely close to reality.")
    else:
        print(f"Difference: ${diff:+.2f} -- the estimate has NOT been matching reality closely. "
              f"Worth digging into why before trusting fee-aware exit decisions as much.")


if __name__ == "__main__":
    main()
