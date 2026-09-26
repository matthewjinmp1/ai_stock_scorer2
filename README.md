# ai_stock_scorer2

## Export a portfolio to IBKR

In Runs, select **Build Portfolio**, choose the rules, and click **Create portfolio**.
On the composition page, use **Export to IBKR**: enter a USD budget, review or edit
the saved-run limit prices, and click **Calculate shares**. Quantities round down
at the portfolio weights; excluded holdings and rounding leave cash unallocated.
Review the IBKR symbols and quantities, then click **Save IBKR CSV to Jts**.
Each export creates a unique CSV in `~/Jts` without overwriting earlier exports.
The confirmation shows the exact path to load using BasketTrader's **Browse** button.

Exports contain US stock BUY / LMT / DAY orders in USD routed through SMART.
Non-US holdings are excluded; zero-share rows are omitted. Saved prices are not
live quotes. Symbol mappings (including share classes) should be checked in IBKR.
Saving a CSV does not submit orders or rebalance existing positions.

Order sizing tests: `node --test test_ibkr_export.mjs`.

## US company confidence run

Requests wait up to 10 seconds for response headers (including the connection
handshake), then track generation time separately. Streaming allows these phases
to be distinguished. A connection timeout tries a different eligible provider of
the same model, up to the configured attempt limit (three by default), without
reusing a timed-out provider. Low-cost mode starts with its selected provider and
may fall back to another provider. If no alternative is available, the stock fails.
The existing generation timeout remains in effect after connection.
Request Details shows separate connection and response durations; the request log
records these for each attempt. This applies to new requests, not workers that
were already running when the application was updated.

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

## Read-only IBKR cash

Install `python3 -m pip install -r requirements.txt`. Keep TWS logged in with
Socket Clients enabled, Read-Only API checked, and port 7496. **All cash** on the
IBKR export view fills the USD budget from the lower of CashBalance and
AvailableFunds. It requires exactly one account and USD balances. No fees are
reserved. Click **Fetch prices & calculate shares** after loading cash.
The integration requests account data only and never submits orders.

Enable **Balance purchases against my current positions (buy only)** to read
TWS positions before sizing. The budget tops up underweight target US holdings,
using fresh source prices and normalized eligible target weights. No sells are
created; non-target positions and pending orders are not included. Incomplete
position reads or short target holdings stop calculation.
