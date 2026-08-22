#!/usr/bin/env python3
import argparse
import sqlite3
import time
from pathlib import Path

from server import COMPANY_UNIVERSE_LIMIT, SOURCE_URL, fetch_top_market_cap_companies


ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = ROOT / "companies.db"


def connect(db_path):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def create_schema(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS companies (
            ticker TEXT PRIMARY KEY,
            rank INTEGER NOT NULL,
            name TEXT NOT NULL,
            market_cap TEXT NOT NULL,
            market_cap_value INTEGER NOT NULL,
            price TEXT NOT NULL,
            today TEXT NOT NULL,
            country TEXT NOT NULL,
            logo TEXT NOT NULL,
            source_url TEXT NOT NULL,
            fetched_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_companies_rank ON companies(rank);
        CREATE INDEX IF NOT EXISTS idx_companies_market_cap_value ON companies(market_cap_value DESC);

        CREATE TABLE IF NOT EXISTS fetch_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_url TEXT NOT NULL,
            company_count INTEGER NOT NULL,
            fetched_at INTEGER NOT NULL
        );
        """
    )
    connection.commit()


def upsert_companies(connection, companies, fetched_at, prune=False):
    rows = [
        (
            company["ticker"],
            company["rank"],
            company["name"],
            company["marketCap"],
            company["marketCapValue"],
            company["price"],
            company["today"],
            company["country"],
            company["logo"],
            SOURCE_URL,
            fetched_at,
            fetched_at,
        )
        for company in companies
    ]

    with connection:
        connection.executemany(
            """
            INSERT INTO companies (
                ticker, rank, name, market_cap, market_cap_value, price, today,
                country, logo, source_url, fetched_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                rank = excluded.rank,
                name = excluded.name,
                market_cap = excluded.market_cap,
                market_cap_value = excluded.market_cap_value,
                price = excluded.price,
                today = excluded.today,
                country = excluded.country,
                logo = excluded.logo,
                source_url = excluded.source_url,
                fetched_at = excluded.fetched_at,
                updated_at = excluded.updated_at
            """,
            rows,
        )
        connection.execute(
            """
            INSERT INTO fetch_runs (source_url, company_count, fetched_at)
            VALUES (?, ?, ?)
            """,
            (SOURCE_URL, len(companies), fetched_at),
        )
        pruned_count = 0
        if prune:
            cursor = connection.execute(
                "DELETE FROM companies WHERE fetched_at <> ?",
                (fetched_at,),
            )
            pruned_count = cursor.rowcount
    return pruned_count


def sync_us_stock_list(connection, fetched_at, list_name="All US Stocks"):
    required_tables = {"stock_lists", "stock_list_members"}
    existing_tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (?, ?)",
            tuple(required_tables),
        )
    }
    if existing_tables != required_tables:
        return None

    now = int(time.time())
    stock_list = connection.execute(
        "SELECT id FROM stock_lists WHERE name = ? COLLATE NOCASE AND deleted_at IS NULL",
        (list_name,),
    ).fetchone()
    with connection:
        if stock_list:
            list_id = stock_list["id"]
            connection.execute(
                "UPDATE stock_lists SET updated_at = ? WHERE id = ?",
                (now, list_id),
            )
            connection.execute(
                "DELETE FROM stock_list_members WHERE list_id = ?",
                (list_id,),
            )
        else:
            cursor = connection.execute(
                "INSERT INTO stock_lists (name, created_at, updated_at) VALUES (?, ?, ?)",
                (list_name, now, now),
            )
            list_id = cursor.lastrowid

        tickers = [
            row["ticker"]
            for row in connection.execute(
                """
                SELECT ticker
                FROM companies
                WHERE fetched_at = ? AND country = 'USA'
                ORDER BY rank
                """,
                (fetched_at,),
            )
        ]
        connection.executemany(
            "INSERT INTO stock_list_members (list_id, ticker, position) VALUES (?, ?, ?)",
            [(list_id, ticker, position) for position, ticker in enumerate(tickers)],
        )
    return {"list_id": list_id, "company_count": len(tickers)}


def fetch_top_companies(limit):
    def show_progress(page, company_count, last_rank):
        print(
            f"Fetched page {page}: {company_count:,} unique companies "
            f"through rank {last_rank:,}",
            flush=True,
        )

    return fetch_top_market_cap_companies(limit, progress_callback=show_progress)


def main():
    parser = argparse.ArgumentParser(
        description="Fetch top companies from CompaniesMarketCap.com into SQLite."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite database path.")
    parser.add_argument(
        "--limit",
        type=int,
        default=COMPANY_UNIVERSE_LIMIT,
        help="Number of companies to fetch.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Fetch every available CompaniesMarketCap page instead of stopping at --limit.",
    )
    args = parser.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    fetched_at = int(time.time())
    if args.limit < 1:
        parser.error("--limit must be at least 1")
    companies = fetch_top_companies(None if args.all else args.limit)

    with connect(db_path) as connection:
        create_schema(connection)
        pruned_count = upsert_companies(connection, companies, fetched_at, prune=args.all)
        us_list = sync_us_stock_list(connection, fetched_at)

    print(f"Stored {len(companies)} companies in {db_path}")
    if args.all:
        print(f"Removed {pruned_count} companies no longer present in the complete listing")
    if us_list:
        print(f"Updated All US Stocks with {us_list['company_count']:,} companies")
    print(f"Source: {SOURCE_URL}")


if __name__ == "__main__":
    main()
