"""Synthetic data for Zaffre Wallet, a fictional digital wallet.

Everything here is invented: the company, the country (Meridia), the currency
(MRD), the cities, the merchants and every customer. The schema was designed
for these demos and is not modelled on any real system.

The data is built to contain the things that make payments data awkward:

* timestamps are stored in UTC but the business reports in local time (UTC+3),
  so late-evening transactions fall on a different date and sometimes a
  different month depending on which clock you read
* amounts are stored in minor units (1 MRD = 100 minor units)
* failed and reversed transactions sit in the same table as successful ones
* internal test accounts transact like real customers and have to be excluded
* some customers signed up late in the year, some stopped using the product,
  and some never transacted at all
* one day (17 Sep 2025) has an outage with a spike in failures

Usage:
    python data/generate.py                      # full size, ~190k transactions
    python data/generate.py --customers 400      # small, for tests
"""

import argparse
import sqlite3
from pathlib import Path

import numpy as np

SEED = 42
YEAR = 2025
UTC_OFFSET_HOURS = 3
CURRENCY = "MRD"
OUTAGE_DATE = "2025-09-17"

CITIES = ["Port Azure", "Saffron Bay", "Highmere", "Castellan", "Dunmore", "Riverton"]
CITY_WEIGHTS = [0.34, 0.22, 0.16, 0.12, 0.09, 0.07]

TXN_TYPES = ["p2p_transfer", "merchant_payment", "card_payment", "bill_payment", "cash_in", "cash_out"]
TYPE_WEIGHTS = np.array([0.30, 0.22, 0.18, 0.12, 0.13, 0.05])

# median amount in MRD, lognormal sigma
AMOUNT_PARAMS = {
    "p2p_transfer": (40.0, 0.9),
    "merchant_payment": (18.0, 0.8),
    "card_payment": (30.0, 0.9),
    "bill_payment": (55.0, 0.5),
    "cash_in": (120.0, 0.7),
    "cash_out": (90.0, 0.6),
}

RETAIL_CATEGORIES = ["groceries", "dining", "fuel", "fashion", "electronics", "travel", "health", "entertainment"]
BILLER_CATEGORIES = ["utilities", "telecom", "education"]

NAME_PREFIXES = [
    "Azure", "Saffron", "Highmere", "Castellan", "Dunmore", "Riverton", "Harbour", "Copper",
    "Juniper", "Lantern", "Meridian", "Orchard", "Pebble", "Quill", "Sable", "Tamarind",
    "Umber", "Verdant", "Willow", "Zephyr",
]
NAME_SUFFIXES = {
    "groceries": ["Grocers", "Market", "Pantry"],
    "dining": ["Kitchen", "Cafe", "Grill"],
    "fuel": ["Fuel", "Petroleum"],
    "fashion": ["Outfitters", "Wardrobe"],
    "electronics": ["Electronics", "Gadgets"],
    "travel": ["Travel", "Airways"],
    "health": ["Pharmacy", "Clinic"],
    "entertainment": ["Cinemas", "Arcade"],
    "utilities": ["Power", "Water", "Gas"],
    "telecom": ["Mobile", "Broadband"],
    "education": ["Academy", "College"],
}

# relative volume by local hour of day
HOUR_WEIGHTS = np.array(
    [1.2, 0.7, 0.4, 0.3, 0.3, 0.5, 1.0, 2.0, 3.2, 4.0, 4.4, 4.8,
     5.4, 5.2, 4.6, 4.4, 4.6, 5.2, 6.0, 6.4, 5.8, 4.6, 3.2, 2.0]
)

