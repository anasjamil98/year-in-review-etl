"""Command line: python -m review {build,show}"""

import argparse
import json
import sys
from pathlib import Path

from . import VARIANTS, build, validate
from .cards import build_card, render_text

OUTPUT_DIR = build.ROOT / "output"


def cmd_build(args):
    con = build.build(args.year)
    try:
        counts = dict(con.execute("SELECT card_variant, COUNT(*) FROM year_in_review GROUP BY card_variant").fetchall())
        print(f"year_in_review {args.year}: " + ", ".join(f"{counts.get(v, 0)} {v}" for v in VARIANTS))

        checks = validate.run_all(con, args.year, args.sample)
        for check in checks:
            print(f"  {'PASS' if check.passed else 'FAIL'}  {check.name}: {check.detail}")
        if not all(check.passed for check in checks):
            print("Validation failed. No cards written.")
            return 1

        names = dict(con.execute("SELECT merchant_id, merchant_name FROM src.merchants").fetchall())
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        cards_path = OUTPUT_DIR / f"cards_{args.year}.jsonl"
        samples = {}
        written = 0
        with open(str(cards_path), "w", encoding="utf-8", newline="\n") as handle:
            for row in con.execute("SELECT * FROM year_in_review ORDER BY customer_id"):
                card = build_card(row, args.year, names)
                handle.write(json.dumps(card) + "\n")
                samples.setdefault(card["variant"], card)
                written += 1
        samples_path = OUTPUT_DIR / f"sample_cards_{args.year}.json"
        with open(str(samples_path), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps([samples[v] for v in VARIANTS if v in samples], indent=2) + "\n")
        print(f"Wrote {written} cards to {cards_path.relative_to(build.ROOT)}")
        return 0
    finally:
        con.close()


def cmd_show(args):
    con = build.connect()
    try:
        row = con.execute("SELECT * FROM year_in_review WHERE customer_id = ?", (args.customer,)).fetchone()
        if row is None:
            print(f"No card for customer {args.customer}.", file=sys.stderr)
            return 1
        names = dict(con.execute("SELECT merchant_id, merchant_name FROM src.merchants").fetchall())
        print(render_text(build_card(row, args.year, names)))
        return 0
    finally:
        con.close()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m review")
    sub = parser.add_subparsers(dest="command", required=True)
    p_build = sub.add_parser("build", help="build the table, validate it, write the cards")
    p_build.add_argument("--year", type=int, default=2025)
    p_build.add_argument("--sample", type=int, default=200, help="customers to recount the slow way")
    p_show = sub.add_parser("show", help="print one customer's card")
    p_show.add_argument("--customer", type=int, required=True)
    p_show.add_argument("--year", type=int, default=2025)
    args = parser.parse_args(argv)

    if not Path(build.DEFAULT_SOURCE).exists():
        print("No source database. Run: python data/generate.py", file=sys.stderr)
        return 2
    return {"build": cmd_build, "show": cmd_show}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
