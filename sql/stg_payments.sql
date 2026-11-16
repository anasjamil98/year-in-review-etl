-- Every payment a real customer made in the review year, with local date parts.
-- Materialised once as a temp table so each metric below reads the same rows.
--
-- "Payment" means money the customer chose to send: transfers to people,
-- merchant payments, card payments and bill payments. Cash in and cash out
-- are topping up and withdrawing, and nobody wants those in their year in review.
--
-- The time range is bounded on the raw UTC column (the indexed one). The
-- boundaries are shifted to local time in Python and passed in.

CREATE TEMP TABLE stg_payments AS
SELECT
    t.txn_id,
    t.customer_id,
    date(t.txn_ts, '+3 hours')                             AS local_date,
    CAST(strftime('%m', t.txn_ts, '+3 hours') AS INTEGER)  AS local_month,
    CAST(strftime('%H', t.txn_ts, '+3 hours') AS INTEGER)  AS local_hour,
    t.txn_type,
    t.counterparty_customer_id,
    t.merchant_id,
    t.amount_minor
FROM src.transactions AS t
JOIN src.customers    AS c ON c.customer_id = t.customer_id
WHERE t.txn_ts >= :year_start_utc
  AND t.txn_ts <  :year_end_utc
  AND t.status = 'success'
  AND t.txn_type IN ('p2p_transfer', 'merchant_payment', 'card_payment', 'bill_payment')
  AND c.is_test_account = 0;
