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
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parent
SOURCE_URL = "https://companiesmarketcap.com/"
CACHE_SECONDS = 60 * 15
DB_PATH = ROOT / "companies.db"
AI_REQUEST_LOG_PATH = ROOT / "ai_requests.json"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "deepseek/deepseek-v4-flash"
DEFAULT_SCORING_COMPANY_COUNT = 10
OPENROUTER_MAX_TOKENS = int(os.environ.get("OPENROUTER_MAX_TOKENS", "200"))

_cache = {"companies": None, "fetched_at": 0, "error": None}
_cache_lock = threading.Lock()
_ai_request_log_lock = threading.Lock()


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
    return OPENROUTER_MODEL


def openrouter_max_tokens():
    return int(os.environ.get("OPENROUTER_MAX_TOKENS", str(OPENROUTER_MAX_TOKENS)))


def prompt_has_company_keyword(prompt):
    return "COMPANY" in prompt


def append_ai_request_log(entry):
    with _ai_request_log_lock:
        if AI_REQUEST_LOG_PATH.exists():
            try:
                entries = json.loads(AI_REQUEST_LOG_PATH.read_text())
                if not isinstance(entries, list):
                    entries = []
            except json.JSONDecodeError:
                entries = []
        else:
            entries = []

        entries.append(entry)
        tmp_path = AI_REQUEST_LOG_PATH.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(entries, indent=2, sort_keys=True))
        tmp_path.replace(AI_REQUEST_LOG_PATH)


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
                name TEXT,
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
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(scoring_runs)").fetchall()
        }
        if "name" not in columns:
            connection.execute("ALTER TABLE scoring_runs ADD COLUMN name TEXT")
        connection.execute(
            """
            UPDATE scoring_runs
            SET name = 'Run #' || id
            WHERE name IS NULL OR trim(name) = ''
            """
        )
        connection.commit()


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


def scoring_companies(company_count):
    return db_companies()[:company_count]


def normalize_company_count(value):
    companies_available = len(db_companies())
    if companies_available <= 0:
        raise RuntimeError("No companies found. Run ./fetch_companies_to_db.py first.")
    try:
        company_count = int(value)
    except (TypeError, ValueError):
        company_count = DEFAULT_SCORING_COMPANY_COUNT
    if company_count < 1:
        raise ValueError("Company count must be at least 1.")
    if company_count > companies_available:
        raise ValueError(f"Company count cannot exceed {companies_available}.")
    return company_count


def normalize_run_name(value):
    name = (value or "").strip()
    if not name:
        raise ValueError("Run name is required.")
    if len(name) > 120:
        raise ValueError("Run name must be 120 characters or fewer.")
    return name


def create_scoring_run(name, prompt, model, company_count):
    if not os.environ.get("OPENROUTER_KEY"):
        raise RuntimeError("OPENROUTER_KEY is not set")
    if not prompt_has_company_keyword(prompt):
        raise ValueError("Prompt must include the COMPANY keyword.")

    name = normalize_run_name(name)
    company_count = normalize_company_count(company_count)
    companies = scoring_companies(company_count)
    if not companies:
        raise RuntimeError("No companies found. Run ./fetch_companies_to_db.py first.")

    now = int(time.time())
    with db_connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO scoring_runs (name, prompt, model, status, company_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, prompt, model, "queued", len(companies), now),
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
            SELECT id, name, prompt, model, status, company_count, completed_count, failed_count,
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


def ai_request_entries():
    if not AI_REQUEST_LOG_PATH.exists():
        return []
    try:
        entries = json.loads(AI_REQUEST_LOG_PATH.read_text())
        return entries if isinstance(entries, list) else []
    except json.JSONDecodeError:
        return []


def find_ai_request_entry(run_id, ticker):
    ticker = ticker.upper()
    matches = [
        entry
        for entry in ai_request_entries()
        if entry.get("run_id") == run_id
        and (entry.get("company", {}).get("ticker") or "").upper() == ticker
    ]
    return matches[-1] if matches else None


