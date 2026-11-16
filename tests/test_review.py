import shutil
import sqlite3

import pytest

from review import METRIC_COLUMNS, VARIANTS, build, slow, validate
from review.cards import build_card, render_text

YEAR = 2025


def merchant_names(con):
    return dict(con.execute("SELECT merchant_id, merchant_name FROM src.merchants").fetchall())


def first_of(con, variant):
    return con.execute(
        "SELECT * FROM year_in_review WHERE card_variant = ? ORDER BY customer_id LIMIT 1", (variant,)
    ).fetchone()


# ---- boundaries ------------------------------------------------------------

def test_year_boundaries_are_shifted_to_utc():
    params = build.year_params(2025)
    assert params["year_start_utc"] == "2024-12-31 21:00:00"
    assert params["year_end_utc"] == "2025-12-31 21:00:00"
    assert params["newcomer_cutoff"] == "2025-11-02"


# ---- the validation suite passes on clean data ------------------------------

def test_validation_passes(con):
    failed = [c for c in validate.run_all(con, YEAR, sample_size=120) if not c.passed]
    assert failed == []


def test_all_four_variants_occur(con):
    found = {r[0] for r in con.execute("SELECT DISTINCT card_variant FROM year_in_review")}
    assert found == set(VARIANTS)


# ---- every customer, recounted the slow way ---------------------------------

def test_every_customer_matches_the_slow_recount(con):
    """Not a sample: the test dataset is small enough to recount everyone."""
    differences = []
    for fast in con.execute("SELECT * FROM year_in_review").fetchall():
        expected = slow.recount(con, fast["customer_id"], YEAR)
        for column in validate.COMPARED:
            if fast[column] != expected.get(column):
                differences.append((fast["customer_id"], column, fast[column], expected.get(column)))
    assert differences == []


# ---- edge cases, one at a time ----------------------------------------------

def test_quiet_customer_gets_a_card_with_no_figures(con):
    row = first_of(con, "quiet")
    assert all(row[column] is None for column in METRIC_COLUMNS)
    card = build_card(row, YEAR, merchant_names(con))
    assert card["tiles"] == []
    assert not any(ch.isdigit() for ch in card["message"])
    assert "0" not in render_text(card).replace(str(YEAR), "")


def test_customer_with_only_failed_payments_is_quiet(source, tmp_path):
    """Trying to pay and failing all year isn't a year worth reviewing."""
    db = tmp_path / "all_failed.db"
    shutil.copy(str(source), str(db))
    raw = sqlite3.connect(str(db))
    customer_id = raw.execute(
        "SELECT t.customer_id FROM transactions t JOIN customers c ON c.customer_id = t.customer_id "
        "WHERE c.is_test_account = 0 GROUP BY t.customer_id HAVING COUNT(*) >= 5 LIMIT 1"
    ).fetchone()[0]
    raw.execute("UPDATE transactions SET status = 'failed', fee_minor = 0 WHERE customer_id = ?", (customer_id,))
    raw.commit()
    raw.close()

    con = build.build(YEAR, db, tmp_path / "review.db")
    try:
        row = con.execute("SELECT * FROM year_in_review WHERE customer_id = ?", (customer_id,)).fetchone()
        assert row["card_variant"] == "quiet"
        assert all(c.passed for c in validate.run_all(con, YEAR, sample_size=40))
    finally:
        con.close()


def test_customer_who_only_topped_up_is_quiet(con):
    """Cash in and cash out aren't payments, so they don't earn a card."""
    row = con.execute(
        "SELECT y.card_variant FROM year_in_review y WHERE y.card_variant = 'quiet' AND EXISTS ("
        " SELECT 1 FROM src.transactions t WHERE t.customer_id = y.customer_id AND t.status = 'success'"
        " AND t.txn_type IN ('cash_in', 'cash_out')) LIMIT 1"
    ).fetchone()
    if row is None:
        pytest.skip("no such customer in this dataset")
    assert row["card_variant"] == "quiet"


def test_newcomer_card_makes_no_claims_about_the_year(con):
    card = build_card(first_of(con, "newcomer"), YEAR, merchant_names(con))
    labels = {tile["label"] for tile in card["tiles"]}
    assert labels == {"Since you joined", "Paid so far"}
    assert "persona" not in card


def test_light_card_hides_rankings_and_streaks(con):
    card = build_card(first_of(con, "light"), YEAR, merchant_names(con))
    labels = {tile["label"] for tile in card["tiles"]}
    assert labels == {"Payments made", "Total paid", "Biggest payment"}
    assert "persona" not in card


def test_single_payment_reads_as_singular(con):
    row = con.execute("SELECT * FROM year_in_review WHERE payments_made = 1 LIMIT 1").fetchone()
    if row is None:
        pytest.skip("no single-payment customer in this dataset")
    text = render_text(build_card(row, YEAR, merchant_names(con)))
    assert "1 payment" in text and "1 payments" not in text


