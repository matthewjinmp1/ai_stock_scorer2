# ai_stock_scorer2

## Export a portfolio to IBKR

In Runs, select **Build Portfolio**, choose the rules, and click **Create portfolio**.
On the composition page, open **Export to IBKR**, enter a USD budget, and
click **Fetch prices & calculate shares**. Fresh CompaniesMarketCap US prices
are used only for sizing; stored prices are never substituted.

Exports contain BUY / LMT / DAY orders, SMART routing, and fractional quantities
rounded down to four decimal places. Each **LmtPrice** is 3% above the freshly
fetched price, rounded up to the cent, so orders fill like market orders unless
the price rises more than 3% by activation; unfilled orders expire at the close.
**GoodAfterTime** schedules activation at 10:30 Eastern on the next trading day
(strictly after today), skipping weekends and NYSE holidays. **OutsideRth** is
FALSE. The exact date appears before saving. This is a one-time schedule, not a
recurring weekly purchase.

Limit orders are used because IBKR Lite treats market orders placed before the
open as OnOpen orders, which lose commission-free pricing past 10% of monthly
volume; IBKR cancelled such a weekend market basket. Shares are sized so the
total at limit prices fits the budget, which also covers IBKR's cash check.
Commissions are not reserved (IBKR Lite). Review the schedule and
fractional-order acceptance in TWS before transmitting. Unexpected exchange
closures may require reviewing the date manually.

### Read positions and cash with IBKR Flex (works on IBKR Lite)

IBKR Lite blocks the TWS API, so **All cash** and position balancing can read a
Flex Query instead. Flex is reporting-only and cannot place orders.

1. In the IBKR Portal, open **Performance & Reports → Flex Queries** and create an
   **Activity Flex Query** with the **Open Positions** section (Summary; Symbol,
   Asset Class, Currency, Quantity) and the **Cash Report** section. Use XML
   format and period **Last Business Day**. Save it and note its Query ID.
2. On the same page, open **Flex Web Service Configuration**, enable it, and
   generate a token.
3. Add `IBKR_FLEX_TOKEN=...` and `IBKR_FLEX_QUERY_ID=...` to `.env` (not
   committed), then restart the app.

When both are set, the app uses Flex instead of TWS. Flex data is as of the last
business day and excludes pending orders and newer deposits.

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
