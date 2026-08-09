#!/usr/bin/env python3
import sqlite3
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent / "companies.db"


def main():
    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        latest_fetch = connection.execute("SELECT MAX(fetched_at) AS fetched_at FROM companies").fetchone()["fetched_at"]
        count = connection.execute(
            "SELECT COUNT(*) AS count FROM companies WHERE fetched_at = ?",
            (latest_fetch,),
        ).fetchone()["count"]
        rows = connection.execute(
            """
            SELECT rank, name, ticker, market_cap, price, today, country
            FROM companies
            WHERE fetched_at = ?
            ORDER BY rank
            LIMIT 10
            """,
            (latest_fetch,),
        ).fetchall()

    print(f"active companies: {count}")
    for row in rows:
        print(
            f"{row['rank']:>3}  {row['name']} ({row['ticker']})  "
            f"{row['market_cap']}  {row['price']}  {row['today']}  {row['country']}"
        )


if __name__ == "__main__":
    main()
