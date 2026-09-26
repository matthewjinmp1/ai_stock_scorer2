# ai_stock_scorer2

## Export a portfolio to IBKR

In Runs, select **Build Portfolio**, choose the rules, and click **Create portfolio**.
On the composition page, open **Export to IBKR**, enter a USD budget, and
click **Fetch prices & calculate shares**. Fresh CompaniesMarketCap US prices
are used only for sizing; stored prices are never substituted.

Exports contain BUY / MKT / DAY orders, SMART routing, and fractional quantities
rounded down to four decimal places. **GoodAfter** schedules activation at 10:30
Eastern on the next trading day (strictly after today), skipping weekends and
NYSE holidays. **OutsideRth** is FALSE. The exact date appears before saving.
This is a one-time schedule, not a recurring weekly purchase.

Market execution prices and total spending can differ from estimates. No cash
buffer is deducted; IBKR may reject orders for insufficient funds. Review the
schedule and fractional-order acceptance in TWS before transmitting. Unexpected
exchange closures may require reviewing the date manually.

**Save IBKR CSV to Jts** replaces `~/Jts/ibkr_basket.csv` only after validation
and a complete write. Saving creates a file; it never submits orders.

Install dependencies with `python3 -m pip install -r requirements.txt`.
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