SCHEMA = """
CREATE TABLE customers (
    customer_id      INTEGER PRIMARY KEY,
    signup_date      TEXT    NOT NULL,   -- local calendar date, YYYY-MM-DD
    city             TEXT    NOT NULL,
    segment          TEXT    NOT NULL,   -- standard | premium
    is_test_account  INTEGER NOT NULL    -- 1 = internal test account
);

CREATE TABLE merchants (
    merchant_id      INTEGER PRIMARY KEY,
    merchant_name    TEXT    NOT NULL,
    category         TEXT    NOT NULL,
    is_biller        INTEGER NOT NULL
);

CREATE TABLE transactions (
    txn_id                    INTEGER PRIMARY KEY,
    txn_ts                    TEXT    NOT NULL,  -- UTC, YYYY-MM-DD HH:MM:SS
    customer_id               INTEGER NOT NULL REFERENCES customers(customer_id),
    counterparty_customer_id  INTEGER,           -- p2p transfers only
    merchant_id               INTEGER REFERENCES merchants(merchant_id),
    txn_type                  TEXT    NOT NULL,
    channel                   TEXT    NOT NULL,  -- app | qr | card
    amount_minor              INTEGER NOT NULL,  -- 1 MRD = 100 minor units
    fee_minor                 INTEGER NOT NULL,
    currency                  TEXT    NOT NULL,
    status                    TEXT    NOT NULL   -- success | failed | reversed
);

CREATE INDEX idx_txn_ts ON transactions (txn_ts);
CREATE INDEX idx_txn_customer ON transactions (customer_id);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _day_weights():
    """Relative volume for each local day of the year."""
    days = np.arange(np.datetime64(f"{YEAR}-01-01"), np.datetime64(f"{YEAR + 1}-01-01"))
    n = len(days)
    growth = 1.0 + 0.6 * np.arange(n) / (n - 1)
    weekday = (days.astype("datetime64[D]").view("int64") + 3) % 7  # 0 = Monday
    weekday_factor = np.array([1.0, 0.97, 0.98, 1.02, 1.15, 1.10, 0.90])[weekday]
    day_of_month = (days - days.astype("datetime64[M]")).astype(int) + 1
    payday = np.where((day_of_month <= 3) | (day_of_month >= 25), 1.2, 1.0)
    return days, growth * weekday_factor * payday


def _make_merchants(rng):
    rows = []
    used = set()
    merchant_id = 1
    plan = [(c, 14) for c in RETAIL_CATEGORIES] + [(c, 6) for c in BILLER_CATEGORIES]
    for category, count in plan:
        made = 0
        while made < count:
            name = f"{rng.choice(NAME_PREFIXES)} {rng.choice(NAME_SUFFIXES[category])}"
            if name in used:
                continue
            used.add(name)
            rows.append((merchant_id, name, category, int(category in BILLER_CATEGORIES)))
            merchant_id += 1
            made += 1
    return rows


def build(db_path, n_customers=4000, seed=SEED):
    """Create the SQLite database at db_path. Returns the transaction count."""
    rng = np.random.default_rng(seed)
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    days, day_w = _day_weights()
    n_days = len(days)
    cum_w = np.concatenate([[0.0], np.cumsum(day_w)])
    year_start = np.datetime64(f"{YEAR}-01-01")

    # ---- customers -------------------------------------------------------
    cust_ids = np.arange(1, n_customers + 1)
    signed_up_this_year = rng.random(n_customers) < 0.30
    prior_days = (year_start - np.datetime64("2023-01-01")).astype(int)
    signup_offset = np.where(
        signed_up_this_year,
        rng.integers(0, n_days, n_customers),
        -rng.integers(1, prior_days + 1, n_customers),
    )
    signup_date = year_start + signup_offset
    city = rng.choice(CITIES, n_customers, p=CITY_WEIGHTS)
    segment = np.where(rng.random(n_customers) < 0.15, "premium", "standard")
    is_test = (rng.random(n_customers) < 0.015).astype(int)

    never_transacts = rng.random(n_customers) < 0.12
    annual_rate = np.minimum(rng.lognormal(np.log(45.0), 0.9, n_customers), 600.0)
    annual_rate[never_transacts] = 0.0

    first_day = np.maximum(signup_offset, 0)
    churned = (rng.random(n_customers) < 0.15) & ~never_transacts
    last_day = np.full(n_customers, n_days - 1)
    span = np.maximum(n_days - 1 - first_day, 1)
    churn_day = first_day + (rng.random(n_customers) * span).astype(int)
    last_day[churned] = churn_day[churned]

    window_share = (cum_w[last_day + 1] - cum_w[first_day]) / cum_w[-1]
    n_txn = rng.poisson(annual_rate * window_share)

    # ---- transactions ----------------------------------------------------
    total = int(n_txn.sum())
    cust_idx = np.repeat(np.arange(n_customers), n_txn)

    # local day, drawn from each customer's own active window
    u = rng.random(total)
    target = cum_w[first_day[cust_idx]] + u * (cum_w[last_day[cust_idx] + 1] - cum_w[first_day[cust_idx]])
    day = np.clip(np.searchsorted(cum_w, target, side="right") - 1, 0, n_days - 1)

    hour = rng.choice(24, total, p=HOUR_WEIGHTS / HOUR_WEIGHTS.sum())
    second_of_day = hour * 3600 + rng.integers(0, 3600, total)
    local_ts = (
        year_start.astype("datetime64[s]")
        + day.astype("timedelta64[D]").astype("timedelta64[s]")
        + second_of_day.astype("timedelta64[s]")
    )
    utc_ts = local_ts - np.timedelta64(UTC_OFFSET_HOURS, "h")

    # each customer has their own mix of transaction types
    cust_type_p = rng.dirichlet(TYPE_WEIGHTS * 8.0, n_customers)
    cum_p = np.cumsum(cust_type_p[cust_idx], axis=1)
    type_idx = np.minimum((rng.random(total)[:, None] > cum_p).sum(axis=1), len(TXN_TYPES) - 1)
    txn_type = np.array(TXN_TYPES)[type_idx]

    merchants = _make_merchants(rng)
    retail_ids = np.array([m[0] for m in merchants if not m[3]])
    biller_ids = np.array([m[0] for m in merchants if m[3]])
    retail_p = 1.0 / np.arange(1, len(retail_ids) + 1) ** 0.8
    biller_p = 1.0 / np.arange(1, len(biller_ids) + 1) ** 0.6
    rng.shuffle(retail_p)
    rng.shuffle(biller_p)

    merchant_id = np.zeros(total, dtype=np.int64)
    is_retail = np.isin(txn_type, ["merchant_payment", "card_payment"])
    is_bill = txn_type == "bill_payment"
    merchant_id[is_retail] = rng.choice(retail_ids, int(is_retail.sum()), p=retail_p / retail_p.sum())
    merchant_id[is_bill] = rng.choice(biller_ids, int(is_bill.sum()), p=biller_p / biller_p.sum())

    is_p2p = txn_type == "p2p_transfer"
    counterparty = np.zeros(total, dtype=np.int64)
    other = rng.integers(0, n_customers, total)
    other = np.where(other == cust_idx, (other + 1) % n_customers, other)
    counterparty[is_p2p] = cust_ids[other[is_p2p]]

    channel = np.full(total, "app", dtype=object)
    channel[txn_type == "card_payment"] = "card"
    qr = (txn_type == "merchant_payment") & (rng.random(total) < 0.6)
    channel[qr] = "qr"

    amount = np.zeros(total)
    for name, (median, sigma) in AMOUNT_PARAMS.items():
        mask = txn_type == name
        amount[mask] = rng.lognormal(np.log(median), sigma, int(mask.sum()))
    amount_minor = np.maximum(np.round(amount * 100).astype(np.int64), 100)

    outage_day = int((np.datetime64(OUTAGE_DATE) - year_start).astype(int))
    fail_p = np.where(day == outage_day, 0.30, 0.05)
    r = rng.random(total)
    status = np.where(r < fail_p, "failed", np.where(r < fail_p + 0.015, "reversed", "success"))

    fee_minor = np.zeros(total, dtype=np.int64)
    fee_minor[is_bill] = 50
    cash_out = txn_type == "cash_out"
    fee_minor[cash_out] = np.round(amount_minor[cash_out] * 0.01).astype(np.int64)
    fee_minor[status != "success"] = 0

    order = np.argsort(utc_ts, kind="stable")
    ts_text = np.char.replace(np.datetime_as_string(utc_ts[order], unit="s"), "T", " ")

    txn_rows = [
        (
            i + 1,
            str(ts_text[i]),
            int(cust_ids[cust_idx[j]]),
            int(counterparty[j]) or None,
            int(merchant_id[j]) or None,
            str(txn_type[j]),
            str(channel[j]),
            int(amount_minor[j]),
            int(fee_minor[j]),
            CURRENCY,
            str(status[j]),
        )
        for i, j in enumerate(order)
    ]
    cust_rows = [
        (int(cust_ids[i]), str(signup_date[i]), str(city[i]), str(segment[i]), int(is_test[i]))
        for i in range(n_customers)
    ]

    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(SCHEMA)
        con.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", cust_rows)
        con.executemany("INSERT INTO merchants VALUES (?,?,?,?)", merchants)
        con.executemany("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?,?)", txn_rows)
        con.executemany(
            "INSERT INTO meta VALUES (?,?)",
            [
                ("company", "Zaffre Wallet (fictional)"),
                ("seed", str(seed)),
                ("customers", str(n_customers)),
                ("year", str(YEAR)),
                ("utc_offset_hours", str(UTC_OFFSET_HOURS)),
                ("currency", CURRENCY),
                ("outage_date", OUTAGE_DATE),
            ],
        )
        con.commit()
    finally:
        con.close()
    return total


def main():
    parser = argparse.ArgumentParser(description="Generate the synthetic Zaffre Wallet database.")
    parser.add_argument("--db", default=str(Path(__file__).parent / "zaffre.db"))
    parser.add_argument("--customers", type=int, default=4000)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    total = build(args.db, args.customers, args.seed)
    print(f"Wrote {args.db}: {args.customers} customers, {total} transactions")


if __name__ == "__main__":
    main()
