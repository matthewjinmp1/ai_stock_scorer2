#!/usr/bin/env python3
import concurrent.futures
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
from urllib.parse import parse_qsl, unquote, urlparse


ROOT = Path(__file__).resolve().parent
SOURCE_URL = "https://companiesmarketcap.com/"
COMPANY_UNIVERSE_LIMIT = 1000
COMPANIESMARKETCAP_PAGE_SIZE = 100
CACHE_SECONDS = 60 * 15
DB_PATH = ROOT / "companies.db"
AI_REQUEST_LOG_PATH = ROOT / "ai_requests.json"
PROVIDER_BLOCKLIST_PATH = ROOT / "provider_blocklist.json"
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
        "id": "deepseek/deepseek-v4-flash",
        "label": "DeepSeek V4 Flash (non-reasoning)",
        "reasoning": {"effort": "none", "exclude": False},
        "provider": {
            "require_parameters": True,
        },
    }
]
DEFAULT_OPENROUTER_MODEL = MODEL_OPTIONS[0]["id"]
DEFAULT_SCORING_COMPANY_COUNT = 10
DEFAULT_SCORING_CONCURRENCY = 20
OPENROUTER_MAX_TOKENS = int(os.environ.get("OPENROUTER_MAX_TOKENS", "200"))

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


def normalize_model(model):
    requested = (model or DEFAULT_OPENROUTER_MODEL).strip()
    allowed = {option["id"] for option in MODEL_OPTIONS}
    if requested not in allowed:
        raise ValueError("Selected model is not available.")
    return requested


def model_config(model):
    normalized = normalize_model(model)
    return next(option for option in MODEL_OPTIONS if option["id"] == normalized)


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


def model_details(model):
    config = model_config(model)
    return {
        "id": config["id"],
        "label": config["label"],
        "reasoning": config["reasoning"],
        "provider": provider_preferences(config),
    }


def openrouter_max_tokens():
    return int(os.environ.get("OPENROUTER_MAX_TOKENS", str(OPENROUTER_MAX_TOKENS)))


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
                status TEXT NOT NULL,
                company_count INTEGER NOT NULL DEFAULT 0,
                completed_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                started_at INTEGER,
                finished_at INTEGER,
                deleted_at INTEGER,
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
        if "deleted_at" not in columns:
            connection.execute("ALTER TABLE scoring_runs ADD COLUMN deleted_at INTEGER")
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


def normalize_scoring_prompt(value):
    prompt = (value or "").strip()
    if not prompt:
        raise ValueError("Prompt is required")
    if not prompt_has_company_keyword(prompt):
        raise ValueError("Prompt must include the COMPANY keyword.")
    return prompt


def create_scoring_run(name, prompt, model, company_count):
    if not os.environ.get("OPENROUTER_KEY"):
        raise RuntimeError("OPENROUTER_KEY is not set")

    name = normalize_run_name(name)
    prompt = normalize_scoring_prompt(prompt)
    model = normalize_model(model)
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


def extend_scoring_run(run_id, company_count):
    target_count = normalize_company_count(company_count)
    with db_connect() as connection:
        run = connection.execute(
            """
            SELECT id, status, company_count
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

        connection.execute(
            """
            UPDATE scoring_runs
            SET company_count = ?, status = ?, started_at = ?, finished_at = NULL, error = NULL
            WHERE id = ?
            """,
            (target_count, "queued", int(time.time()), run_id),
        )
        connection.commit()
        start_index = run["company_count"]

    thread = threading.Thread(target=score_run_worker, args=(run_id, start_index), daemon=True)
    thread.start()
    return get_run(run_id)


def get_run(run_id):
    with db_connect() as connection:
        run = connection.execute(
            """
            SELECT id, name, prompt, model, status, company_count, completed_count, failed_count,
                   created_at, started_at, finished_at, error
            FROM scoring_runs
            WHERE id = ? AND deleted_at IS NULL
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
        payload["results"].append(result)
    payload["model_details"] = model_details(run["model"])
    payload["stats"] = ai_request_stats_for_run(run_id)
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


def cost_estimate(model, company_count):
    model = normalize_model(model)
    company_count = normalize_company_count(company_count)
    costs = []
    for entry in ai_request_entries():
        request = entry.get("request") or {}
        if request.get("model") != model:
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
        "company_count": company_count,
        "estimated_cost": average_cost * company_count,
        "average_request_cost": average_cost,
        "sample_size": sample_size,
        "source": "recent_requests" if sample_size else "fallback",
    }


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
        "average_latency_ms": None,
    }
    for entry in ai_request_entries():
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
        stats["average_latency_ms"] = round(stats["total_latency_ms"] / stats["request_count"])
    return stats


