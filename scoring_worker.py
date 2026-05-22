#!/usr/bin/env python3
import sys

import server


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage: scoring_worker.py RUN_ID [START_INDEX]")
    run_id = int(sys.argv[1])
    start_index = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    server.score_run_worker(run_id, start_index)


if __name__ == "__main__":
    main()
