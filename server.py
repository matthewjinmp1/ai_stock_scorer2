#!/usr/bin/env python3
import concurrent.futures
import copy
import html
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse


ROOT = Path(__file__).resolve().parent
SOURCE_URL = "https://companiesmarketcap.com/"
COMPANY_UNIVERSE_LIMIT = 1000
COMPANIESMARKETCAP_PAGE_SIZE = 100
CACHE_SECONDS = 60 * 15
DB_PATH = ROOT / "companies.db"
AI_REQUEST_LOG_PATH = ROOT / "ai_requests.json"
PROVIDER_BLOCKLIST_PATH = ROOT / "provider_blocklist.json"
SCORING_WORKER_PATH = ROOT / "scoring_worker.py"
SCORING_WORKER_LOG_PATH = ROOT / "scoring_worker.log"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_PROVIDER_BLOCKLIST = ["siliconflow", "gmicloud"]
PROVIDER_SLUGS = {
    "AkashML": "akashml",
    "Alibaba": "alibaba",
    "AtlasCloud": "atlas-cloud",
    "Baidu": "baidu",
    "DeepSeek": "deepseek",
    "GMICloud": "gmicloud",
    "SiliconFlow": "siliconflow",
}
MODEL_OPTIONS = [
    {
        "id": "deepseek/deepseek-v4-flash-0731",
        "label": "DeepSeek V4 Flash 0731",
        "provider": {
            "require_parameters": True,
        },
    },
    {
        "id": "xiaomi/mimo-v2.5",
        "label": "Xiaomi MiMo V2.5",
        "provider": {
            "require_parameters": True,
        },
    },
    {
        "id": "nvidia/nemotron-3-ultra-550b-a55b:free",
        "label": "NVIDIA Nemotron 3 Ultra (free)",
        "provider": {
            "require_parameters": True,
        },
    },
]
LEGACY_MODEL_OPTIONS = [
    {
        "id": "deepseek/deepseek-v4-flash",
        "label": "DeepSeek V4 Flash (legacy)",
        "provider": {
            "require_parameters": True,
        },
    },
]
ALL_MODEL_OPTIONS = MODEL_OPTIONS + LEGACY_MODEL_OPTIONS
REASONING_OPTIONS = [
    {
        "id": "none",
        "label": "Non-reasoning",
        "reasoning": {"effort": "none", "exclude": False},
    },
    {
        "id": "medium",
        "label": "Reasoning",
        "reasoning": {"effort": "medium", "exclude": False},
    },
]
DEFAULT_OPENROUTER_MODEL = MODEL_OPTIONS[0]["id"]
DEFAULT_REASONING_MODE = REASONING_OPTIONS[0]["id"]
DEFAULT_SCORING_COMPANY_COUNT = 10
DEFAULT_SCORING_CONCURRENCY = 20
OPENROUTER_MAX_TOKENS = int(os.environ.get("OPENROUTER_MAX_TOKENS", "200"))
OPENROUTER_MAX_ATTEMPTS = 3
OPENROUTER_ATTEMPT_TIMEOUT_SECONDS = 90
MAX_OPENROUTER_RESPONSE_TOKENS = 32768
OPENROUTER_RESPONSE_CACHE_TTL_SECONDS = 86400
TOKEN_LIMIT_ERROR = "Invalid response: model hit the response token limit before completing."

_cache = {"companies": None, "fetched_at": 0, "error": None}
_cache_lock = threading.Lock()
_ai_request_log_lock = threading.Lock()
_provider_blocklist_lock = threading.Lock()


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


def openrouter_model_options():
    return MODEL_OPTIONS


def openrouter_model():
    return DEFAULT_OPENROUTER_MODEL


def reasoning_options():
    return REASONING_OPTIONS


def default_reasoning_mode():
    return DEFAULT_REASONING_MODE


def normalize_model(model):
    requested = (model or DEFAULT_OPENROUTER_MODEL).strip()
    allowed = {option["id"] for option in ALL_MODEL_OPTIONS}
    if requested not in allowed:
        raise ValueError("Selected model is not available.")
    return requested


def model_config(model):
    normalized = normalize_model(model)
    return next(option for option in ALL_MODEL_OPTIONS if option["id"] == normalized)


def normalize_reasoning_mode(reasoning_mode):
    requested = (reasoning_mode or DEFAULT_REASONING_MODE).strip()
    allowed = {option["id"] for option in REASONING_OPTIONS}
    if requested not in allowed:
        raise ValueError("Selected reasoning mode is not available.")
    return requested


def reasoning_config(reasoning_mode):
    normalized = normalize_reasoning_mode(reasoning_mode)
    return next(option for option in REASONING_OPTIONS if option["id"] == normalized)


def provider_slug(provider):
    if not provider:
        return ""
    provider = str(provider).strip()
    if not provider:
        return ""
    if provider in PROVIDER_SLUGS:
        return PROVIDER_SLUGS[provider]
    return re.sub(r"[^a-z0-9]+", "-", provider.lower()).strip("-")


def provider_blocklist():
    blocked = {provider_slug(provider) for provider in DEFAULT_PROVIDER_BLOCKLIST}
    with _provider_blocklist_lock:
        if PROVIDER_BLOCKLIST_PATH.exists():
            try:
                payload = json.loads(PROVIDER_BLOCKLIST_PATH.read_text())
                providers = payload.get("blocked_providers") if isinstance(payload, dict) else payload
                if isinstance(providers, list):
                    blocked.update(provider_slug(provider) for provider in providers if provider)
            except json.JSONDecodeError:
                pass
    return sorted(provider for provider in blocked if provider)


def save_provider_blocklist(blocked):
    payload = {
        "blocked_providers": sorted(set(blocked)),
        "updated_at": int(time.time()),
    }
    tmp_path = PROVIDER_BLOCKLIST_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    tmp_path.replace(PROVIDER_BLOCKLIST_PATH)


def block_reasoning_provider(provider, run_id=None, ticker=None, reasoning_tokens=0):
    slug = provider_slug(provider)
    if not slug:
        return False
    with _provider_blocklist_lock:
        blocked = {provider_slug(item) for item in DEFAULT_PROVIDER_BLOCKLIST}
        if PROVIDER_BLOCKLIST_PATH.exists():
            try:
                payload = json.loads(PROVIDER_BLOCKLIST_PATH.read_text())
                providers = payload.get("blocked_providers") if isinstance(payload, dict) else payload
                if isinstance(providers, list):
                    blocked.update(provider_slug(item) for item in providers if item)
            except json.JSONDecodeError:
                pass
        blocked = {item for item in blocked if item}
        already_blocked = slug in blocked
        blocked.add(slug)
        payload = {
            "blocked_providers": sorted(blocked),
            "updated_at": int(time.time()),
            "last_blocked": {
                "provider": provider,
                "provider_slug": slug,
                "run_id": run_id,
                "ticker": ticker,
                "reasoning_tokens": reasoning_tokens,
            },
        }
        tmp_path = PROVIDER_BLOCKLIST_PATH.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
        tmp_path.replace(PROVIDER_BLOCKLIST_PATH)
    return not already_blocked


