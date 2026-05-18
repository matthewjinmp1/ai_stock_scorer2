#!/usr/bin/env python3
import html
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
SOURCE_URL = "https://companiesmarketcap.com/"
CACHE_SECONDS = 60 * 15
DB_PATH = ROOT / "companies.db"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_MODEL = "deepseek/deepseek-v4-flash"

_cache = {"companies": None, "fetched_at": 0, "error": None}
_cache_lock = threading.Lock()


def load_env_file():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()


def openrouter_model():
    return os.environ.get("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)


def _clean(value):
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def _number_from_market_cap(value):
    match = re.search(r"\$\s*([\d,.]+)\s*([TBM])", value)
    if not match:
        return 0
    amount = float(match.group(1).replace(",", ""))
    unit = match.group(2)
    multiplier = {"T": 1_000_000_000_000, "B": 1_000_000_000, "M": 1_000_000}[unit]
    return round(amount * multiplier)


def _fetch_html(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        return response.read().decode("utf-8", "replace")


def _parse_companies(page_html):
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", page_html, flags=re.I | re.S)
    companies = []

    for row in rows:
        if "company-code" not in row and "company-name" not in row:
            continue

        rank_match = re.search(r'<td[^>]*class="[^"]*rank-td[^"]*"[^>]*>\s*(\d+)', row, re.I)
        name_match = re.search(r'<div[^>]*class="[^"]*company-name[^"]*"[^>]*>(.*?)</div>', row, re.I | re.S)
        ticker_match = re.search(r'<div[^>]*class="[^"]*company-code[^"]*"[^>]*>(.*?)</div>', row, re.I | re.S)
        logo_match = re.search(r'<img[^>]+class="[^"]*company-logo[^"]*"[^>]+src="([^"]+)"', row, re.I)
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.I | re.S)

        if not (rank_match and name_match and ticker_match) or len(tds) < 5:
            continue

        values = [_clean(td) for td in tds]
        country = re.sub(r"[^\x00-\x7F]+", "", values[-1]).strip()
        today = values[5]
        if "percentage-red" in tds[5] and not today.startswith("-"):
            today = f"-{today}"
        logo = html.unescape(logo_match.group(1)) if logo_match else ""
        if logo.startswith("/"):
            logo = f"https://companiesmarketcap.com{logo}"

        companies.append(
            {
                "rank": int(rank_match.group(1)),
                "name": _clean(name_match.group(1)),
                "ticker": _clean(ticker_match.group(1)),
                "marketCap": values[3],
                "marketCapValue": _number_from_market_cap(values[3]),
                "price": values[4],
                "today": today,
                "country": country,
                "logo": logo,
            }
        )

    if not companies:
        # Fallback for the text-like shape returned by some crawlers.
        compact = re.sub(r"\s+", " ", _clean(page_html))
        pattern = re.compile(
            r"(?:favorite icon )?(?P<rank>\d{1,3}) Image: (?P<name>.+?) logo "
            r"(?P=name) (?P<ticker>[A-Z0-9.\-]+) (?P<cap>\$[\d,.]+ [TBM])"
            r"(?P<price>\$[\d,.]+) (?P<today>[\d.\-]+%) (?P<country>[A-Za-z. ]+?)(?= favorite icon \d|$)"
        )
        for match in pattern.finditer(compact):
            companies.append(
                {
                    "rank": int(match.group("rank")),
                    "name": match.group("name").strip(),
                    "ticker": match.group("ticker"),
                    "marketCap": match.group("cap"),
                    "marketCapValue": _number_from_market_cap(match.group("cap")),
                    "price": match.group("price"),
                    "today": match.group("today"),
                    "country": match.group("country").strip(),
                    "logo": "",
                }
            )

    return sorted(companies, key=lambda item: item["rank"])[:100]


def get_companies(force=False):
    now = time.time()
    with _cache_lock:
        if (
            not force
            and _cache["companies"] is not None
            and now - _cache["fetched_at"] < CACHE_SECONDS
        ):
            return _cache

    try:
        companies = _parse_companies(_fetch_html(SOURCE_URL))
        if len(companies) < 100:
            raise ValueError(f"Expected 100 companies, parsed {len(companies)}")
        payload = {"companies": companies, "fetched_at": time.time(), "error": None}
    except Exception as exc:
        with _cache_lock:
            stale = _cache["companies"]
        if stale:
            payload = {"companies": stale, "fetched_at": _cache["fetched_at"], "error": str(exc)}
        else:
            raise

    with _cache_lock:
        _cache.update(payload)
        return _cache


def db_connect():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def ensure_scoring_schema():
    with db_connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS scoring_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                company_count INTEGER NOT NULL DEFAULT 0,
                completed_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                started_at INTEGER,
                finished_at INTEGER,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS scoring_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                company_name TEXT NOT NULL,
                rank INTEGER NOT NULL,
                market_cap TEXT NOT NULL,
                market_cap_value INTEGER NOT NULL,
                price TEXT NOT NULL,
                country TEXT NOT NULL,
                score REAL,
                raw_response TEXT,
                error TEXT,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(run_id) REFERENCES scoring_runs(id),
                UNIQUE(run_id, ticker)
            );

            CREATE INDEX IF NOT EXISTS idx_scoring_runs_created_at ON scoring_runs(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_scoring_results_run_score ON scoring_results(run_id, score DESC);
            """
        )


def row_to_company(row):
    return {
        "rank": row["rank"],
        "name": row["name"],
        "ticker": row["ticker"],
        "marketCap": row["market_cap"],
        "marketCapValue": row["market_cap_value"],
        "price": row["price"],
        "today": row["today"],
        "country": row["country"],
        "logo": row["logo"],
    }


def db_companies():
    with db_connect() as connection:
        rows = connection.execute(
            """
            SELECT ticker, rank, name, market_cap, market_cap_value, price, today, country, logo
            FROM companies
            ORDER BY rank
            """
        ).fetchall()
    return [row_to_company(row) for row in rows]


def create_scoring_run(prompt, model):
    if not os.environ.get("OPENROUTER_KEY"):
        raise RuntimeError("OPENROUTER_KEY is not set")

    companies = db_companies()
    if not companies:
        raise RuntimeError("No companies found. Run ./fetch_companies_to_db.py first.")

    now = int(time.time())
    with db_connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO scoring_runs (prompt, model, status, company_count, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (prompt, model, "queued", len(companies), now),
        )
        run_id = cursor.lastrowid
        connection.commit()

    thread = threading.Thread(target=score_run_worker, args=(run_id,), daemon=True)
    thread.start()
    return run_id


def get_run(run_id):
    with db_connect() as connection:
        run = connection.execute(
            """
            SELECT id, prompt, model, status, company_count, completed_count, failed_count,
                   created_at, started_at, finished_at, error
            FROM scoring_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        if not run:
            return None
        results = connection.execute(
            """
            SELECT ticker, company_name, rank, market_cap, market_cap_value, price, country,
                   score, raw_response, error, created_at
            FROM scoring_results
            WHERE run_id = ?
            ORDER BY score IS NULL, score DESC, rank ASC
            """,
            (run_id,),
        ).fetchall()
    payload = dict(run)
    payload["results"] = [dict(row) for row in results]
    return payload


def list_runs():
    with db_connect() as connection:
        rows = connection.execute(
            """
            SELECT id, prompt, model, status, company_count, completed_count, failed_count,
                   created_at, started_at, finished_at, error
            FROM scoring_runs
            ORDER BY created_at DESC, id DESC
            LIMIT 100
            """
        ).fetchall()
    return [dict(row) for row in rows]


def update_run_counts(connection, run_id):
    row = connection.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN error IS NULL THEN 1 ELSE 0 END) AS completed,
            SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS failed
        FROM scoring_results
        WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    connection.execute(
        """
        UPDATE scoring_runs
        SET completed_count = ?, failed_count = ?
        WHERE id = ?
        """,
        (row["completed"] or 0, row["failed"] or 0, run_id),
    )


