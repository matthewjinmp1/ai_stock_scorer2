#!/usr/bin/env python3
import sqlite3
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent / "companies.db"


def main():
    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        count = connection.execute("SELECT COUNT(*) AS count FROM companies").fetchone()["count"]
        rows = connection.execute(
            """
            SELECT rank, name, ticker, market_cap, price, today, country
            FROM companies
            ORDER BY rank
            LIMIT 10
            """
        ).fetchall()

    print(f"companies: {count}")
    for row in rows:
        print(
            f"{row['rank']:>3}  {row['name']} ({row['ticker']})  "
            f"{row['market_cap']}  {row['price']}  {row['today']}  {row['country']}"
        )


if __name__ == "__main__":
    main()
