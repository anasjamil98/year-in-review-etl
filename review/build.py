"""Builds the year_in_review table."""

import datetime as dt
import sqlite3
from pathlib import Path

from . import NEWCOMER_DAYS

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"
DEFAULT_SOURCE = ROOT / "data" / "zaffre.db"
DEFAULT_OUTPUT = ROOT / "data" / "review.db"
UTC_OFFSET_HOURS = 3


def year_params(year):
    """Boundaries for one review year, in the forms the SQL needs."""
    start, end = dt.datetime(year, 1, 1), dt.datetime(year + 1, 1, 1)
    offset = dt.timedelta(hours=UTC_OFFSET_HOURS)
    fmt = "%Y-%m-%d %H:%M:%S"
    return {
        "year_start_utc": (start - offset).strftime(fmt),
        "year_end_utc": (end - offset).strftime(fmt),
        "year_end_local": end.date().isoformat(),
        "newcomer_cutoff": (end.date() - dt.timedelta(days=NEWCOMER_DAYS)).isoformat(),
    }


def connect(source_db=DEFAULT_SOURCE, output_db=DEFAULT_OUTPUT):
    """Output database as main, source attached read-only as src."""
    Path(output_db).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(Path(output_db).resolve().as_uri(), uri=True)
    con.row_factory = sqlite3.Row
    con.execute("ATTACH DATABASE ? AS src", (Path(source_db).resolve().as_uri() + "?mode=ro",))
    return con


def _only(params, sql):
    return {key: value for key, value in params.items() if f":{key}" in sql}


def build(year, source_db=DEFAULT_SOURCE, output_db=DEFAULT_OUTPUT):
    """Full rebuild for one year. Returns an open connection with stg_payments still available."""
    params = year_params(year)
    con = connect(source_db, output_db)
    staging = (SQL_DIR / "stg_payments.sql").read_text(encoding="utf-8")
    main = (SQL_DIR / "year_in_review.sql").read_text(encoding="utf-8")
    con.execute(staging, _only(params, staging))
    con.execute("CREATE INDEX temp.idx_stg_customer ON stg_payments (customer_id)")
    con.execute("DROP TABLE IF EXISTS year_in_review")
    con.execute(main, _only(params, main))
    con.commit()
    return con