def parse_numeric_score(text):
    match = re.search(r"-?\d+(?:\.\d+)?", text or "")
    if not match:
        raise ValueError("Model response did not contain a number")
    return float(match.group(0))


def call_openrouter(prompt, company, model):
    api_key = os.environ.get("OPENROUTER_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_KEY is not set")

    user_prompt = (
        f"{prompt.strip()}\n\n"
        "Return only one numeric score. Do not include explanation, labels, punctuation, or units.\n\n"
        "Company data:\n"
        f"Name: {company['name']}\n"
        f"Ticker: {company['ticker']}\n"
        f"Market cap: {company['marketCap']}\n"
        f"Market cap numeric USD: {company['marketCapValue']}\n"
        f"Share price: {company['price']}\n"
        f"Country: {company['country']}\n"
        f"CompaniesMarketCap rank: {company['rank']}\n"
    )
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a quantitative stock scoring engine. Return only a number.",
                },
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "max_tokens": 20,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        OPENROUTER_API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost",
            "X-Title": "AI Stock Scorer",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload["choices"][0]["message"]["content"].strip()


def save_result(connection, run_id, company, score, raw_response, error):
    now = int(time.time())
    connection.execute(
        """
        INSERT INTO scoring_results (
            run_id, ticker, company_name, rank, market_cap, market_cap_value, price,
            country, score, raw_response, error, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, ticker) DO UPDATE SET
            company_name = excluded.company_name,
            rank = excluded.rank,
            market_cap = excluded.market_cap,
            market_cap_value = excluded.market_cap_value,
            price = excluded.price,
            country = excluded.country,
            score = excluded.score,
            raw_response = excluded.raw_response,
            error = excluded.error,
            created_at = excluded.created_at
        """,
        (
            run_id,
            company["ticker"],
            company["name"],
            company["rank"],
            company["marketCap"],
            company["marketCapValue"],
            company["price"],
            company["country"],
            score,
            raw_response,
            error,
            now,
        ),
    )


