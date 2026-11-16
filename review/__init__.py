"""Per-customer year-in-review metrics, with the edge cases handled on purpose."""

METRIC_COLUMNS = [
    "payments_made", "total_spent_minor", "active_days", "first_payment_date", "people_paid",
    "merchants_visited", "night_payments", "bill_payments", "p2p_payments",
    "biggest_payment_minor", "biggest_payment_date", "busiest_month", "busiest_month_payments",
    "favourite_merchant_id", "favourite_merchant_visits", "top_category", "top_category_minor",
    "longest_streak_days",
]

VARIANTS = ["full", "light", "newcomer", "quiet"]
FULL_CARD_MINIMUM_PAYMENTS = 5
NEWCOMER_DAYS = 60
PAYMENT_TYPES = ("p2p_transfer", "merchant_payment", "card_payment", "bill_payment")
