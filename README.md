# year-in-review-etl

Turns a year of transactions into one personalised summary per customer, and decides what to do about the customers a summary doesn't fit.

## The problem

A year-in-review is the one piece of analytics that goes to every customer at the same moment, and every one of them is an expert on their own numbers. If your dashboard is slightly off, somebody raises it in a meeting. If a customer's "favourite merchant" is a shop they've never heard of, they post a screenshot.

The averages aren't where it goes wrong. It goes wrong at the edges:

- The customer who signed up 3 weeks ago and is told about "their year".
- The customer who made 1 payment and gets a "favourite merchant" and a "1-day streak".
- The customer who did nothing all year and receives a card full of zeros.
- 2 merchants tied for first place, so the answer changes between runs.
- The payment at 23:40 on 31 December that's stored on 1 January, because the database is in UTC and the customer isn't.

At work I led the data architecture and ETL for a personalised year-in-review campaign, including the edge cases so new and inactive users got an accurate one too. This repo isn't that work. I invented the metrics, rules and data here for the demo. What carries over is the approach.

## What it does

```
transactions (raw, UTC)
      |
      v
stg_payments          one year, local dates, successful payments by real customers
      |
      v
year_in_review        exactly one row per eligible customer, 18 metrics, a card variant
      |
      v
validation            8 checks, including an independent recount
      |
      +--> any FAIL: no cards are written
      |
      v
cards_2025.jsonl      what each customer would see
```

On the full synthetic dataset, 4,000 customers and 194,707 transactions, the build takes about 4 seconds:

```
year_in_review 2025: 3058 full, 191 light, 125 newcomer, 553 quiet
  PASS  one_card_per_eligible_customer: 3927 rows, 3927 distinct, 3927 eligible
  PASS  no_test_accounts: 0 test accounts have a card
  PASS  totals_reconcile_to_source: cards (146477, 698758473); source (146477, 698758473)
  PASS  quiet_cards_carry_no_numbers: 0 quiet cards have a figure on them
  PASS  variants_follow_the_rules: 0 rows break a variant rule
  PASS  values_are_possible: 0 rows hold an impossible value
  PASS  matches_slow_recount: 200 customers recounted, 0 differences
  PASS  every_card_renders_cleanly: 3927 cards built, 0 with a problem
Wrote 3927 cards to output\cards_2025.jsonl
```

That's 3,927 cards from 146,477 payments worth MRD 6,987,584.73, and the total agrees with the source to the last minor unit. The 73 test accounts get nothing.

## Four cards, not one

Of those 3,927 customers, 869 don't fit the standard card. That's 22%, more than 1 in 5. Each group gets a card built for it.

| Variant | Who | How many | What they see |
|---|---|---|---|
| `full` | 5 or more payments, joined more than 60 days before year end | 3,058 | Everything: totals, biggest payment, busiest month, favourite place, top category, streak, a one-word persona |
| `light` | 1 to 4 payments | 191 | Totals and biggest payment only. No rankings, no streak, no persona |
| `newcomer` | Joined in the last 60 days of the year | 125 | A welcome, and what they've done since joining. Nothing about "the year" |
| `quiet` | No successful payments | 553 | A short friendly card with no figures at all |

I held everything to one rule: **never show a number that would embarrass us.** Even on a full card, a favourite place needs at least 3 visits, a streak needs at least 3 days, and "people you paid" needs at least 2 people. Below that, I leave the tile off.

A quiet customer's 18 metrics are stored as NULL, not 0, and check 4 of the 8 enforces it. Zero is a claim. NULL is the absence of one.

You can see all 4 variants in [`docs/sample_cards_2025.json`](docs/sample_cards_2025.json).

## Run it

You need Python 3.9 or later.

```bash
pip install -r requirements.txt
```

```bash
python data/generate.py
```

```bash
python -m review build
```

```bash
python -m review show --customer 1
```

```bash
python -m pytest
```

Customer 1 gets a full card:

```
Your 2025 in review
You're a: Generous friend
  Payments made             25 payments
  Total paid                MRD 1,508.53
  Biggest payment           MRD 169.42  (2025-12-12)
  Busiest month             August  (4 payments)
  Top category              Education  (17% of what you paid)
  People you paid           15
  Days you used your wallet 24
```

No favourite place and no streak appear, because this customer never visited one merchant 3 times or paid on 3 days running. Customer 3 did nothing in 2025 and gets a 2-line card with nothing counted on it.

## Two implementations that share nothing

I compute every metric twice.

[`sql/year_in_review.sql`](sql/year_in_review.sql) is the fast one: window functions, a gaps-and-islands query for streaks, one pass for all 3,927 customers.

[`review/slow.py`](review/slow.py) is the slow one. It reads 1 customer's raw rows and counts things in plain Python loops. No SQL aggregation, no shared helpers. It's much too slow to run for everyone, and that doesn't matter. Its only job is to disagree with the fast version when the fast version is wrong.

Every build recounts 200 customers, 50 from each variant. A purely random 200 would be mostly full cards, and the bugs live in the other 3. My test suite uses a smaller dataset of 700 customers and recounts every one of them.

The only thing the 2 share is the tie-break rules, written out in both:

| Metric | Tie-break |
|---|---|
| Biggest payment | Earlier transaction |
| Busiest month | Higher value, then the earlier month |
| Favourite merchant | Higher value, then lower merchant id |
| Top category | Alphabetical |

Without those, "favourite merchant" for a customer with 2 equal candidates depends on how the database felt that day. Both implementations could be right and still disagree.

## Decisions worth explaining

**"Payment" excludes cash in and cash out.** Topping up and withdrawing aren't things you want celebrated. A customer who only did those gets a quiet card.

**Failed attempts don't count.** A customer who tried to pay all year and failed every time hasn't had a year worth reviewing. One of my 16 tests turns a customer's whole history to failed and checks they end up quiet.

**The year is a local year.** I shift the boundaries to UTC in Python (2025 starts at 21:00 on 31 December 2024) and apply them to the raw indexed timestamp. The filter stays cheap and the 23:40 payment lands in the right year. Another test builds a 7-row dataset where a late-night transaction lands on the next local day and stretches a 2-day streak to 3.

**Validation gates the output.** If any of the 8 checks fails, no card file is written. 3 tests corrupt the table (a wrong total, a duplicated customer, a figure on a quiet card) and confirm each is caught.

**Totals reconcile to the source.** The sum of every customer's payments has to equal 1 independent query on the raw table. It catches customers dropped by a join, which a per-customer check never would.

## The messy part

- **The thresholds are judgment calls.** 5 payments for a full card, 60 days for a newcomer, 3 visits for a favourite. I can defend them, but they came from looking at this data. Real ones should come from looking at real cards with whoever owns the campaign.
- **Personas are first-match-wins.** There are 6. A customer who's both a night owl and a bill tamer is a night owl, because I listed that rule first. Simple and reproducible, but it means the order of the rules is a product decision hiding in a CASE statement. It's also uneven: 1,080 explorers and only 11 night owls.
- **It's a full rebuild.** Fine for a job that runs once a year. Run nightly in the weeks before launch for previews, it'd want to be incremental.
- **No previous-year comparison.** "Up 20% on last year" is the tile everyone asks for next, and it brings its own edge cases: about 1,200 of these customers joined during 2025 and have no last year.

## About the data

Every row is generated by [`data/generate.py`](data/generate.py) from a fixed seed. Zaffre Wallet and all of its customers and merchants are invented. No code, metric definition or rule here comes from an employer's systems.

## Licence

MIT
