#!/usr/bin/env python3
import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime

import server


MODEL = "deepseek/deepseek-v4-flash-0731"
REASONING_MODE = "none"
DEFAULT_MAX_TOKENS = 250
DEFAULT_WORKERS = 20
PROMPT = server.CONFIDENCE_SCORE_PROMPT
FINAL_STATUSES = {"completed", "failed", "stopped"}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Score all stored US companies for DeepSeek evaluation confidence."
    )
    parser.add_argument(
        "--resume",
        type=int,
        metavar="RUN_ID",
        help="Reconnect to an existing run and continue displaying its progress.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help="Concurrent OpenRouter requests (1-20, default: 20).",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_MAX_TOKENS,
        help="Maximum response tokens per company (default: 250).",
    )
    parser.add_argument(
        "--name",
        help="Saved run name. A timestamped name is used by default.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Start without asking for confirmation after showing the estimate.",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Start the detached worker and exit instead of tracking progress.",
    )
    return parser.parse_args()


def us_tickers():
    with server.db_connect() as connection:
        rows = connection.execute(
            """
            SELECT ticker
            FROM companies
            WHERE country = 'USA'
              AND fetched_at = (SELECT MAX(fetched_at) FROM companies)
            ORDER BY rank
            """
        ).fetchall()
    return [row["ticker"] for row in rows]


def run_snapshot(run_id):
    with server.db_connect() as connection:
        row = connection.execute(
            """
            SELECT id, name, status, company_count, completed_count, failed_count,
                   queue_count, created_at, started_at, finished_at, error
            FROM scoring_runs
            WHERE id = ? AND deleted_at IS NULL
            """,
            (run_id,),
        ).fetchone()
    return dict(row) if row else None


def format_duration(seconds):
    if seconds is None:
        return "--"
    seconds = max(0, int(round(seconds)))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def progress_line(run):
    total = int(run["company_count"] or 0)
    succeeded = int(run["completed_count"] or 0)
    failed = int(run["failed_count"] or 0)
    processed = succeeded + failed
    started_at = run["started_at"] or run["created_at"]
    elapsed = max(0, time.time() - started_at)
    rate = processed / elapsed if processed and elapsed else 0
    remaining = max(0, total - processed)
    eta = remaining / rate if rate else None
    percent = (processed / total * 100) if total else 0
    return (
        f"{processed:>4}/{total:<4} {percent:6.2f}% | "
        f"ok {succeeded:<4} failed {failed:<4} | "
        f"{rate:5.2f}/s | elapsed {format_duration(elapsed):>8} | "
        f"ETA {format_duration(eta):>8} | {run['status']}"
    )


def monitor_run(run_id):
    print(f"Tracking run #{run_id}. Press Ctrl-C to detach; the worker will keep running.")
    last_line_length = 0
    try:
        while True:
            run = run_snapshot(run_id)
            if not run:
                raise SystemExit(f"Run #{run_id} was not found.")
            line = progress_line(run)
            padding = " " * max(0, last_line_length - len(line))
            print(f"\r{line}{padding}", end="", flush=True)
            last_line_length = len(line)
            if run["status"] in FINAL_STATUSES:
                print()
                if run["error"]:
                    print(f"Run message: {run['error']}")
                print(f"Saved in {server.DB_PATH} as scoring run #{run_id}.")
                print(f"Open in the app: http://localhost:3001/run.html?id={run_id}")
                return 0 if run["status"] == "completed" else 1
            time.sleep(1)
    except KeyboardInterrupt:
        print()
        print("Detached from progress display. The scoring worker is still running.")
        print(f"Reconnect with: ./run_us_confidence_scores.py --resume {run_id}")
        return 0


def confirm_start(company_count, workers, max_tokens):
    estimate = server.cost_estimate(MODEL, company_count, REASONING_MODE)
    estimated_cents = estimate["estimated_cost"] * 100
    print("US company confidence scoring")
    print(f"Companies: {company_count}")
    print(f"Model: {MODEL}")
    print(f"Reasoning: {REASONING_MODE}")
    print(f"Workers: {workers}")
    print(f"Response limit: {max_tokens} tokens per company")
    print("\nPrompt:\n")
    print(PROMPT)
    print()
    print(
        f"Estimated cost: {estimated_cents:.4f} cents "
        f"({estimate['sample_size']} recent request samples)"
    )
    response = input("Start this run? [y/N] ").strip().lower()
    return response in {"y", "yes"}


def main():
    args = parse_args()
    server.ensure_scoring_schema()

    if args.resume is not None:
        return monitor_run(args.resume)

    if not 1 <= args.workers <= 20:
        raise SystemExit("--workers must be between 1 and 20.")
    max_tokens = server.normalize_max_tokens(args.max_tokens)
    tickers = us_tickers()
    if not tickers:
        raise SystemExit("No current US companies were found in companies.db.")

    if not args.yes and not confirm_start(len(tickers), args.workers, max_tokens):
        print("Cancelled. No requests were sent.")
        return 0

    os.environ["SCORING_CONCURRENCY"] = str(args.workers)
    run_name = args.name or f"US company confidence {datetime.now():%Y-%m-%d %H:%M}"
    run_id = server.create_scoring_run(
        name=run_name,
        prompt=PROMPT,
        model=MODEL,
        reasoning_mode=REASONING_MODE,
        tickers=tickers,
        max_tokens=max_tokens,
    )
    print(f"Started detached scoring worker for run #{run_id}.")
    print(f"Reconnect later with: ./run_us_confidence_scores.py --resume {run_id}")
    if args.no_wait:
        return 0
    return monitor_run(run_id)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