def ai_request_stats_by_ticker(run_id):
    stats = {}
    for entry in ai_request_entries():
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
            SELECT id, name, prompt, model, status, created_at, started_at, finished_at, error
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
            SELECT id, name, prompt, model, status, company_count, completed_count, failed_count,
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
            SELECT id, name, prompt, model, status, company_count, completed_count, failed_count,
                   created_at, started_at, finished_at, error
            FROM scoring_runs
            WHERE deleted_at IS NULL
            ORDER BY created_at DESC, id DESC
            LIMIT 100
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


def update_scoring_run(run_id, name=None, prompt=None):
    updates = []
    values = []
    if name is not None:
        updates.append("name = ?")
        values.append(normalize_run_name(name))
    if prompt is not None:
        updates.append("prompt = ?")
        values.append(normalize_scoring_prompt(prompt))
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


def result_tickers_for_run(run_id):
    with db_connect() as connection:
        rows = connection.execute(
            "SELECT ticker FROM scoring_results WHERE run_id = ?",
            (run_id,),
        ).fetchall()
    return {row["ticker"] for row in rows}


def mark_interrupted_runs():
    now = int(time.time())
    with db_connect() as connection:
        connection.execute(
            """
            UPDATE scoring_runs
            SET status = ?,
                finished_at = COALESCE(finished_at, ?),
                error = COALESCE(error, ?)
            WHERE deleted_at IS NULL
              AND status IN ('queued', 'running', 'stop_requested')
            """,
            ("stopped", now, "Interrupted by server restart before all companies finished."),
        )
        connection.commit()


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


def call_openrouter(prompt, company, model, run_id=None):
    api_key = os.environ.get("OPENROUTER_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_KEY is not set")

    config = model_config(model)
    request_payload = {
        "model": config["id"],
        "messages": [
            {"role": "user", "content": prompt_for_company(prompt, company)},
        ],
        "temperature": 0,
        "max_tokens": openrouter_max_tokens(),
        "reasoning": config["reasoning"],
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

    maybe_block_reasoning_provider(run_id, company, request_payload, payload)

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


def score_company_request(run_id, prompt, model, company):
    raw_response = None
    score = None
    error = None
    try:
        raw_response = call_openrouter(prompt, company, model, run_id=run_id)
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
            "SELECT name, prompt, model, company_count FROM scoring_runs WHERE id = ? AND deleted_at IS NULL",
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
        if start_index:
            companies = companies[start_index:]
        saved_tickers = result_tickers_for_run(run_id)
        companies = [company for company in companies if company["ticker"] not in saved_tickers]
        if not companies:
            with db_connect() as connection:
                connection.execute(
                    """
                    UPDATE scoring_runs
                    SET status = ?, finished_at = ?, error = ?
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
                company,
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

        if parsed.path == "/api/models":
            self.send_json({"models": openrouter_model_options(), "default": openrouter_model()})
            return

        if parsed.path == "/api/cost-estimate":
            query = dict(parse_qsl(parsed.query))
            try:
                estimate = cost_estimate(query.get("model"), query.get("companyCount"))
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
                company_count = normalize_company_count(payload.get("companyCount"))
                run_id = create_scoring_run(name, prompt, model, company_count)
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
        self.send_json({"error": "Not found"}, 404)

    def do_PATCH(self):
        parsed = urlparse(self.path)
        run_match = re.fullmatch(r"/api/runs/(\d+)", parsed.path)
        if run_match:
            ensure_scoring_schema()
            try:
                payload = self.read_json()
                run = update_scoring_run(
                    int(run_match.group(1)),
                    name=payload.get("name") if "name" in payload else None,
                    prompt=payload.get("prompt") if "prompt" in payload else None,
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
    mark_interrupted_runs()
    port = 3001
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Serving AI Stock Scorer at http://localhost:{port}")
    print("Edit server.py, index.html, styles.css, or app.js and the browser will auto-refresh.")
    server.serve_forever()


if __name__ == "__main__":
    main()
