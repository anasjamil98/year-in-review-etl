"""Validation that has to pass before any card is written.

A year in review is shown to every customer at once, and each of them knows
their own numbers better than we do. So the checks come at it from four sides:
shape (one card each), totals (reconcile to the source), sanity (no impossible
values), and an independent recount of individual customers.
"""

import random
from collections import namedtuple

from . import METRIC_COLUMNS, slow
from .build import year_params
from .cards import build_card

Check = namedtuple("Check", "name passed detail")
COMPARED = ["card_variant", "persona"] + METRIC_COLUMNS


def _scalar(con, sql, params=()):
    return con.execute(sql, params).fetchone()[0]


def one_card_each(con, year):
    params = year_params(year)
    eligible = _scalar(
        con,
        "SELECT COUNT(*) FROM src.customers WHERE is_test_account = 0 AND signup_date < ?",
        (params["year_end_local"],),
    )
    rows, distinct = con.execute("SELECT COUNT(*), COUNT(DISTINCT customer_id) FROM year_in_review").fetchone()
    ok = rows == distinct == eligible
    return Check("one_card_per_eligible_customer", ok, f"{rows} rows, {distinct} distinct, {eligible} eligible")


def no_test_accounts(con):
    leaked = _scalar(
        con,
        "SELECT COUNT(*) FROM year_in_review y JOIN src.customers c ON c.customer_id = y.customer_id "
        "WHERE c.is_test_account = 1",
    )
    return Check("no_test_accounts", leaked == 0, f"{leaked} test accounts have a card")


def totals_reconcile(con, year):
    """Sum of the per-customer figures against one query on the raw table."""
    params = year_params(year)
    source = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(amount_minor), 0) FROM src.transactions "
        "WHERE txn_ts >= ? AND txn_ts < ? AND status = 'success' "
        "AND txn_type IN ('p2p_transfer', 'merchant_payment', 'card_payment', 'bill_payment') "
        "AND customer_id NOT IN (SELECT customer_id FROM src.customers WHERE is_test_account = 1)",
        (params["year_start_utc"], params["year_end_utc"]),
    ).fetchone()
    cards = con.execute(
        "SELECT COALESCE(SUM(payments_made), 0), COALESCE(SUM(total_spent_minor), 0) FROM year_in_review"
    ).fetchone()
    ok = tuple(source) == tuple(cards)
    return Check("totals_reconcile_to_source", ok, f"cards {tuple(cards)}; source {tuple(source)}")


def quiet_cards_are_empty(con):
    filled = " OR ".join(f"{column} IS NOT NULL" for column in METRIC_COLUMNS)
    bad = _scalar(con, f"SELECT COUNT(*) FROM year_in_review WHERE card_variant = 'quiet' AND ({filled})")
    return Check("quiet_cards_carry_no_numbers", bad == 0, f"{bad} quiet cards have a figure on them")


def variants_follow_the_rules(con, year):
    cutoff = year_params(year)["newcomer_cutoff"]
    bad = _scalar(
        con,
        "SELECT COUNT(*) FROM year_in_review WHERE "
        "(card_variant = 'quiet' AND payments_made IS NOT NULL) OR "
        "(card_variant <> 'quiet' AND COALESCE(payments_made, 0) < 1) OR "
        "(card_variant = 'newcomer' AND signup_date < :cutoff) OR "
        "(card_variant IN ('full', 'light') AND signup_date >= :cutoff) OR "
        "(card_variant = 'light' AND payments_made >= 5) OR "
        "(card_variant = 'full' AND payments_made < 5) OR "
        "(card_variant = 'full' AND persona IS NULL) OR "
        "(card_variant <> 'full' AND persona IS NOT NULL) OR "
        "card_variant NOT IN ('full', 'light', 'newcomer', 'quiet')",
        {"cutoff": cutoff},
    )
    return Check("variants_follow_the_rules", bad == 0, f"{bad} rows break a variant rule")


def values_are_possible(con, year):
    bad = _scalar(
        con,
        "SELECT COUNT(*) FROM year_in_review WHERE card_variant <> 'quiet' AND ("
        "total_spent_minor <= 0 OR "
        "active_days < 1 OR active_days > payments_made OR active_days > 366 OR "
        "longest_streak_days < 1 OR longest_streak_days > active_days OR "
        "busiest_month NOT BETWEEN 1 AND 12 OR busiest_month_payments > payments_made OR "
        "biggest_payment_minor > total_spent_minor OR "
        "COALESCE(top_category_minor, 0) > total_spent_minor OR "
        "COALESCE(favourite_merchant_visits, 0) > payments_made OR "
        "people_paid > p2p_payments OR "
        "night_payments > payments_made OR bill_payments + p2p_payments > payments_made OR "
        "first_payment_date < signup_date OR substr(first_payment_date, 1, 4) <> :year OR "
        "biggest_payment_date < first_payment_date)",
        {"year": str(year)},
    )
    return Check("values_are_possible", bad == 0, f"{bad} rows hold an impossible value")


def matches_slow_recount(con, year, sample_size, seed=11):
    """Recount a sample of customers the slow way and compare every field.

    The sample always includes customers of every variant, because a random
    draw would be mostly 'full' cards and the edge cases are where bugs live.
    """
    rng = random.Random(seed)
    chosen = []
    for (variant,) in con.execute("SELECT DISTINCT card_variant FROM year_in_review").fetchall():
        ids = [r[0] for r in con.execute(
            "SELECT customer_id FROM year_in_review WHERE card_variant = ? ORDER BY customer_id", (variant,)
        )]
        chosen += rng.sample(ids, min(len(ids), max(sample_size // 4, 1)))

    mismatches = []
    for customer_id in chosen:
        fast = con.execute("SELECT * FROM year_in_review WHERE customer_id = ?", (customer_id,)).fetchone()
        expected = slow.recount(con, customer_id, year)
        for column in COMPARED:
            if fast[column] != expected.get(column):
                mismatches.append(f"customer {customer_id} {column}: table={fast[column]!r} recount={expected.get(column)!r}")
    detail = f"{len(chosen)} customers recounted, {len(mismatches)} differences"
    if mismatches:
        detail += ": " + "; ".join(mismatches[:5])
    return Check("matches_slow_recount", not mismatches, detail)


def every_card_renders(con, year):
    names = dict(con.execute("SELECT merchant_id, merchant_name FROM src.merchants").fetchall())
    problems = 0
    total = 0
    for row in con.execute("SELECT * FROM year_in_review"):
        total += 1
        try:
            card = build_card(row, year, names)
        except Exception:  # noqa: BLE001 - any failure here is a card a customer wouldn't get
            problems += 1
            continue
        text = repr(card)
        if "None" in text or "nan" in text.lower():
            problems += 1
    return Check("every_card_renders_cleanly", problems == 0, f"{total} cards built, {problems} with a problem")


def run_all(con, year, sample_size=200):
    return [
        one_card_each(con, year),
        no_test_accounts(con),
        totals_reconcile(con, year),
        quiet_cards_are_empty(con),
        variants_follow_the_rules(con, year),
        values_are_possible(con, year),
        matches_slow_recount(con, year, sample_size),
        every_card_renders(con, year),
    ]
