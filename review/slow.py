"""A second implementation of every metric, written to be obviously correct.

No SQL aggregation, no window functions: it reads one customer's raw rows and
counts things in plain Python. It's far too slow to run for everybody, which is
fine. Its job is to disagree with the fast version when the fast version is wrong.

The two share nothing except the tie-break rules, which are stated in both.
"""

import datetime as dt
from collections import Counter, defaultdict

from . import FULL_CARD_MINIMUM_PAYMENTS, NEWCOMER_DAYS, PAYMENT_TYPES

UTC_OFFSET = dt.timedelta(hours=3)


def recount(con, customer_id, year):
    """Returns the expected year_in_review row for one customer as a dict."""
    signup_date, is_test = con.execute(
        "SELECT signup_date, is_test_account FROM src.customers WHERE customer_id = ?", (customer_id,)
    ).fetchone()
    assert not is_test, "test accounts get no card"
    categories = dict(con.execute("SELECT merchant_id, category FROM src.merchants"))

    payments = []
    for txn_id, ts, txn_type, counterparty, merchant_id, amount, status in con.execute(
        "SELECT txn_id, txn_ts, txn_type, counterparty_customer_id, merchant_id, amount_minor, status "
        "FROM src.transactions WHERE customer_id = ?",
        (customer_id,),
    ):
        local = dt.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") + UTC_OFFSET
        if local.year == year and status == "success" and txn_type in PAYMENT_TYPES:
            payments.append((txn_id, local, txn_type, counterparty, merchant_id, amount))

    row = {"customer_id": customer_id, "signup_date": signup_date}
    newcomer_cutoff = (dt.date(year + 1, 1, 1) - dt.timedelta(days=NEWCOMER_DAYS)).isoformat()

    if not payments:
        row.update({"card_variant": "quiet", "persona": None})
        return row

    count = len(payments)
    if signup_date >= newcomer_cutoff:
        variant = "newcomer"
    elif count < FULL_CARD_MINIMUM_PAYMENTS:
        variant = "light"
    else:
        variant = "full"

    days = sorted({p[1].date() for p in payments})
    longest = run = 1
    for previous, current in zip(days, days[1:]):
        run = run + 1 if (current - previous).days == 1 else 1
        longest = max(longest, run)

    biggest = min(payments, key=lambda p: (-p[5], p[0]))

    by_month = defaultdict(lambda: [0, 0])
    for p in payments:
        by_month[p[1].month][0] += 1
        by_month[p[1].month][1] += p[5]
    busiest_month = min(by_month, key=lambda m: (-by_month[m][0], -by_month[m][1], m))

    by_merchant = defaultdict(lambda: [0, 0])
    by_category = Counter()
    for p in payments:
        if p[4] is not None:
            by_merchant[p[4]][0] += 1
            by_merchant[p[4]][1] += p[5]
            by_category[categories[p[4]]] += p[5]
    favourite = min(by_merchant, key=lambda m: (-by_merchant[m][0], -by_merchant[m][1], m)) if by_merchant else None
    top_category = min(by_category, key=lambda c: (-by_category[c], c)) if by_category else None

    night = sum(1 for p in payments if p[1].hour >= 22 or p[1].hour < 5)
    bills = sum(1 for p in payments if p[2] == "bill_payment")
    p2p = sum(1 for p in payments if p[2] == "p2p_transfer")
    favourite_visits = by_merchant[favourite][0] if favourite is not None else None

    persona = None
    if variant == "full":
        if night / count >= 0.35:
            persona = "night_owl"
        elif bills / count >= 0.40:
            persona = "bill_tamer"
        elif p2p / count >= 0.50:
            persona = "generous_friend"
        elif (favourite_visits or 0) >= 10:
            persona = "regular"
        elif len(by_merchant) >= 15:
            persona = "explorer"
        else:
            persona = "all_rounder"

    row.update(
        {
            "card_variant": variant,
            "payments_made": count,
            "total_spent_minor": sum(p[5] for p in payments),
            "active_days": len(days),
            "first_payment_date": days[0].isoformat(),
            "people_paid": len({p[3] for p in payments if p[3] is not None}),
            "merchants_visited": len(by_merchant),
            "night_payments": night,
            "bill_payments": bills,
            "p2p_payments": p2p,
            "biggest_payment_minor": biggest[5],
            "biggest_payment_date": biggest[1].date().isoformat(),
            "busiest_month": busiest_month,
            "busiest_month_payments": by_month[busiest_month][0],
            "favourite_merchant_id": favourite,
            "favourite_merchant_visits": favourite_visits,
            "top_category": top_category,
            "top_category_minor": by_category[top_category] if top_category else None,
            "longest_streak_days": longest,
            "persona": persona,
        }
    )
    return row
