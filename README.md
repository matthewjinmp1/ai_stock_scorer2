# ai_stock_scorer2

## US company confidence run

Run the saved DeepSeek confidence prompt against every current US company:

```bash
./run_us_confidence_scores.py
```

The command shows the estimated cost and asks for confirmation before sending
requests. It uses 20 concurrent requests by default, saves the run and results
in `companies.db`, and displays live progress, throughput, elapsed time, and ETA.
The scoring worker runs independently, so closing the progress display does not
stop the run.

Reconnect to an existing run:

```bash
./run_us_confidence_scores.py --resume RUN_ID
```

Use `--workers`, `--max-tokens`, `--name`, `--yes`, or `--no-wait` to override
the defaults. Run `./run_us_confidence_scores.py --help` for all options.

## Count lines of code

Count source lines by module and language:

```bash
./count_lines.py
```

Use `./count_lines.py --json` for machine-readable output.

## Refresh company data

Fetch every company currently listed by CompaniesMarketCap:

```bash
./fetch_companies_to_db.py --all
```

The command prints progress after each page and only updates `companies.db`
after the complete scrape succeeds. A complete refresh also removes stale
companies that are no longer present in the site's full listing.
