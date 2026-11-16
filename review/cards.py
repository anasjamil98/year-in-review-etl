"""Turns a year_in_review row into the card a customer would see.

The variant decides what's shown, and the rule is: never show a number that
would embarrass us. A favourite merchant visited once isn't a favourite. A
one-day streak isn't a streak. A customer with nothing gets a short friendly
card with no figures on it, not a list of zeros.
"""

import calendar

PERSONA_LABELS = {
    "night_owl": "Night owl",
    "bill_tamer": "Bill tamer",
    "generous_friend": "Generous friend",
    "regular": "Regular",
    "explorer": "Explorer",
    "all_rounder": "All-rounder",
}


def money(minor):
    return f"MRD {minor / 100:,.2f}"


def plural(count, word):
    return f"{count:,} {word}" if count == 1 else f"{count:,} {word}s"


def build_card(row, year, merchant_names):
    """row is a mapping from year_in_review. Returns a JSON-ready dict."""
    variant = row["card_variant"]
    card = {"customer_id": row["customer_id"], "year": year, "variant": variant, "tiles": []}
    tiles = card["tiles"]

    if variant == "quiet":
        card["headline"] = f"We didn't see much of you in {year}"
        card["message"] = "Your wallet's ready whenever you are."
        return card

    payments = plural(row["payments_made"], "payment")

    if variant == "newcomer":
        card["headline"] = "Welcome aboard"
        card["message"] = f"You joined us late in {year}. Here's your start."
        tiles.append({"label": "Since you joined", "value": payments})
        tiles.append({"label": "Paid so far", "value": money(row["total_spent_minor"])})
        return card

    tiles.append({"label": "Payments made", "value": payments})
    tiles.append({"label": "Total paid", "value": money(row["total_spent_minor"])})
    tiles.append(
        {"label": "Biggest payment", "value": money(row["biggest_payment_minor"]), "detail": row["biggest_payment_date"]}
    )

    if variant == "light":
        card["headline"] = f"Your {year}, in brief"
        return card

    # full card: rankings and streaks only appear when they mean something
    card["headline"] = f"Your {year} in review"
    card["persona"] = PERSONA_LABELS[row["persona"]]
    tiles.append(
        {
            "label": "Busiest month",
            "value": calendar.month_name[row["busiest_month"]],
            "detail": plural(row["busiest_month_payments"], "payment"),
        }
    )
    if row["favourite_merchant_id"] is not None and row["favourite_merchant_visits"] >= 3:
        tiles.append(
            {
                "label": "Favourite place",
                "value": merchant_names[row["favourite_merchant_id"]],
                "detail": plural(row["favourite_merchant_visits"], "visit"),
            }
        )
    if row["top_category"] is not None:
        share = row["top_category_minor"] / row["total_spent_minor"]
        tiles.append({"label": "Top category", "value": row["top_category"].title(), "detail": f"{share:.0%} of what you paid"})
    if row["longest_streak_days"] >= 3:
        tiles.append({"label": "Longest streak", "value": plural(row["longest_streak_days"], "day") + " in a row"})
    if row["people_paid"] >= 2:
        tiles.append({"label": "People you paid", "value": f"{row['people_paid']:,}"})
    tiles.append({"label": "Days you used your wallet", "value": f"{row['active_days']:,}"})
    return card


def render_text(card):
    lines = [card["headline"]]
    if "persona" in card:
        lines.append(f"You're a: {card['persona']}")
    if "message" in card:
        lines.append(card["message"])
    for tile in card["tiles"]:
        detail = f"  ({tile['detail']})" if "detail" in tile else ""
        lines.append(f"  {tile['label']:<26}{tile['value']}{detail}")
    return "\n".join(lines)