def provider_preferences(config):
    provider = dict(config.get("provider") or {})
    blocked = provider_blocklist()
    if blocked:
        provider["ignore"] = blocked
    return provider


def model_details(model, reasoning_mode=None):
    config = model_config(model)
    reasoning = reasoning_config(reasoning_mode)
    return {
        "id": config["id"],
        "label": config["label"],
        "reasoning_mode": reasoning["id"],
        "reasoning_label": reasoning["label"],
        "reasoning": reasoning["reasoning"],
        "provider": provider_preferences(config),
    }


def openrouter_max_tokens():
    return int(os.environ.get("OPENROUTER_MAX_TOKENS", str(OPENROUTER_MAX_TOKENS)))


def normalize_max_tokens(value):
    if value is None or value == "":
        value = openrouter_max_tokens()
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Response token limit must be a whole number.") from exc
    if value < 1 or value > MAX_OPENROUTER_RESPONSE_TOKENS:
        raise ValueError(f"Choose a response token limit from 1 to {MAX_OPENROUTER_RESPONSE_TOKENS:,}.")
    return value


def openrouter_max_attempts():
    try:
        value = int(os.environ.get("OPENROUTER_MAX_ATTEMPTS", str(OPENROUTER_MAX_ATTEMPTS)))
    except ValueError:
        value = OPENROUTER_MAX_ATTEMPTS
    return max(1, min(5, value))


def openrouter_attempt_timeout_seconds():
    try:
        value = float(
            os.environ.get(
                "OPENROUTER_ATTEMPT_TIMEOUT_SECONDS",
                str(OPENROUTER_ATTEMPT_TIMEOUT_SECONDS),
            )
        )
    except (TypeError, ValueError):
        value = OPENROUTER_ATTEMPT_TIMEOUT_SECONDS
    return max(1, value)


def read_http_response_with_deadline(response, timeout_seconds):
    timed_out = threading.Event()

    def abort_response():
        timed_out.set()
        try:
            sock = response.fp.raw._sock
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            response.close()
        except Exception:
            pass

    timer = threading.Timer(timeout_seconds, abort_response)
    timer.daemon = True
    timer.start()
    try:
        payload = response.read()
    except Exception as exc:
        if timed_out.is_set():
            raise TimeoutError(
                f"OpenRouter request exceeded the {timeout_seconds:g}-second attempt limit."
            ) from exc
        raise
    finally:
        timer.cancel()

    if timed_out.is_set():
        raise TimeoutError(
            f"OpenRouter request exceeded the {timeout_seconds:g}-second attempt limit."
        )
    return payload


def openrouter_cache_ttl_seconds():
    try:
        value = int(
            os.environ.get(
                "OPENROUTER_CACHE_TTL_SECONDS",
                str(OPENROUTER_RESPONSE_CACHE_TTL_SECONDS),
            )
        )
    except ValueError:
        value = OPENROUTER_RESPONSE_CACHE_TTL_SECONDS
    return max(1, min(86400, value))


def scoring_concurrency():
    try:
        value = int(os.environ.get("SCORING_CONCURRENCY", str(DEFAULT_SCORING_CONCURRENCY)))
    except ValueError:
        value = DEFAULT_SCORING_CONCURRENCY
    return max(1, min(20, value))


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


def company_source_url_for_page(page):
    if page <= 1:
        return SOURCE_URL
    return f"{SOURCE_URL}page/{page}/"


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

    return sorted(companies, key=lambda item: item["rank"])