def test_full_card_never_shows_a_weak_favourite_or_streak(con):
    names = merchant_names(con)
    for row in con.execute("SELECT * FROM year_in_review WHERE card_variant = 'full'").fetchall():
        labels = {tile["label"] for tile in build_card(row, YEAR, names)["tiles"]}
        if (row["favourite_merchant_visits"] or 0) < 3:
            assert "Favourite place" not in labels
        if row["longest_streak_days"] < 3:
            assert "Longest streak" not in labels
        if row["people_paid"] < 2:
            assert "People you paid" not in labels


def test_test_accounts_get_no_card(con):
    assert validate.no_test_accounts(con).passed
    test_accounts = con.execute("SELECT COUNT(*) FROM src.customers WHERE is_test_account = 1").fetchone()[0]
    assert test_accounts > 0  # the dataset does contain some, so the check means something


def test_streak_logic_on_a_known_pattern(tmp_path):
    """Days 1,2,3 then 5,6 then 10: longest streak is 3."""
    db = tmp_path / "tiny.db"
    raw = sqlite3.connect(str(db))
    raw.executescript(
        "CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, signup_date TEXT, city TEXT, segment TEXT, is_test_account INTEGER);"
        "CREATE TABLE merchants (merchant_id INTEGER PRIMARY KEY, merchant_name TEXT, category TEXT, is_biller INTEGER);"
        "CREATE TABLE transactions (txn_id INTEGER PRIMARY KEY, txn_ts TEXT, customer_id INTEGER, counterparty_customer_id INTEGER,"
        " merchant_id INTEGER, txn_type TEXT, channel TEXT, amount_minor INTEGER, fee_minor INTEGER, currency TEXT, status TEXT);"
        "INSERT INTO customers VALUES (1, '2024-01-01', 'Port Azure', 'standard', 0);"
        "INSERT INTO merchants VALUES (1, 'Azure Grocers', 'groceries', 0);"
    )
    for i, day in enumerate([1, 2, 3, 5, 6, 10], start=1):
        raw.execute(
            "INSERT INTO transactions VALUES (?, ?, 1, NULL, 1, 'merchant_payment', 'app', 1000, 0, 'MRD', 'success')",
            (i, f"2025-03-{day:02d} 09:00:00"),
        )
    # 23:30 local on the 6th is stored as 20:30 UTC, and 00:30 local on the 7th as 21:30 UTC on the 6th.
    raw.execute("INSERT INTO transactions VALUES (7, '2025-03-06 21:30:00', 1, NULL, 1, 'merchant_payment', 'app', 500, 0, 'MRD', 'success')")
    raw.commit()
    raw.close()

    con = build.build(YEAR, db, tmp_path / "review.db")
    try:
        row = con.execute("SELECT * FROM year_in_review WHERE customer_id = 1").fetchone()
        # The late transaction lands on the 7th local, joining 5,6 into 5,6,7.
        assert row["active_days"] == 7
        assert row["longest_streak_days"] == 3
        assert row["payments_made"] == 7
        assert row["card_variant"] == "full"
        assert slow.recount(con, 1, YEAR)["longest_streak_days"] == 3
    finally:
        con.close()


# ---- broken output is caught -------------------------------------------------

def test_validation_catches_a_wrong_total(source, tmp_path):
    con = build.build(YEAR, source, tmp_path / "review.db")
    try:
        con.execute(
            "UPDATE year_in_review SET total_spent_minor = total_spent_minor + 1 "
            "WHERE customer_id = (SELECT MIN(customer_id) FROM year_in_review WHERE card_variant = 'full')"
        )
        failed = {c.name for c in validate.run_all(con, YEAR, sample_size=40) if not c.passed}
        assert "totals_reconcile_to_source" in failed
    finally:
        con.close()


def test_validation_catches_a_duplicated_customer(source, tmp_path):
    con = build.build(YEAR, source, tmp_path / "review.db")
    try:
        con.execute("INSERT INTO year_in_review SELECT * FROM year_in_review LIMIT 1")
        failed = {c.name for c in validate.run_all(con, YEAR, sample_size=40) if not c.passed}
        assert "one_card_per_eligible_customer" in failed
    finally:
        con.close()


def test_validation_catches_a_figure_on_a_quiet_card(source, tmp_path):
    con = build.build(YEAR, source, tmp_path / "review.db")
    try:
        con.execute(
            "UPDATE year_in_review SET active_days = 0 "
            "WHERE customer_id = (SELECT MIN(customer_id) FROM year_in_review WHERE card_variant = 'quiet')"
        )
        failed = {c.name for c in validate.run_all(con, YEAR, sample_size=40) if not c.passed}
        assert "quiet_cards_carry_no_numbers" in failed
    finally:
        con.close()
