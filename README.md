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