def fetch_top_market_cap_companies(limit=COMPANY_UNIVERSE_LIMIT):
    companies_by_ticker = {}
    page_count = (limit + COMPANIESMARKETCAP_PAGE_SIZE - 1) // COMPANIESMARKETCAP_PAGE_SIZE
    for page in range(1, page_count + 1):
        parsed = _parse_companies(_fetch_html(company_source_url_for_page(page)))
        for company in parsed:
            companies_by_ticker[company["ticker"]] = company

    companies = sorted(companies_by_ticker.values(), key=lambda item: item["rank"])[:limit]
    if len(companies) < limit:
        raise ValueError(f"Expected {limit} companies, parsed {len(companies)}")
    return companies


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
        companies = fetch_top_market_cap_companies(COMPANY_UNIVERSE_LIMIT)
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
                reasoning_mode TEXT NOT NULL DEFAULT 'none',
                max_tokens INTEGER NOT NULL DEFAULT 200,
                stock_list_id INTEGER,
                status TEXT NOT NULL,
                company_count INTEGER NOT NULL DEFAULT 0,
                completed_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                started_at INTEGER,
                finished_at INTEGER,
                deleted_at INTEGER,
                starred INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                worker_pid INTEGER,
                worker_started_at INTEGER
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

            CREATE TABLE IF NOT EXISTS stock_lists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                deleted_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS stock_list_members (
                list_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY(list_id, ticker),
                FOREIGN KEY(list_id) REFERENCES stock_lists(id)
            );

            CREATE TABLE IF NOT EXISTS scoring_run_companies (
                run_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY(run_id, ticker),
                FOREIGN KEY(run_id) REFERENCES scoring_runs(id)
            );

            CREATE INDEX IF NOT EXISTS idx_scoring_runs_created_at ON scoring_runs(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_scoring_results_run_score ON scoring_results(run_id, score DESC);
            CREATE INDEX IF NOT EXISTS idx_stock_list_members_position ON stock_list_members(list_id, position);
            CREATE INDEX IF NOT EXISTS idx_scoring_run_companies_position ON scoring_run_companies(run_id, position);
            """
        )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(scoring_runs)").fetchall()
        }
        if "name" not in columns:
            connection.execute("ALTER TABLE scoring_runs ADD COLUMN name TEXT")
        if "deleted_at" not in columns:
            connection.execute("ALTER TABLE scoring_runs ADD COLUMN deleted_at INTEGER")
        if "worker_pid" not in columns:
            connection.execute("ALTER TABLE scoring_runs ADD COLUMN worker_pid INTEGER")
        if "worker_started_at" not in columns:
            connection.execute("ALTER TABLE scoring_runs ADD COLUMN worker_started_at INTEGER")
        if "reasoning_mode" not in columns:
            connection.execute("ALTER TABLE scoring_runs ADD COLUMN reasoning_mode TEXT NOT NULL DEFAULT 'none'")
        if "max_tokens" not in columns:
            connection.execute("ALTER TABLE scoring_runs ADD COLUMN max_tokens INTEGER NOT NULL DEFAULT 200")
        if "stock_list_id" not in columns:
            connection.execute("ALTER TABLE scoring_runs ADD COLUMN stock_list_id INTEGER")
        if "starred" not in columns:
            connection.execute("ALTER TABLE scoring_runs ADD COLUMN starred INTEGER NOT NULL DEFAULT 0")
        connection.execute(
            """
            UPDATE scoring_runs
            SET name = 'Run #' || id
            WHERE name IS NULL OR trim(name) = ''
            """
        )
        connection.execute(
            """
            UPDATE scoring_runs
            SET reasoning_mode = 'none'
            WHERE reasoning_mode IS NULL OR trim(reasoning_mode) = ''
            """
        )
        connection.execute(
            "UPDATE scoring_runs SET max_tokens = 200 WHERE max_tokens IS NULL OR max_tokens < 1"
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


def db_companies(active_only=True):
    where_clause = "WHERE fetched_at = (SELECT MAX(fetched_at) FROM companies)" if active_only else ""
    with db_connect() as connection:
        rows = connection.execute(
            f"""
            SELECT ticker, rank, name, market_cap, market_cap_value, price, today, country, logo
            FROM companies
            {where_clause}
            ORDER BY rank
            """
        ).fetchall()
    return [row_to_company(row) for row in rows]


def scoring_companies(company_count):
    return db_companies()[:company_count]


def normalize_stock_list_name(value):
    name = (value or "").strip()
    if not name:
        raise ValueError("List name is required.")
    if len(name) > 120:
        raise ValueError("List name must be 120 characters or fewer.")
    return name


def companies_for_tickers(tickers):
    requested = []
    seen = set()
    for ticker in tickers or []:
        normalized = str(ticker or "").strip().upper()
        if normalized and normalized not in seen:
            requested.append(normalized)
            seen.add(normalized)
    if not requested:
        raise ValueError("Choose at least one stock for the list.")

    companies = {company["ticker"].upper(): company for company in db_companies(active_only=False)}
    missing = [ticker for ticker in requested if ticker not in companies]
    if missing:
        raise ValueError(f"Unknown ticker{'s' if len(missing) != 1 else ''}: {', '.join(missing)}")
    return [companies[ticker] for ticker in requested]


def get_stock_list(list_id):
    try:
        list_id = int(list_id)
    except (TypeError, ValueError):
        return None
    with db_connect() as connection:
        stock_list = connection.execute(
            """
            SELECT id, name, created_at, updated_at
            FROM stock_lists
            WHERE id = ? AND deleted_at IS NULL
            """,
            (list_id,),
        ).fetchone()
        if not stock_list:
            return None
        rows = connection.execute(
            """
            SELECT companies.ticker, companies.rank, companies.name, companies.market_cap,
                   companies.market_cap_value, companies.price, companies.today,
                   companies.country, companies.logo
            FROM stock_list_members
            JOIN companies ON companies.ticker = stock_list_members.ticker
            WHERE stock_list_members.list_id = ?
            ORDER BY stock_list_members.position
            """,
            (list_id,),
        ).fetchall()
    payload = dict(stock_list)
    payload["companies"] = [row_to_company(row) for row in rows]
    payload["company_count"] = len(payload["companies"])
    return payload


def list_stock_lists():
    with db_connect() as connection:
        ids = [
            row["id"]
            for row in connection.execute(
                "SELECT id FROM stock_lists WHERE deleted_at IS NULL ORDER BY updated_at DESC, id DESC"
            ).fetchall()
        ]
    return [stock_list for list_id in ids if (stock_list := get_stock_list(list_id))]


def save_stock_list(name, tickers, list_id=None):
    name = normalize_stock_list_name(name)
    companies = companies_for_tickers(tickers)
    now = int(time.time())
    with db_connect() as connection:
        if list_id is None:
            cursor = connection.execute(
                "INSERT INTO stock_lists (name, created_at, updated_at) VALUES (?, ?, ?)",
                (name, now, now),
            )
            list_id = cursor.lastrowid
        else:
            try:
                list_id = int(list_id)
            except (TypeError, ValueError) as exc:
                raise ValueError("Stock list not found.") from exc
            existing = connection.execute(
                "SELECT id FROM stock_lists WHERE id = ? AND deleted_at IS NULL",
                (list_id,),
            ).fetchone()
            if not existing:
                return None
            connection.execute(
                "UPDATE stock_lists SET name = ?, updated_at = ? WHERE id = ?",
                (name, now, list_id),
            )
            connection.execute("DELETE FROM stock_list_members WHERE list_id = ?", (list_id,))

        connection.executemany(
            "INSERT INTO stock_list_members (list_id, ticker, position) VALUES (?, ?, ?)",
            [(list_id, company["ticker"], position) for position, company in enumerate(companies)],
        )
        connection.commit()
    return get_stock_list(list_id)


def archive_stock_list(list_id):
    now = int(time.time())
    with db_connect() as connection:
        cursor = connection.execute(
            "UPDATE stock_lists SET deleted_at = ?, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
            (now, now, list_id),
        )
        connection.commit()
    return cursor.rowcount > 0


def snapshot_run_companies(connection, run_id, companies):
    connection.execute("DELETE FROM scoring_run_companies WHERE run_id = ?", (run_id,))
    connection.executemany(
        "INSERT INTO scoring_run_companies (run_id, ticker, position) VALUES (?, ?, ?)",
        [(run_id, company["ticker"], position) for position, company in enumerate(companies)],
    )


def scoring_companies_for_run(run_id):
    with db_connect() as connection:
        rows = connection.execute(
            """
            SELECT companies.ticker, companies.rank, companies.name, companies.market_cap,
                   companies.market_cap_value, companies.price, companies.today,
                   companies.country, companies.logo
            FROM scoring_run_companies
            JOIN companies ON companies.ticker = scoring_run_companies.ticker
            WHERE scoring_run_companies.run_id = ?
            ORDER BY scoring_run_companies.position
            """,
            (run_id,),
        ).fetchall()
        if rows:
            return [row_to_company(row) for row in rows]
        run = connection.execute(
            "SELECT company_count FROM scoring_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    return scoring_companies(run["company_count"]) if run else []


def extension_companies_for_run(run_id, stock_list_id=None):
    existing = scoring_companies_for_run(run_id)
    if stock_list_id is not None:
        stock_list = get_stock_list(stock_list_id)
        source = stock_list["companies"] if stock_list else []
    else:
        source = db_companies()

    companies = list(existing)
    seen = {company["ticker"] for company in companies}
    for company in source:
        if company["ticker"] in seen:
            continue
        companies.append(company)
        seen.add(company["ticker"])
    return companies


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


def normalize_scoring_prompt(value):
    prompt = (value or "").strip()
    if not prompt:
        raise ValueError("Prompt is required")
    if not prompt_has_company_keyword(prompt):
        raise ValueError("Prompt must include the COMPANY keyword.")
    return prompt


def process_is_running(pid):
    try:
        pid = int(pid or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start_scoring_worker_process(run_id, start_index=0):
    with SCORING_WORKER_LOG_PATH.open("ab") as log_file:
        process = subprocess.Popen(
            [sys.executable, str(SCORING_WORKER_PATH), str(run_id), str(start_index)],
            cwd=str(ROOT),
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
            close_fds=True,
        )

    with db_connect() as connection:
        connection.execute(
            """
            UPDATE scoring_runs
            SET worker_pid = ?, worker_started_at = ?
            WHERE id = ?
            """,
            (process.pid, int(time.time()), run_id),
        )
        connection.commit()
    return process.pid


def create_scoring_run(
    name,
    prompt,
    model,
    company_count=None,
    reasoning_mode=None,
    stock_list_id=None,
    tickers=None,
    max_tokens=None,
):
    if not os.environ.get("OPENROUTER_KEY"):
        raise RuntimeError("OPENROUTER_KEY is not set")

    name = normalize_run_name(name)
    prompt = normalize_scoring_prompt(prompt)
    model = normalize_model(model)
    reasoning_mode = normalize_reasoning_mode(reasoning_mode)
    max_tokens = normalize_max_tokens(max_tokens)
    selected_list = None
    if stock_list_id is not None:
        if tickers is not None:
            try:
                stock_list_id = int(stock_list_id)
            except (TypeError, ValueError) as exc:
                raise ValueError("Selected stock list was not found.") from exc
        else:
            selected_list = get_stock_list(stock_list_id)
            if not selected_list:
                raise ValueError("Selected stock list was not found.")
            stock_list_id = selected_list["id"]

    if tickers is not None:
        companies = companies_for_tickers(tickers)
    elif selected_list:
        companies = selected_list["companies"]
    else:
        company_count = normalize_company_count(company_count)
        companies = scoring_companies(company_count)
    if (tickers is not None or selected_list) and company_count is not None:
        company_count = normalize_company_count(company_count)
        if company_count > len(companies):
            raise ValueError(f"Company count cannot exceed {len(companies)} for the selected stock list.")
        companies = companies[:company_count]
    if not companies:
        raise RuntimeError("No companies found. Run ./fetch_companies_to_db.py first.")

    now = int(time.time())
    with db_connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO scoring_runs (
                name, prompt, model, reasoning_mode, max_tokens, stock_list_id, status, company_count, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, prompt, model, reasoning_mode, max_tokens, stock_list_id, "queued", len(companies), now),
        )
        run_id = cursor.lastrowid
        snapshot_run_companies(connection, run_id, companies)
        connection.commit()

    start_scoring_worker_process(run_id)
    return run_id


def extend_scoring_run(run_id, company_count):
    target_count = normalize_company_count(company_count)
    with db_connect() as connection:
        run = connection.execute(
            """
            SELECT id, status, company_count, stock_list_id
            FROM scoring_runs
            WHERE id = ? AND deleted_at IS NULL
            """,
            (run_id,),
        ).fetchone()
        if not run:
            return None
        if run["status"] in ("queued", "running", "stop_requested"):
            raise ValueError("Wait for the current run to finish before extending it.")
        if target_count <= run["company_count"]:
            raise ValueError(f"Choose a stock count above {run['company_count']}.")
        extension_companies = extension_companies_for_run(run_id, run["stock_list_id"])
        if target_count > len(extension_companies):
            raise ValueError(
                f"Company count cannot exceed {len(extension_companies)} for this run's stock universe."
            )

        connection.execute(
            """
            UPDATE scoring_runs
            SET company_count = ?, status = ?, started_at = ?, finished_at = NULL, error = NULL
            WHERE id = ?
            """,
            (target_count, "queued", int(time.time()), run_id),
        )
        snapshot_run_companies(connection, run_id, extension_companies[:target_count])
        connection.commit()
        start_index = run["company_count"]

    start_scoring_worker_process(run_id, start_index)
    return get_run(run_id)


def fill_incomplete_scoring_run(run_id):
    with db_connect() as connection:
        run = connection.execute(
            """
            SELECT id, status
            FROM scoring_runs
            WHERE id = ? AND deleted_at IS NULL
            """,
            (run_id,),
        ).fetchone()
        if not run:
            return None
        if run["status"] in ("queued", "running", "stop_requested"):
            raise ValueError("Wait for the current run to finish before filling missing scores.")
        if incomplete_company_count(run_id) <= 0:
            raise ValueError("This run already has a completed score for every selected stock.")

        connection.execute(
            """
            UPDATE scoring_runs
            SET status = ?, started_at = ?, finished_at = NULL, error = NULL
            WHERE id = ?
            """,
            ("queued", int(time.time()), run_id),
        )
        connection.commit()

    start_scoring_worker_process(run_id)
    return get_run(run_id)


def get_run(run_id):
    with db_connect() as connection:
        run = connection.execute(
            """
            SELECT scoring_runs.id, scoring_runs.name, scoring_runs.prompt, scoring_runs.model,
                   scoring_runs.reasoning_mode, scoring_runs.max_tokens, scoring_runs.stock_list_id,
                   scoring_runs.starred,
                   stock_lists.name AS stock_list_name,
                   scoring_runs.status, scoring_runs.company_count, scoring_runs.completed_count,
                   scoring_runs.failed_count, scoring_runs.created_at, scoring_runs.started_at,
                   scoring_runs.finished_at, scoring_runs.error
            FROM scoring_runs
            LEFT JOIN stock_lists ON stock_lists.id = scoring_runs.stock_list_id
            WHERE scoring_runs.id = ? AND scoring_runs.deleted_at IS NULL
            """,
            (run_id,),
        ).fetchone()
        if not run:
            return None
        results = connection.execute(
            """
            SELECT scoring_results.ticker, company_name, scoring_results.rank,
                   scoring_results.market_cap, scoring_results.market_cap_value,
                   scoring_results.price, scoring_results.country,
                   score, raw_response, error, created_at
            FROM scoring_results
            WHERE run_id = ?
            ORDER BY score IS NULL, score DESC, rank ASC
            """,
            (run_id,),
        ).fetchall()
        logo_rows = connection.execute(
            """
            SELECT ticker, logo
            FROM companies
            WHERE ticker IN (SELECT ticker FROM scoring_results WHERE run_id = ?)
            """,
            (run_id,),
        ).fetchall()
        logos = {row["ticker"]: row["logo"] for row in logo_rows}
    result_stats = ai_request_stats_by_ticker(run_id)
    payload = dict(run)
    payload["results"] = []
    for row in results:
        result = dict(row)
        stats = result_stats.get((result["ticker"] or "").upper(), {})
        result["total_tokens"] = stats.get("total_tokens", 0)
        result["prompt_tokens"] = stats.get("prompt_tokens", 0)
        result["completion_tokens"] = stats.get("completion_tokens", 0)
        result["response_tokens"] = stats.get("response_tokens", 0)
        result["reasoning_tokens"] = stats.get("reasoning_tokens", 0)
        result["cost"] = stats.get("cost", 0)
        result["logo"] = logos.get(result["ticker"], "")
        payload["results"].append(result)
    payload["model_details"] = model_details(run["model"], run["reasoning_mode"])
    payload["stats"] = ai_request_stats_for_run(run_id)
    payload["stats"]["recent_average_latency_ms"] = recent_average_latency_ms(run["model"])
    payload["scoring_concurrency"] = scoring_concurrency()
    payload["incomplete_count"] = incomplete_company_count(run_id)
    payload["company_tickers"] = [company["ticker"] for company in scoring_companies_for_run(run_id)]
    payload["extension_limit"] = len(extension_companies_for_run(run_id, run["stock_list_id"]))
    return payload


def ai_request_entries():
    if not AI_REQUEST_LOG_PATH.exists():
        return []
    try:
        entries = json.loads(AI_REQUEST_LOG_PATH.read_text())
        return entries if isinstance(entries, list) else []
    except json.JSONDecodeError:
        return []


def ai_request_cache_key(entry):
    request = entry.get("request") or entry
    provider = request.get("provider_preferences")
    if provider is None:
        provider = request.get("provider")
    signature = {
        "model": request.get("model"),
        "messages": request.get("messages"),
        "temperature": request.get("temperature"),
        "max_tokens": request.get("max_tokens"),
        "reasoning": request.get("reasoning"),
        "provider": provider,
    }
    return json.dumps(signature, sort_keys=True, separators=(",", ":"))


def token_stats_have_counts(token_stats):
    if not isinstance(token_stats, dict):
        return False
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        try:
            if float(token_stats.get(key) or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def zero_token_costs(token_stats):
    copied = copy.deepcopy(token_stats)
    copied["cost"] = 0
    cost_details = copied.get("cost_details")
    if isinstance(cost_details, dict):
        copied["cost_details"] = {key: 0 for key in cost_details}
    copied["cache_reused_token_counts"] = True
    return copied


def effective_ai_request_entries(entries=None):
    entries = ai_request_entries() if entries is None else entries
    prior_token_stats = {}
    effective_entries = []
    for entry in entries:
        cache_key = ai_request_cache_key(entry)
        token_stats = entry.get("token_stats") or {}
        cache = (entry.get("response") or {}).get("cache") or {}
        effective_entry = entry

        if cache.get("status") == "HIT" and not token_stats_have_counts(token_stats):
            prior_stats = prior_token_stats.get(cache_key)
            if prior_stats:
                effective_entry = copy.deepcopy(entry)
                effective_entry["token_stats"] = zero_token_costs(prior_stats)
                effective_cache = (effective_entry.get("response") or {}).setdefault("cache", {})
                effective_cache["token_stats_source"] = "matched_prior_request"

        effective_entries.append(effective_entry)
        effective_stats = effective_entry.get("token_stats") or {}
        if token_stats_have_counts(effective_stats):
            prior_token_stats[cache_key] = effective_stats
    return effective_entries


def find_ai_request_entry(run_id, ticker):
    ticker = ticker.upper()
    matches = [
        entry
        for entry in effective_ai_request_entries()
        if entry.get("run_id") == run_id
        and (entry.get("company", {}).get("ticker") or "").upper() == ticker
    ]
    return matches[-1] if matches else None


def ai_request_costs_by_run():
    costs = {}
    for entry in ai_request_entries():
        run_id = entry.get("run_id")
        if run_id is None:
            continue
        token_stats = entry.get("token_stats") or {}
        try:
            cost = float(token_stats.get("cost") or 0)
        except (TypeError, ValueError):
            cost = 0
        costs[run_id] = costs.get(run_id, 0) + cost
    return costs


def cost_estimate(model, company_count, reasoning_mode=None):
    model = normalize_model(model)
    reasoning_mode = normalize_reasoning_mode(reasoning_mode)
    selected_reasoning = reasoning_config(reasoning_mode)["reasoning"]
    company_count = normalize_company_count(company_count)
    costs = []
    for entry in ai_request_entries():
        request = entry.get("request") or {}
        if request.get("model") != model:
            continue
        if (entry.get("response", {}).get("cache") or {}).get("status") == "HIT":
            continue
        if (request.get("reasoning") or reasoning_config(DEFAULT_REASONING_MODE)["reasoning"]) != selected_reasoning:
            continue
        token_stats = entry.get("token_stats") or {}
        try:
            cost = float(token_stats.get("cost") or 0)
        except (TypeError, ValueError):
            continue
        if cost > 0:
            costs.append(cost)

    sample_size = min(200, len(costs))
    recent_costs = costs[-sample_size:]
    average_cost = sum(recent_costs) / sample_size if sample_size else 0.00005
    return {
        "model": model,
        "reasoning_mode": reasoning_mode,
        "company_count": company_count,
        "estimated_cost": average_cost * company_count,
        "average_request_cost": average_cost,
        "sample_size": sample_size,
        "source": "recent_requests" if sample_size else "fallback",
    }


def recent_average_latency_ms(model, limit=200):
    model = normalize_model(model)
    latencies = []
    for entry in ai_request_entries():
        request = entry.get("request") or {}
        if request.get("model") != model:
            continue
        try:
            duration = int((entry.get("timing") or {}).get("duration_ms") or 0)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            latencies.append(duration)

    sample_size = min(limit, len(latencies))
    recent_latencies = latencies[-sample_size:]
    if not recent_latencies:
        return None
    return round(sum(recent_latencies) / sample_size)


def ai_request_stats_for_run(run_id):
    stats = {
        "cost": 0,
        "total_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "response_tokens": 0,
        "reasoning_tokens": 0,
        "request_count": 0,
        "successful_request_count": 0,
        "failed_request_count": 0,
        "total_latency_ms": 0,
        "average_response_tokens": None,
        "average_latency_ms": None,
    }
    for entry in effective_ai_request_entries():
        if entry.get("run_id") != run_id:
            continue

        stats["request_count"] += 1
        if entry.get("response", {}).get("success"):
            stats["successful_request_count"] += 1
        else:
            stats["failed_request_count"] += 1

        token_stats = entry.get("token_stats") or {}
        completion_details = token_stats.get("completion_tokens_details") or {}
        for key in ("cost", "total_tokens", "prompt_tokens", "completion_tokens"):
            try:
                stats[key] += float(token_stats.get(key) or 0)
            except (TypeError, ValueError):
                pass
        try:
            completion_tokens = float(token_stats.get("completion_tokens") or 0)
            reasoning_tokens = float(completion_details.get("reasoning_tokens") or 0)
            stats["response_tokens"] += max(0, completion_tokens - reasoning_tokens)
        except (TypeError, ValueError):
            pass
        try:
            stats["reasoning_tokens"] += int(completion_details.get("reasoning_tokens") or 0)
        except (TypeError, ValueError):
            pass
        try:
            stats["total_latency_ms"] += int((entry.get("timing") or {}).get("duration_ms") or 0)
        except (TypeError, ValueError):
            pass

    if stats["request_count"]:
        stats["average_response_tokens"] = round(
            stats["response_tokens"] / stats["request_count"], 1
        )
        stats["average_latency_ms"] = round(stats["total_latency_ms"] / stats["request_count"])
    return stats


def ai_request_stats_by_ticker(run_id):
    stats = {}
    for entry in effective_ai_request_entries():
        if entry.get("run_id") != run_id:
            continue
        ticker = (entry.get("company", {}).get("ticker") or "").upper()
        if not ticker:
            continue

        token_stats = entry.get("token_stats") or {}
        completion_details = token_stats.get("completion_tokens_details") or {}
        current = stats.setdefault(
            ticker,
            {
                "cost": 0,
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "response_tokens": 0,
                "reasoning_tokens": 0,
            },
        )
        for key in ("cost", "total_tokens", "prompt_tokens", "completion_tokens"):
            try:
                current[key] += float(token_stats.get(key) or 0)
            except (TypeError, ValueError):
                pass
        try:
            completion_tokens = float(token_stats.get("completion_tokens") or 0)
            reasoning_tokens = float(completion_details.get("reasoning_tokens") or 0)
            current["response_tokens"] += max(0, completion_tokens - reasoning_tokens)
        except (TypeError, ValueError):
            pass
        try:
            current["reasoning_tokens"] += int(completion_details.get("reasoning_tokens") or 0)
        except (TypeError, ValueError):
            pass
    return stats


def get_result_detail(run_id, ticker):
    with db_connect() as connection:
        run = connection.execute(
            """
            SELECT id, name, prompt, model, reasoning_mode, max_tokens, status, created_at, started_at, finished_at, error
            FROM scoring_runs
            WHERE id = ? AND deleted_at IS NULL
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
        row = connection.execute("SELECT status FROM scoring_runs WHERE id = ? AND deleted_at IS NULL", (run_id,)).fetchone()
    return row["status"] if row else None


def stop_scoring_run(run_id):
    now = int(time.time())
    with db_connect() as connection:
        row = connection.execute("SELECT status FROM scoring_runs WHERE id = ? AND deleted_at IS NULL", (run_id,)).fetchone()
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
            SELECT id, name, prompt, model, reasoning_mode, max_tokens, status, company_count, completed_count, failed_count,
                   created_at, started_at, finished_at, error
            FROM scoring_runs
            WHERE id = ? AND deleted_at IS NULL
            """,
            (run_id,),
        ).fetchone()
    return dict(updated)


def list_runs():
    with db_connect() as connection:
        rows = connection.execute(
            """
            SELECT id, name, prompt, model, reasoning_mode, max_tokens, starred, status, company_count, completed_count, failed_count,
                   created_at, started_at, finished_at, error
            FROM scoring_runs
            WHERE deleted_at IS NULL
            ORDER BY created_at DESC, id DESC
            """
        ).fetchall()
    costs = ai_request_costs_by_run()
    runs = []
    for row in rows:
        run = dict(row)
        run["cost"] = costs.get(run["id"], 0)
        runs.append(run)
    return runs


def rename_scoring_run(run_id, name):
    return update_scoring_run(run_id, name=name)


def update_scoring_run(run_id, name=None, prompt=None, starred=None):
    updates = []
    values = []
    if name is not None:
        updates.append("name = ?")
        values.append(normalize_run_name(name))
    if prompt is not None:
        updates.append("prompt = ?")
        values.append(normalize_scoring_prompt(prompt))
    if starred is not None:
        if not isinstance(starred, bool):
            raise ValueError("Starred must be true or false.")
        updates.append("starred = ?")
        values.append(1 if starred else 0)
    if not updates:
        return get_run(run_id)

    with db_connect() as connection:
        row = connection.execute("SELECT id FROM scoring_runs WHERE id = ? AND deleted_at IS NULL", (run_id,)).fetchone()
        if not row:
            return None
        values.append(run_id)
        connection.execute(
            f"UPDATE scoring_runs SET {', '.join(updates)} WHERE id = ?",
            values,
        )
        connection.commit()
    return get_run(run_id)


def delete_scoring_run(run_id):
    now = int(time.time())
    with db_connect() as connection:
        row = connection.execute("SELECT id FROM scoring_runs WHERE id = ? AND deleted_at IS NULL", (run_id,)).fetchone()
        if not row:
            return False
        connection.execute(
            """
            UPDATE scoring_runs
            SET deleted_at = ?,
                status = CASE
                    WHEN status IN ('queued', 'running', 'stop_requested') THEN 'stopped'
                    ELSE status
                END,
                finished_at = CASE
                    WHEN finished_at IS NULL THEN ?
                    ELSE finished_at
                END,
                error = CASE
                    WHEN status IN ('queued', 'running', 'stop_requested') THEN 'Archived by user.'
                    ELSE error
                END
            WHERE id = ?
            """,
            (now, now, run_id),
        )
        connection.commit()
    return True


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


def invalidate_truncated_scoring_results():
    latest_entries = {}
    for entry in ai_request_entries():
        company = entry.get("company") or {}
        ticker = (company.get("ticker") or "").upper()
        run_id = entry.get("run_id")
        if run_id is not None and ticker:
            latest_entries[(run_id, ticker)] = entry

    truncated = [
        (run_id, ticker)
        for (run_id, ticker), entry in latest_entries.items()
        if (entry.get("response") or {}).get("finish_reason") == "length"
    ]
    if not truncated:
        return 0

    affected_runs = set()
    invalidated = 0
    with db_connect() as connection:
        for run_id, ticker in truncated:
            cursor = connection.execute(
                """
                UPDATE scoring_results
                SET score = NULL, error = ?
                WHERE run_id = ? AND UPPER(ticker) = ? AND error IS NULL
                """,
                (TOKEN_LIMIT_ERROR, run_id, ticker),
            )
            if cursor.rowcount:
                invalidated += cursor.rowcount
                affected_runs.add(run_id)
        for run_id in affected_runs:
            update_run_counts(connection, run_id)
        connection.commit()
    return invalidated


def result_tickers_for_run(run_id):
    with db_connect() as connection:
        rows = connection.execute(
            "SELECT ticker FROM scoring_results WHERE run_id = ?",
            (run_id,),
        ).fetchall()
    return {row["ticker"] for row in rows}


def completed_score_tickers_for_run(run_id):
    with db_connect() as connection:
        rows = connection.execute(
            """
            SELECT ticker
            FROM scoring_results
            WHERE run_id = ?
              AND error IS NULL
              AND score IS NOT NULL
            """,
            (run_id,),
        ).fetchall()
    return {row["ticker"] for row in rows}


def incomplete_company_count(run_id):
    with db_connect() as connection:
        run = connection.execute(
            "SELECT company_count FROM scoring_runs WHERE id = ? AND deleted_at IS NULL",
            (run_id,),
        ).fetchone()
    if not run:
        return 0
    complete_tickers = completed_score_tickers_for_run(run_id)
    return sum(
        1
        for company in scoring_companies_for_run(run_id)
        if company["ticker"] not in complete_tickers
    )


def mark_interrupted_runs():
    now = int(time.time())
    with db_connect() as connection:
        rows = connection.execute(
            """
            SELECT id, status, worker_pid
            FROM scoring_runs
            WHERE deleted_at IS NULL
              AND status IN ('queued', 'running', 'stop_requested')
            """,
        ).fetchall()
        for row in rows:
            if process_is_running(row["worker_pid"]):
                continue
            final_error = (
                "Stopped by user."
                if row["status"] == "stop_requested"
                else "Interrupted because the scoring worker is no longer running."
            )
            connection.execute(
                """
                UPDATE scoring_runs
                SET status = ?,
                    finished_at = COALESCE(finished_at, ?),
                    error = COALESCE(error, ?),
                    worker_pid = NULL,
                    worker_started_at = NULL
                WHERE id = ?
                """,
                ("stopped", now, final_error, row["id"]),
            )
        connection.commit()


def parse_numeric_score(text):
    matches = re.findall(r"-?\d+(?:\.\d+)?", text or "")
    if not matches:
        raise ValueError("Model response did not contain a number")
    return float(matches[-1])


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
    company_reference = f"{company['name']} (ticker: {company['ticker']})"
    return prompt.strip().replace("COMPANY", company_reference)


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


def openrouter_payload_error_details(payload, http_status=None):
    if not isinstance(payload, dict):
        return {
            "message": "OpenRouter returned an invalid response payload.",
            "status": http_status,
            "body": payload,
            "retryable": True,
        }

    error = payload.get("error")
    if error:
        if isinstance(error, dict):
            code = error.get("code")
            detail = error.get("message") or json.dumps(error)
        else:
            code = None
            detail = str(error)
        status = code or http_status
        prefix = f"OpenRouter API {status}" if status else "OpenRouter API error"
        return {
            "message": f"{prefix}: {detail}",
            "status": status,
            "body": payload,
            "retryable": status in (408, 409, 429, 500, 502, 503, 504),
        }

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return {
            "message": "OpenRouter returned no completion choices.",
            "status": http_status,
            "body": payload,
            "retryable": True,
        }
    return None


def ai_log_entry(
    run_id,
    company,
    request_payload,
    started_at,
    response_payload=None,
    error=None,
    http_status=None,
    cache_metadata=None,
):
    choice = None
    message = {}
    if response_payload and response_payload.get("choices"):
        choice = response_payload["choices"][0]
        message = choice.get("message", {}) or {}

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
            "provider_preferences": request_payload.get("provider"),
            "response_cache": {
                "enabled": True,
                "ttl_seconds": openrouter_cache_ttl_seconds(),
            },
            "prompt_sent": request_payload["messages"][0]["content"],
        },
        "response": {
            "success": error is None,
            "http_status": http_status,
            "id": response_payload.get("id") if response_payload else None,
            "created": response_payload.get("created") if response_payload else None,
            "model": response_payload.get("model") if response_payload else None,
            "provider": response_payload.get("provider") if response_payload else None,
            "visible_content": message.get("content") if choice else None,
            "reasoning": message.get("reasoning"),
            "reasoning_content": message.get("reasoning_content"),
            "reasoning_details": message.get("reasoning_details"),
            "finish_reason": choice.get("finish_reason") if choice else None,
            "raw_payload": sanitized_ai_payload(response_payload),
            "cache": cache_metadata,
            "error": error,
        },
        "token_stats": response_payload.get("usage") if response_payload else None,
        "timing": {
            "duration_ms": round((time.time() - started_at) * 1000),
        },
        "chain_of_thought": (
            message.get("reasoning")
            or message.get("reasoning_content")
            or message.get("reasoning_details")
        ),
        "chain_of_thought_note": (
            "Reasoning text was returned by the provider."
            if (
                message.get("reasoning")
                or message.get("reasoning_content")
                or message.get("reasoning_details")
            )
            else "Hidden chain-of-thought was not exposed by the model/API for this request."
        ),
    }


def reasoning_token_count(response_payload):
    usage = response_payload.get("usage") if response_payload else None
    completion_details = usage.get("completion_tokens_details") if isinstance(usage, dict) else None
    if not isinstance(completion_details, dict):
        return 0
    try:
        return int(completion_details.get("reasoning_tokens") or 0)
    except (TypeError, ValueError):
        return 0


def response_provider(response_payload):
    if not response_payload:
        return None
    provider = response_payload.get("provider")
    if provider:
        return provider
    return None


def maybe_block_reasoning_provider(run_id, company, request_payload, response_payload):
    reasoning = request_payload.get("reasoning") or {}
    if reasoning.get("effort") != "none":
        return
    tokens = reasoning_token_count(response_payload)
    if tokens <= 0:
        return
    block_reasoning_provider(
        response_provider(response_payload),
        run_id=run_id,
        ticker=company.get("ticker"),
        reasoning_tokens=tokens,
    )


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
    if not sanitized.get("chain_of_thought"):
        sanitized["chain_of_thought"] = (
            response.get("reasoning")
            or response.get("reasoning_content")
            or response.get("reasoning_details")
            if isinstance(response, dict)
            else None
        )
    if sanitized.get("chain_of_thought"):
        sanitized["chain_of_thought_note"] = "Reasoning text was returned by the provider."
    else:
        sanitized["chain_of_thought_note"] = "Hidden chain-of-thought was not exposed by the model/API for this request."
    return sanitized


def call_openrouter(prompt, company, model, reasoning_mode=None, run_id=None, max_tokens=None):
    api_key = os.environ.get("OPENROUTER_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_KEY is not set")

    config = model_config(model)
    reasoning = reasoning_config(reasoning_mode)["reasoning"]
    request_payload = {
        "model": config["id"],
        "messages": [
            {"role": "user", "content": prompt_for_company(prompt, company)},
        ],
        "temperature": 0,
        "max_tokens": normalize_max_tokens(max_tokens),
        "reasoning": reasoning,
        "provider": provider_preferences(config),
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
            "X-OpenRouter-Cache": "true",
            "X-OpenRouter-Cache-TTL": str(openrouter_cache_ttl_seconds()),
        },
        method="POST",
    )
    max_attempts = openrouter_max_attempts()
    for attempt in range(1, max_attempts + 1):
        started_at = time.time()
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                http_status = response.status
                response_body = read_http_response_with_deadline(
                    response,
                    openrouter_attempt_timeout_seconds(),
                )
                payload = json.loads(response_body.decode("utf-8"))
                response_headers = getattr(response, "headers", None)
                header_value = response_headers.get if response_headers is not None else lambda _name: None
                cache_metadata = {
                    "status": header_value("X-OpenRouter-Cache-Status"),
                    "age_seconds": header_value("X-OpenRouter-Cache-Age"),
                    "ttl_seconds": header_value("X-OpenRouter-Cache-TTL"),
                }
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

        payload_error = openrouter_payload_error_details(payload, http_status)
        if not payload_error:
            break
        payload_error["attempt"] = attempt
        payload_error["max_attempts"] = max_attempts
        append_ai_request_log(
            ai_log_entry(
                run_id,
                company,
                request_payload,
                started_at,
                response_payload=payload,
                error=payload_error,
                http_status=http_status,
                cache_metadata=cache_metadata,
            )
        )
        if payload_error["retryable"] and attempt < max_attempts:
            time.sleep(attempt)
            continue
        raise RuntimeError(payload_error["message"])

    maybe_block_reasoning_provider(run_id, company, request_payload, payload)

    try:
        choice = payload["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ValueError(TOKEN_LIMIT_ERROR)
        message = choice.get("message", {})
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
                cache_metadata=cache_metadata,
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
            cache_metadata=cache_metadata,
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


def score_company_request(run_id, prompt, model, reasoning_mode, company, max_tokens):
    raw_response = None
    score = None
    error = None
    try:
        raw_response = call_openrouter(
            prompt,
            company,
            model,
            reasoning_mode,
            run_id=run_id,
            max_tokens=max_tokens,
        )
        score = parse_numeric_score(raw_response)
    except FatalScoringError:
        raise
    except Exception as exc:
        error = str(exc)
    return company, score, raw_response, error


def save_scoring_result(run_id, company, score, raw_response, error):
    with db_connect() as connection:
        if run_status(run_id) is None:
            return False
        if run_status(run_id) == "stop_requested":
            return False
        save_result(connection, run_id, company, score, raw_response, error)
        update_run_counts(connection, run_id)
        connection.commit()
    return True


def score_run_worker(run_id, start_index=0):
    ensure_scoring_schema()
    with db_connect() as connection:
        run = connection.execute(
            "SELECT name, prompt, model, reasoning_mode, max_tokens, company_count FROM scoring_runs WHERE id = ? AND deleted_at IS NULL",
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
        companies = scoring_companies_for_run(run_id)
        if start_index:
            companies = companies[start_index:]
        completed_tickers = completed_score_tickers_for_run(run_id)
        companies = [company for company in companies if company["ticker"] not in completed_tickers]
        if not companies:
            with db_connect() as connection:
                connection.execute(
                    """
                    UPDATE scoring_runs
                    SET status = ?, finished_at = ?, error = ?,
                        worker_pid = NULL, worker_started_at = NULL
                    WHERE id = ?
                    """,
                    ("completed", int(time.time()), None, run_id),
                )
                update_run_counts(connection, run_id)
                connection.commit()
            return
        company_iter = iter(companies)
        futures = {}
        max_workers = min(scoring_concurrency(), len(companies)) or 1

        def submit_next(executor):
            try:
                company = next(company_iter)
            except StopIteration:
                return False
            current_status = run_status(run_id)
            if current_status is None:
                return False
            if current_status == "stop_requested":
                return False
            future = executor.submit(
                score_company_request,
                run_id,
                run["prompt"],
                run["model"],
                run["reasoning_mode"],
                company,
                run["max_tokens"],
            )
            futures[future] = company
            return True

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            for _ in range(max_workers):
                if not submit_next(executor):
                    break

            while futures:
                current_status = run_status(run_id)
                if current_status is None:
                    stopped = True
                    break
                if current_status == "stop_requested":
                    stopped = True
                    break

                done, _pending = concurrent.futures.wait(
                    futures,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for future in done:
                    futures.pop(future, None)
                    try:
                        company, score, raw_response, error = future.result()
                    except FatalScoringError as exc:
                        fatal_error = str(exc)
                        stopped = False
                        break

                    if not save_scoring_result(run_id, company, score, raw_response, error):
                        stopped = True
                        break

                    if not submit_next(executor):
                        continue

                if fatal_error or stopped:
                    break

            for future in futures:
                future.cancel()
    except Exception as exc:
        fatal_error = str(exc)

    with db_connect() as connection:
        current_status = run_status(run_id)
        if current_status is None:
            return
        if current_status == "stop_requested":
            stopped = True
        status = "stopped" if stopped else "failed" if fatal_error else "completed"
        final_error = "Stopped by user." if stopped else fatal_error
        connection.execute(
            """
            UPDATE scoring_runs
            SET status = ?, finished_at = ?, error = ?,
                worker_pid = NULL, worker_started_at = NULL
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

        if parsed.path == "/api/models":
            self.send_json(
                {
                    "models": openrouter_model_options(),
                    "default": openrouter_model(),
                    "reasoning_modes": reasoning_options(),
                    "default_reasoning_mode": default_reasoning_mode(),
                    "default_max_tokens": openrouter_max_tokens(),
                }
            )
            return

        if parsed.path == "/api/stock-lists":
            ensure_scoring_schema()
            self.send_json({"lists": list_stock_lists()})
            return

        if parsed.path == "/api/cost-estimate":
            query = dict(parse_qsl(parsed.query))
            try:
                estimate = cost_estimate(
                    query.get("model"),
                    query.get("companyCount"),
                    query.get("reasoningMode"),
                )
                self.send_json({"estimate": estimate})
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
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
        if parsed.path == "/api/stock-lists":
            ensure_scoring_schema()
            try:
                payload = self.read_json()
                stock_list = save_stock_list(payload.get("name"), payload.get("tickers"))
                self.send_json({"list": stock_list}, 201)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return

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
                prompt = normalize_scoring_prompt(payload.get("prompt"))
                model = normalize_model(payload.get("model"))
                reasoning_mode = normalize_reasoning_mode(payload.get("reasoningMode"))
                run_id = create_scoring_run(
                    name,
                    prompt,
                    model,
                    payload.get("companyCount"),
                    reasoning_mode,
                    stock_list_id=payload.get("stockListId"),
                    tickers=payload.get("tickers") if "tickers" in payload else None,
                    max_tokens=payload.get("maxTokens"),
                )
                self.send_json({"runId": run_id, "url": f"/run.html?id={run_id}"}, 201)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return

        extend_match = re.fullmatch(r"/api/runs/(\d+)/extend", parsed.path)
        if extend_match:
            ensure_scoring_schema()
            try:
                payload = self.read_json()
                run = extend_scoring_run(int(extend_match.group(1)), payload.get("companyCount"))
                if not run:
                    self.send_json({"error": "Run not found"}, 404)
                    return
                self.send_json({"run": run})
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return

        fill_match = re.fullmatch(r"/api/runs/(\d+)/fill", parsed.path)
        if fill_match:
            ensure_scoring_schema()
            try:
                run = fill_incomplete_scoring_run(int(fill_match.group(1)))
                if not run:
                    self.send_json({"error": "Run not found"}, 404)
                    return
                self.send_json({"run": run})
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return
        self.send_json({"error": "Not found"}, 404)

    def do_PATCH(self):
        parsed = urlparse(self.path)
        stock_list_match = re.fullmatch(r"/api/stock-lists/(\d+)", parsed.path)
        if stock_list_match:
            ensure_scoring_schema()
            try:
                payload = self.read_json()
                stock_list = save_stock_list(
                    payload.get("name"),
                    payload.get("tickers"),
                    int(stock_list_match.group(1)),
                )
                if not stock_list:
                    self.send_json({"error": "Stock list not found"}, 404)
                    return
                self.send_json({"list": stock_list})
            except ValueError as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return

        run_match = re.fullmatch(r"/api/runs/(\d+)", parsed.path)
        if run_match:
            ensure_scoring_schema()
            try:
                payload = self.read_json()
                run = update_scoring_run(
                    int(run_match.group(1)),
                    name=payload.get("name") if "name" in payload else None,
                    prompt=payload.get("prompt") if "prompt" in payload else None,
                    starred=payload.get("starred") if "starred" in payload else None,
                )
                if not run:
                    self.send_json({"error": "Run not found"}, 404)
                    return
                self.send_json({"run": run})
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        self.send_json({"error": "Not found"}, 404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        stock_list_match = re.fullmatch(r"/api/stock-lists/(\d+)", parsed.path)
        if stock_list_match:
            ensure_scoring_schema()
            archived = archive_stock_list(int(stock_list_match.group(1)))
            if not archived:
                self.send_json({"error": "Stock list not found"}, 404)
                return
            self.send_json({"archived": True})
            return

        run_match = re.fullmatch(r"/api/runs/(\d+)", parsed.path)
        if run_match:
            ensure_scoring_schema()
            archived = delete_scoring_run(int(run_match.group(1)))
            if not archived:
                self.send_json({"error": "Run not found"}, 404)
                return
            self.send_json({"archived": True})
            return
        self.send_json({"error": "Not found"}, 404)


def main():
    ensure_scoring_schema()
    invalidated = invalidate_truncated_scoring_results()
    if invalidated:
        print(f"Marked {invalidated} token-limited scoring responses invalid.")
    mark_interrupted_runs()
    port = 3001
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Serving AI Stock Scorer at http://localhost:{port}")
    print("Edit server.py, index.html, styles.css, or app.js and the browser will auto-refresh.")
    server.serve_forever()


if __name__ == "__main__":
    main()
