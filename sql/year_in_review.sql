-- year_in_review: exactly one row per eligible customer.
--
-- Eligible = a real customer who had signed up by the end of the year.
-- Customers with no payments still get a row (card_variant = 'quiet') with
-- every metric NULL. They are not left out, and they are not given zeros.
--
-- Every "top" metric has a full tie-break so the result is the same on every
-- run. The rules are written next to each one because the slow recount in
-- review/slow.py has to follow exactly the same ones.

CREATE TABLE year_in_review AS
WITH
eligible AS (
    SELECT customer_id, signup_date
    FROM src.customers
    WHERE is_test_account = 0
      AND signup_date < :year_end_local
),

totals AS (
    SELECT
        customer_id,
        COUNT(*)                                  AS payments_made,
        SUM(amount_minor)                         AS total_spent_minor,
        COUNT(DISTINCT local_date)                AS active_days,
        MIN(local_date)                           AS first_payment_date,
        COUNT(DISTINCT counterparty_customer_id)  AS people_paid,
        COUNT(DISTINCT merchant_id)               AS merchants_visited,
        SUM(local_hour >= 22 OR local_hour < 5)   AS night_payments,
        SUM(txn_type = 'bill_payment')            AS bill_payments,
        SUM(txn_type = 'p2p_transfer')            AS p2p_payments
    FROM stg_payments
    GROUP BY customer_id
),

-- Biggest single payment. Tie: the earlier transaction.
biggest AS (
    SELECT customer_id, amount_minor AS biggest_payment_minor, local_date AS biggest_payment_date
    FROM (
        SELECT customer_id, amount_minor, local_date,
               ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY amount_minor DESC, txn_id) AS rn
        FROM stg_payments
    )
    WHERE rn = 1
),

-- Busiest month by number of payments. Tie: higher value, then the earlier month.
busiest AS (
    SELECT customer_id, local_month AS busiest_month, payments AS busiest_month_payments
    FROM (
        SELECT customer_id, local_month, COUNT(*) AS payments,
               ROW_NUMBER() OVER (
                   PARTITION BY customer_id
                   ORDER BY COUNT(*) DESC, SUM(amount_minor) DESC, local_month
               ) AS rn
        FROM stg_payments
        GROUP BY customer_id, local_month
    )
    WHERE rn = 1
),

-- Favourite merchant by number of visits. Tie: higher value, then lower merchant id.
favourite AS (
    SELECT customer_id, merchant_id AS favourite_merchant_id, visits AS favourite_merchant_visits
    FROM (
        SELECT customer_id, merchant_id, COUNT(*) AS visits,
               ROW_NUMBER() OVER (
                   PARTITION BY customer_id
                   ORDER BY COUNT(*) DESC, SUM(amount_minor) DESC, merchant_id
               ) AS rn
        FROM stg_payments
        WHERE merchant_id IS NOT NULL
        GROUP BY customer_id, merchant_id
    )
    WHERE rn = 1
),

-- Top category by value. Tie: alphabetical.
top_category AS (
    SELECT customer_id, category AS top_category, category_minor AS top_category_minor
    FROM (
        SELECT p.customer_id, m.category, SUM(p.amount_minor) AS category_minor,
               ROW_NUMBER() OVER (
                   PARTITION BY p.customer_id
                   ORDER BY SUM(p.amount_minor) DESC, m.category
               ) AS rn
        FROM stg_payments AS p
        JOIN src.merchants AS m ON m.merchant_id = p.merchant_id
        GROUP BY p.customer_id, m.category
    )
    WHERE rn = 1
),

-- Longest run of consecutive days with at least one payment.
-- Gaps and islands: within a run, (date - row number) is constant.
payment_days AS (
    SELECT DISTINCT customer_id, local_date FROM stg_payments
),
islands AS (
    SELECT customer_id,
           julianday(local_date) - ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY local_date) AS island
    FROM payment_days
),
streaks AS (
    SELECT customer_id, MAX(run_length) AS longest_streak_days
    FROM (SELECT customer_id, island, COUNT(*) AS run_length FROM islands GROUP BY customer_id, island)
    GROUP BY customer_id
),

assembled AS (
    SELECT
        e.customer_id,
        e.signup_date,
        -- Which card this customer gets. Order matters: first match wins.
        CASE
            WHEN t.customer_id IS NULL             THEN 'quiet'     -- nothing to show; say so kindly
            WHEN e.signup_date >= :newcomer_cutoff THEN 'newcomer'  -- too new for "your year"
            WHEN t.payments_made < 5               THEN 'light'     -- too little for rankings to mean anything
            ELSE 'full'
        END AS card_variant,
        t.payments_made,
        t.total_spent_minor,
        t.active_days,
        t.first_payment_date,
        t.people_paid,
        t.merchants_visited,
        t.night_payments,
        t.bill_payments,
        t.p2p_payments,
        b.biggest_payment_minor,
        b.biggest_payment_date,
        m.busiest_month,
        m.busiest_month_payments,
        f.favourite_merchant_id,
        f.favourite_merchant_visits,
        c.top_category,
        c.top_category_minor,
        s.longest_streak_days
    FROM eligible AS e
    LEFT JOIN totals       AS t ON t.customer_id = e.customer_id
    LEFT JOIN biggest      AS b ON b.customer_id = e.customer_id
    LEFT JOIN busiest      AS m ON m.customer_id = e.customer_id
    LEFT JOIN favourite    AS f ON f.customer_id = e.customer_id
    LEFT JOIN top_category AS c ON c.customer_id = e.customer_id
    LEFT JOIN streaks      AS s ON s.customer_id = e.customer_id
)

SELECT
    assembled.*,
    -- A one-word summary, only for customers with enough history to earn one.
    -- First match wins.
    CASE
        WHEN card_variant <> 'full'                                   THEN NULL
        WHEN 1.0 * night_payments / payments_made >= 0.35             THEN 'night_owl'
        WHEN 1.0 * bill_payments  / payments_made >= 0.40             THEN 'bill_tamer'
        WHEN 1.0 * p2p_payments   / payments_made >= 0.50             THEN 'generous_friend'
        WHEN COALESCE(favourite_merchant_visits, 0) >= 10             THEN 'regular'
        WHEN merchants_visited >= 15                                  THEN 'explorer'
        ELSE 'all_rounder'
    END AS persona
FROM assembled
ORDER BY customer_id;