def score_run_worker(run_id):
    ensure_scoring_schema()
    with db_connect() as connection:
        run = connection.execute("SELECT prompt, model FROM scoring_runs WHERE id = ?", (run_id,)).fetchone()
        if not run:
            return
        connection.execute(
            "UPDATE scoring_runs SET status = ?, started_at = ? WHERE id = ?",
            ("running", int(time.time()), run_id),
        )
        connection.commit()

    fatal_error = None
    try:
        companies = db_companies()
        for company in companies:
            raw_response = None
            score = None
            error = None
            try:
                raw_response = call_openrouter(run["prompt"], company, run["model"])
                score = parse_numeric_score(raw_response)
            except Exception as exc:
                error = str(exc)

            with db_connect() as connection:
                save_result(connection, run_id, company, score, raw_response, error)
                update_run_counts(connection, run_id)
                connection.commit()
    except Exception as exc:
        fatal_error = str(exc)

    with db_connect() as connection:
        status = "failed" if fatal_error else "completed"
        connection.execute(
            """
            UPDATE scoring_runs
            SET status = ?, finished_at = ?, error = ?
            WHERE id = ?
            """,
            (status, int(time.time()), fatal_error, run_id),
        )
        update_run_counts(connection, run_id)
        connection.commit()


def watched_signature():
    files = ["server.py", "index.html", "styles.css", "app.js", "start_server.sh"]
    signature = []
    for filename in files:
        path = ROOT / filename
        if path.exists():
            stat = path.stat()
            signature.append(f"{filename}:{stat.st_mtime_ns}:{stat.st_size}")
    return "|".join(signature)


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        parsed = urlparse(path).path.lstrip("/")
        if not parsed:
            parsed = "index.html"
        return str((ROOT / parsed).resolve())

    def send_json(self, payload, status=200):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            return {}
        return json.loads(self.rfile.read(content_length).decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/run.html":
            self.path = "/run.html"
            super().do_GET()
            return

        if parsed.path == "/api/companies":
            try:
                companies = db_companies()
                self.send_json(
                    {
                        "source": str(DB_PATH),
                        "companies": companies,
                    }
                )
            except (urllib.error.URLError, ValueError, TimeoutError) as exc:
                self.send_json({"error": str(exc), "companies": []}, 502)
            return

        if parsed.path == "/api/runs":
            ensure_scoring_schema()
            self.send_json({"runs": list_runs()})
            return

        run_match = re.fullmatch(r"/api/runs/(\d+)", parsed.path)
        if run_match:
            ensure_scoring_schema()
            run = get_run(int(run_match.group(1)))
            if not run:
                self.send_json({"error": "Run not found"}, 404)
                return
            self.send_json({"run": run})
            return

        if parsed.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            previous = watched_signature()
            try:
                while True:
                    current = watched_signature()
                    if current != previous:
                        previous = current
                        self.wfile.write(b"event: reload\ndata: changed\n\n")
                    else:
                        self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    time.sleep(1)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/runs":
            ensure_scoring_schema()
            try:
                payload = self.read_json()
                prompt = (payload.get("prompt") or "").strip()
                if not prompt:
                    self.send_json({"error": "Prompt is required"}, 400)
                    return
                run_id = create_scoring_run(prompt, openrouter_model())
                self.send_json({"runId": run_id, "url": f"/run.html?id={run_id}"}, 201)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return
        self.send_json({"error": "Not found"}, 404)


def main():
    ensure_scoring_schema()
    port = int(os.environ.get("PORT", "3000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Serving AI Stock Scorer at http://localhost:{port}")
    print("Edit server.py, index.html, styles.css, or app.js and the browser will auto-refresh.")
    server.serve_forever()


if __name__ == "__main__":
    main()