def get_result_detail(run_id, ticker):
    with db_connect() as connection:
        run = connection.execute(
            """
            SELECT id, name, prompt, model, status, created_at, started_at, finished_at, error
            FROM scoring_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        if not run:
            return None

        result = connection.execute(
            """
            SELECT ticker, company_name, rank, market_cap, market_cap_value, price, country,
                   score, raw_response, error, created_at
            FROM scoring_results
            WHERE run_id = ? AND ticker = ?
            """,
            (run_id, ticker),
        ).fetchone()
        if not result:
            return None

    return {
        "run": dict(run),
        "result": dict(result),
        "aiRequest": sanitized_ai_request_entry(find_ai_request_entry(run_id, ticker)),
    }


def run_status(run_id):
    with db_connect() as connection:
        row = connection.execute("SELECT status FROM scoring_runs WHERE id = ?", (run_id,)).fetchone()
    return row["status"] if row else None


def stop_scoring_run(run_id):
    now = int(time.time())
    with db_connect() as connection:
        row = connection.execute("SELECT status FROM scoring_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            return None
        if row["status"] in ("queued", "running"):
            connection.execute(
                "UPDATE scoring_runs SET status = ?, error = ? WHERE id = ?",
                ("stop_requested", "Stop requested by user.", run_id),
            )
        elif row["status"] == "stop_requested":
            pass
        update_run_counts(connection, run_id)
        connection.commit()
        updated = connection.execute(
            """
            SELECT id, name, prompt, model, status, company_count, completed_count, failed_count,
                   created_at, started_at, finished_at, error
            FROM scoring_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
    return dict(updated)


def list_runs():
    with db_connect() as connection:
        rows = connection.execute(
            """
            SELECT id, name, prompt, model, status, company_count, completed_count, failed_count,
                   created_at, started_at, finished_at, error
            FROM scoring_runs
            ORDER BY created_at DESC, id DESC
            LIMIT 100
            """
        ).fetchall()
    return [dict(row) for row in rows]


def rename_scoring_run(run_id, name):
    name = normalize_run_name(name)
    with db_connect() as connection:
        row = connection.execute("SELECT id FROM scoring_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            return None
        connection.execute("UPDATE scoring_runs SET name = ? WHERE id = ?", (name, run_id))
        connection.commit()
    return get_run(run_id)


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


class FatalScoringError(RuntimeError):
    pass


def openrouter_error_message(error):
    try:
        body = error.read().decode("utf-8", "replace")
    except Exception:
        body = ""
    message = f"OpenRouter HTTP {error.code}: {error.reason}"
    if body:
        try:
            payload = json.loads(body)
            detail = payload.get("error", {}).get("message") or payload.get("message")
            if detail:
                message = f"{message} - {detail}"
        except json.JSONDecodeError:
            message = f"{message} - {body[:300]}"
    return message


def prompt_for_company(prompt, company):
    return prompt.strip().replace("COMPANY", company["name"])


def openrouter_http_error_details(error):
    try:
        body = error.read().decode("utf-8", "replace")
    except Exception:
        body = ""

    message = f"OpenRouter HTTP {error.code}: {error.reason}"
    parsed_body = None
    if body:
        try:
            parsed_body = json.loads(body)
            detail = parsed_body.get("error", {}).get("message") or parsed_body.get("message")
            if detail:
                message = f"{message} - {detail}"
        except json.JSONDecodeError:
            message = f"{message} - {body[:300]}"

    return {
        "message": message,
        "status": error.code,
        "reason": error.reason,
        "body": parsed_body if parsed_body is not None else body,
    }


def ai_log_entry(run_id, company, request_payload, started_at, response_payload=None, error=None, http_status=None):
    choice = None
    if response_payload and response_payload.get("choices"):
        choice = response_payload["choices"][0]

    return {
        "timestamp": int(time.time()),
        "run_id": run_id,
        "company": {
            "name": company["name"],
            "ticker": company["ticker"],
            "market_cap_rank": company["rank"],
        },
        "request": {
            "provider": "openrouter",
            "url": OPENROUTER_API_URL,
            "model": request_payload.get("model"),
            "messages": request_payload.get("messages"),
            "temperature": request_payload.get("temperature"),
            "max_tokens": request_payload.get("max_tokens"),
            "reasoning": request_payload.get("reasoning"),
            "prompt_sent": request_payload["messages"][0]["content"],
        },
        "response": {
            "success": error is None,
            "http_status": http_status,
            "id": response_payload.get("id") if response_payload else None,
            "created": response_payload.get("created") if response_payload else None,
            "model": response_payload.get("model") if response_payload else None,
            "visible_content": choice.get("message", {}).get("content") if choice else None,
            "finish_reason": choice.get("finish_reason") if choice else None,
            "raw_payload": sanitized_ai_payload(response_payload),
            "error": error,
        },
        "token_stats": response_payload.get("usage") if response_payload else None,
        "timing": {
            "duration_ms": round((time.time() - started_at) * 1000),
        },
        "chain_of_thought": None,
        "chain_of_thought_note": "Hidden chain-of-thought is not exposed by the model/API and is not logged.",
    }


def sanitized_ai_payload(payload):
    if payload is None:
        return None
    sanitized = json.loads(json.dumps(payload))
    for choice in sanitized.get("choices", []) or []:
        message = choice.get("message")
        if isinstance(message, dict):
            message.pop("reasoning", None)
            message.pop("reasoning_details", None)
            message.pop("reasoning_content", None)
    return sanitized


def sanitized_ai_request_entry(entry):
    if entry is None:
        return None
    sanitized = json.loads(json.dumps(entry))
    response = sanitized.get("response")
    if isinstance(response, dict):
        response["raw_payload"] = sanitized_ai_payload(response.get("raw_payload"))
    sanitized["chain_of_thought"] = None
    sanitized["chain_of_thought_note"] = "Hidden chain-of-thought is not exposed in this app."
    return sanitized


def call_openrouter(prompt, company, model, run_id=None):
    api_key = os.environ.get("OPENROUTER_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_KEY is not set")

    request_payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt_for_company(prompt, company)},
        ],
        "temperature": 0,
        "max_tokens": openrouter_max_tokens(),
        "reasoning": {
            "effort": "none",
            "exclude": True,
        },
    }
    body = json.dumps(request_payload).encode("utf-8")
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
    started_at = time.time()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            http_status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = openrouter_http_error_details(exc)
        append_ai_request_log(
            ai_log_entry(
                run_id,
                company,
                request_payload,
                started_at,
                error=details,
                http_status=details["status"],
            )
        )
        message = details["message"]
        if exc.code in (401, 403):
            raise FatalScoringError(message) from exc
        raise RuntimeError(message) from exc
    except Exception as exc:
        append_ai_request_log(
            ai_log_entry(
                run_id,
                company,
                request_payload,
                started_at,
                error={"message": str(exc), "type": exc.__class__.__name__},
            )
        )
        raise

    try:
        message = payload["choices"][0].get("message", {})
        content = message.get("content")
        if content is None:
            raise ValueError(
                "Model returned no visible content. It likely used the response token budget "
                "without producing an answer."
            )
        content = content.strip()
    except Exception as exc:
        append_ai_request_log(
            ai_log_entry(
                run_id,
                company,
                request_payload,
                started_at,
                response_payload=payload,
                error={"message": str(exc), "type": exc.__class__.__name__},
                http_status=http_status,
            )
        )
        raise

    append_ai_request_log(
        ai_log_entry(
            run_id,
            company,
            request_payload,
            started_at,
            response_payload=payload,
            http_status=http_status,
        )
    )
    return content


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
        run = connection.execute(
            "SELECT name, prompt, model, company_count FROM scoring_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if not run:
            return
        connection.execute(
            "UPDATE scoring_runs SET status = ?, started_at = ? WHERE id = ?",
            ("running", int(time.time()), run_id),
        )
        connection.commit()

    fatal_error = None
    stopped = False
    try:
        companies = scoring_companies(run["company_count"])
        for company in companies:
            if run_status(run_id) == "stop_requested":
                stopped = True
                break

            raw_response = None
            score = None
            error = None
            try:
                raw_response = call_openrouter(run["prompt"], company, run["model"], run_id=run_id)
                score = parse_numeric_score(raw_response)
            except FatalScoringError as exc:
                fatal_error = str(exc)
                break
            except Exception as exc:
                error = str(exc)

            with db_connect() as connection:
                save_result(connection, run_id, company, score, raw_response, error)
                update_run_counts(connection, run_id)
                connection.commit()
    except Exception as exc:
        fatal_error = str(exc)

    with db_connect() as connection:
        current_status = run_status(run_id)
        if current_status == "stop_requested":
            stopped = True
        status = "stopped" if stopped else "failed" if fatal_error else "completed"
        final_error = "Stopped by user." if stopped else fatal_error
        connection.execute(
            """
            UPDATE scoring_runs
            SET status = ?, finished_at = ?, error = ?
            WHERE id = ?
            """,
            (status, int(time.time()), final_error, run_id),
        )
        update_run_counts(connection, run_id)
        connection.commit()


def watched_signature():
    files = [
        "server.py",
        "index.html",
        "styles.css",
        "app.js",
        "run.html",
        "run.js",
        "result.html",
        "result.js",
        "start_server.sh",
    ]
    signature = []
    for filename in files:
        path = ROOT / filename
        if path.exists():
            stat = path.stat()
            signature.append(f"{filename}:{stat.st_mtime_ns}:{stat.st_size}")
    return "|".join(signature)


class Handler(SimpleHTTPRequestHandler):
    def handle_one_request(self):
        try:
            super().handle_one_request()
        except ConnectionResetError:
            pass

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

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

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
        if parsed.path == "/result.html":
            self.path = "/result.html"
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

        result_match = re.fullmatch(r"/api/runs/(\d+)/results/(.+)", parsed.path)
        if result_match:
            ensure_scoring_schema()
            detail = get_result_detail(int(result_match.group(1)), unquote(result_match.group(2)))
            if not detail:
                self.send_json({"error": "Result not found"}, 404)
                return
            self.send_json({"detail": detail})
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
        stop_match = re.fullmatch(r"/api/runs/(\d+)/stop", parsed.path)
        if stop_match:
            ensure_scoring_schema()
            run = stop_scoring_run(int(stop_match.group(1)))
            if not run:
                self.send_json({"error": "Run not found"}, 404)
                return
            self.send_json({"run": run})
            return

        if parsed.path == "/api/runs":
            ensure_scoring_schema()
            try:
                payload = self.read_json()
                name = (payload.get("name") or "").strip()
                if not name:
                    self.send_json({"error": "Run name is required."}, 400)
                    return
                prompt = (payload.get("prompt") or "").strip()
                if not prompt:
                    self.send_json({"error": "Prompt is required"}, 400)
                    return
                if not prompt_has_company_keyword(prompt):
                    self.send_json({"error": "Prompt must include the COMPANY keyword."}, 400)
                    return
                company_count = normalize_company_count(payload.get("companyCount"))
                run_id = create_scoring_run(name, prompt, openrouter_model(), company_count)
                self.send_json({"runId": run_id, "url": f"/run.html?id={run_id}"}, 201)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return
        self.send_json({"error": "Not found"}, 404)

    def do_PATCH(self):
        parsed = urlparse(self.path)
        run_match = re.fullmatch(r"/api/runs/(\d+)", parsed.path)
        if run_match:
            ensure_scoring_schema()
            try:
                payload = self.read_json()
                run = rename_scoring_run(int(run_match.group(1)), payload.get("name"))
                if not run:
                    self.send_json({"error": "Run not found"}, 404)
                    return
                self.send_json({"run": run})
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
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
